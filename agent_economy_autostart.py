"""agent_economy_autostart.py — 三件套+gateway 开机自启 & VPS 隧道自愈。

设计:复用 l4_remote_relay.py 的成熟模式(隐藏窗口、指数退避、双判定探测)。
  - 四服务(identity/authz/meter/gateway)由本脚本拉起,并做存活守护(崩溃自拉起)。
  - VPS 反向隧道(18901)自愈:与 l4_remote_relay 同机制 — 纯出向 SSH 到 VPS:49108,
    与系统 VPN 出口独立,VPN 切换只会让它断线重连,不影响本机网络环境。

用法:
    python agent_economy_autostart.py            # 前台跑(守护循环)
    python agent_economy_autostart.py --install  # 注册为开机自启(任务计划,登录时触发)
    python agent_economy_autostart.py --daemon   # 后台分离跑
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

VPS_HOST = "192.220.14.165"
VPS_PORT = 49108
VPS_USER = "root"

PY = sys.executable
# 无控制台 python(分离守护用):python.exe 在 DETACHED 下起子进程会因控制台
# 句柄损坏即退;pythonw.exe 无控制台,派生子服务稳定(实测确认)。
PYW = str(Path(PY).with_name("pythonw.exe"))
PKG = Path(__file__).resolve().parent.parent  # oi_enhancements/

# 四服务:模块名 -> (脚本绝对路径, 端口, 健康探测)
# 用绝对路径而非 -m 模块:DETACHED 子进程的 cwd/PYTHONPATH 继承不可靠,
# -m 会 ModuleNotFoundError(实测确认);直接传 .py 路径最稳。
_SVC_DIR = Path(__file__).resolve().parent / "agent_economy"
SERVICES: dict[str, tuple[Path, int, str]] = {
    "identity": (_SVC_DIR / "agent_identity.py", 18901, "identity/health"),
    "authz":    (_SVC_DIR / "agent_authz.py", 18902, "authz/health"),
    "meter":    (_SVC_DIR / "agent_meter.py", 18903, "meter/health"),
    "gateway":  (_SVC_DIR / "agent_gateway.py", 18910, "gateway/health"),
}

LOG_DIR = Path.home() / ".local" / "share" / "aureon" / "log"
LOG_FILE = LOG_DIR / "agent_economy_autostart.log"
TASK_NAME = "AgentEconomyAutostart"


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _hide_kwargs(detached: bool = False) -> dict:
    """隐藏子进程窗口。detached=True 时用 DETACHED_PROCESS 让子服务脱离守护的
    控制台(守护以 --daemon 分离跑时无控制台,CREATE_NO_WINDOW 单独用会让
    子服务初始化控制台失败即退 — 这是实测确认的坑)。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detached:
        # DETACHED_PROCESS(0x8) | CREATE_NEW_PROCESS_GROUP(0x200):
        # 缺 0x200 时从分离守护派生的子服务即退(实测确认)。
        flags |= 0x00000008 | 0x00000200
    return {"startupinfo": si, "creationflags": flags}


# ── 服务守护 ────────────────────────────────────────────────────────
def _health_ok(port: int, probe: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/{probe}", timeout=4) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _start_service(script: Path) -> subprocess.Popen:
    # 直接传 .py 绝对路径(不依赖 -m 模块解析/cwd/PYTHONPATH);
    # 服务文件内已有 sys.path.insert(parent) 处理包导入。
    short = script.stem
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"svc_{short}.log", "ab")
    try:
        return subprocess.Popen(
            [PYW, str(script)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **_hide_kwargs(detached=True))
    except Exception as e:  # noqa: BLE001
        _log(f"!! 拉起 {script.name} 抛异常: {type(e).__name__}: {e}")
        raise


# ── VPS 隧道自愈 ────────────────────────────────────────────────────
def _start_tunnel() -> subprocess.Popen:
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-N", "-R", "18901:127.0.0.1:18901",
            "-p", str(VPS_PORT), f"{VPS_USER}@{VPS_HOST}"]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **_hide_kwargs(detached=True))


def _vps_identity_ok() -> bool:
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-p", str(VPS_PORT), f"{VPS_USER}@{VPS_HOST}",
             "curl -s -o /dev/null -w %{http_code} --max-time 5 "
             "http://127.0.0.1:18901/identity/health"],
            capture_output=True, text=True, timeout=15, **_hide_kwargs())
        return "200" in r.stdout
    except Exception:  # noqa: BLE001
        return False


# ── 主守护循环 ──────────────────────────────────────────────────────
_LOCK_FILE = LOG_DIR / "agent_economy_autostart.lock"


def _pid_alive(pid: int) -> bool:
    """用 PowerShell 精确判断 PID 是否为活着的 autostart 守护。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
            capture_output=True, text=True, timeout=10, **_hide_kwargs())
        return "True" in r.stdout
    except Exception:  # noqa: BLE001
        return False


def _acquire_singleton() -> bool:
    """单实例锁:PID 文件 + 进程存活检测。已活实例则拒绝。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if _LOCK_FILE.exists():
        try:
            old_pid = int(_LOCK_FILE.read_text().strip())
            if old_pid != os.getpid() and _pid_alive(old_pid):
                return False  # 已有活实例
        except Exception:  # noqa: BLE001
            pass  # 锁损坏/进程已死,可接管
    _LOCK_FILE.write_text(str(os.getpid()))
    return True


def run_forever() -> None:
    if not _acquire_singleton():
        print("已有守护实例在跑,退出(单实例锁)", flush=True)
        return
    _log("agent_economy 自启守护启动(四服务 + VPS 隧道自愈)")
    procs: dict[str, subprocess.Popen] = {}
    tunnel: subprocess.Popen | None = None
    backoff = 5
    while True:
        # 服务守护:崩溃/未起则拉起
        for name, (script, port, probe) in SERVICES.items():
            p = procs.get(name)
            if p is None or p.poll() is not None:
                if not _health_ok(port, probe):  # 端口未被别的实例占
                    _log(f"拉起服务 {name}(:{port})")
                    procs[name] = _start_service(script)
                else:
                    procs[name] = procs.get(name) or p  # 已有健康实例在跑
        # 隧道自愈:断线或 VPS 侧不可达则重建
        if tunnel is None or tunnel.poll() is not None or not _vps_identity_ok():
            if tunnel is not None and tunnel.poll() is None:
                _log("VPS 侧 identity 不可达,重建隧道")
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    tunnel.kill()
            if tunnel is None or tunnel.poll() is not None:
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
                tunnel = _start_tunnel()
                _log(f"VPS 隧道已建立(pid={tunnel.pid})")
                backoff = 5
        time.sleep(30)


# ── 开机自启注册(任务计划,登录触发)──────────────────────────────────
def install() -> None:
    script = os.path.abspath(__file__)
    # pythonw 无窗跑,登录时触发。守护脚本内部再拉起四服务+隧道。
    cmd = (f'schtasks /Create /TN "{TASK_NAME}" /F '
           f'/TR "\\"{PYW}\\" \\"{script}\\"" '
           f'/SC ONLOGON /RL LIMITED')
    _log(f"注册开机自启: {cmd}")
    # 字节读输出,避免 schtasks 的 GBK 输出触发 UnicodeDecodeError
    r = subprocess.run(cmd, shell=True, capture_output=True)
    out = (r.stdout or r.stderr).decode("gbk", errors="replace")
    print(out)
    if r.returncode == 0:
        print(f"已注册任务计划 '{TASK_NAME}'(登录时自启)。手动触发: schtasks /Run /TN {TASK_NAME}")
    else:
        print("注册失败,可手动以管理员重试,或改用任务计划程序 GUI。")


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    elif "--daemon" in sys.argv:
        # pythonw 分离守护;重定向自身输出到日志(不用 DEVNULL/close_fds,
        # 否则守护再派生子服务时句柄无效、子进程即退 — 实测确认)。
        DETACHED = 0x00000008 | 0x00000200
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        dlog = open(LOG_DIR / "daemon.out.log", "ab")
        subprocess.Popen([PYW, os.path.abspath(__file__)],
                         creationflags=DETACHED,
                         stdout=dlog, stderr=subprocess.STDOUT)
        print("agent_economy 自启守护已后台启动")
    else:
        run_forever()
