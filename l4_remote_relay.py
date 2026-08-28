"""l4_remote_relay.py — L4 远程接入中继(自愈)

Agent-First OS 阶段 2 远程接入(两步走的第一步)。L4 绑 127.0.0.1,不直接暴露公网;
经 VPS(192.220.14.165,SSH 49108 ed25519 免密)做中继:

    你的设备 --ssh -L 18800--> VPS:18800 --ssh -R 18800(本脚本)--> 本机 L4:18800

本脚本只负责**后半段**(VPS 反向隧道 → 本机 L4),并做断线自愈(指数退避重连,
与 daemon 的 ssh_tunnel_manager 同模式)。你设备上的前半段(本地转发)一条命令即可。

用法:
    python l4_remote_relay.py            # 前台跑(Ctrl+C 停)
    python l4_remote_relay.py --daemon   # 后台跑(写 pid 到 aureon/l4_relay.pid)

之后在任何设备上:
    ssh -L 18800:127.0.0.1:18800 -p 49108 root@192.220.14.165
    浏览器开 http://127.0.0.1:18800/?token=<l4_token>
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

# 转发的服务:VPS loopback 端口 -> 本机端口。18800=l4_web,18801=SecureDM。
FORWARDS: list[tuple[int, int, str]] = [
    (18800, 18800, "l4_web/api/health"),
    (18801, 18801, "dm/api/status"),
]

PID_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_relay.pid"
LOG_FILE = Path.home() / ".local" / "share" / "aureon" / "log" / "l4_relay.log"


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _hide_window_kwargs() -> dict:
    """Windows:子进程不弹 CMD 窗(CREATE_NO_WINDOW + 隐藏 startupinfo)。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": si,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _vps_health_ok(remote_port: int, probe: str) -> bool:
    """探 VPS loopback 上某服务(经反向隧道)是否可达。"""
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-p", str(VPS_PORT), f"{VPS_USER}@{VPS_HOST}",
             f"curl -s -o /dev/null -w %{{http_code}} --max-time 5 http://127.0.0.1:{remote_port}/{probe}"],
            capture_output=True, text=True, timeout=15,
            **_hide_window_kwargs(),
        )
        return "200" in r.stdout
    except Exception:  # noqa: BLE001
        return False


def _start_forward() -> subprocess.Popen:
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-N"]
    for remote, local, _probe in FORWARDS:
        args += ["-R", f"{remote}:127.0.0.1:{local}"]
    args += ["-p", str(VPS_PORT), f"{VPS_USER}@{VPS_HOST}"]
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **_hide_window_kwargs(),
    )


def run_forever() -> None:
    fwd_desc = " ".join(f"VPS:{r}->本机:{l}" for r, l, _ in FORWARDS)
    _log(f"L4 远程中继启动: {fwd_desc}")
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    backoff = 5
    probe_interval = 60  # 探测间隔:15s → 60s,降低开销(隧道断主要靠前向 keepalive 自愈)
    proc: subprocess.Popen | None = None
    while True:
        if proc is None or proc.poll() is not None:
            if proc is not None:
                _log(f"反向隧道断开(exit={proc.returncode}),{backoff}s 后重连")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
            proc = _start_forward()
            _log(f"反向隧道已建立(pid={proc.pid})")
            backoff = 5
        time.sleep(probe_interval)
        # 双判定:子进程存活 + 所有服务 VPS loopback 真可达
        if proc.poll() is None and all(_vps_health_ok(r, p) for r, _l, p in FORWARDS):
            continue
        if proc.poll() is None:
            _log("VPS loopback 探测失败,重建隧道")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        # Windows 后台:重开自身为分离进程
        DETACHED = 0x00000008 | 0x00000200
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            creationflags=DETACHED, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("L4 远程中继已后台启动")
    else:
        run_forever()
