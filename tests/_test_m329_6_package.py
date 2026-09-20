# -*- coding: utf-8 -*-
"""
M3.29.6 PyInstaller 打包 + NSIS 安装包 e2e

覆盖:
  T1: PyInstaller spec 文件存在 + 含 PrisirAI-music-web exe name
  T2: dist/PrisirAI-music-web.exe 已生成(<200MB,无 litellm/rapidocr 大依赖)
  T3: exe smoke 启动 — 双击 → music web 在随机端口监听 → HKCU 写 music_port
  T4: exe 服务 NL intent + cfg set + lyrics 静态页 + music-static CSS 全部 200
  T5: 陪聊 web /api/music/dispatch 能正确探测 exe 写过的 music_port
  T6: installer/prisirai.nsi 含 File "..\\dist\\PrisirAI-music-web.exe" 行

回滚预案:PyInstaller spec 删 + dist exe 删 + nsi 行删,PrisirAI 主安装包退回到 M3.28 状态
(陪聊 web 仍可调 LX Bridge 兜底,M3.29 三进程架构只挂 launcher)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def http_get(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def http_post(url: str, body: dict, timeout: float = 5.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "err": e.read().decode("utf-8")}


results: list = []


def check(actual, expected, label: str) -> None:
    if actual == expected:
        print(f"  PASS {label}")
        results.append(True)
    else:
        print(f"  FAIL {label}: got {actual!r}, expected {expected!r}")
        results.append(False)


def check_true(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS {label}")
        results.append(True)
    else:
        print(f"  FAIL {label}")
        results.append(False)


def main() -> int:
    # T1: spec file exists
    print("\n[T1] PyInstaller spec 文件")
    spec = ROOT / "prisIr-music-web.spec"
    check_true(spec.exists(), f"prisIr-music-web.spec exists")
    if spec.exists():
        src = spec.read_text(encoding="utf-8")
        check_true("PrisirAI-music-web" in src, "spec name = PrisirAI-music-web")
        check_true("prisiragent-music-web.py" in src, "entry script = prisiragent-music-web.py")
        check_true("excludes" in src, "excludes declared")

    # T2: dist exe exists
    print("\n[T2] dist/PrisirAI-music-web.exe 已生成")
    exe = ROOT / "dist/PrisirAI-music-web.exe"
    check_true(exe.exists(), f"dist exe exists")
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"  size: {size_mb:.1f} MB")
        check_true(size_mb < 200, f"size < 200 MB (got {size_mb:.1f} MB)")
        check_true(size_mb > 10, f"size > 10 MB (got {size_mb:.1f} MB) — 非空")

    # T3: exe smoke 启动
    print("\n[T3] exe smoke 启动")
    # 清空 HKCU music_port(避免读陈旧)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI", 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, "music_port")
                print(f"  cleared stale music_port")
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    # 启动 exe(后台)
    log_path = Path(os.environ.get("TEMP", "/tmp")) / "music_web_exe_smoke.log"
    try:
        log_path.unlink(missing_ok=True)
    except PermissionError:
        # 上次跑的 log 还没释放 — 换一个名
        log_path = Path(os.environ.get("TEMP", "/tmp")) / f"music_web_exe_smoke_{int(time.time())}.log"
    log_f = open(log_path, "wb")
    proc = subprocess.Popen(
        [str(exe), "--port", "0", "--host", "127.0.0.1"],
        stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    exe_pid = proc.pid
    print(f"  spawned exe pid={exe_pid}")
    # 等就绪
    port = None
    for _ in range(40):  # 20s 超时
        time.sleep(0.5)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
                port = int(winreg.QueryValueEx(k, "music_port")[0])
                break
        except FileNotFoundError:
            continue
        except Exception:
            continue
    if port:
        check_true(port > 0, f"HKCU music_port written ({port})")
    else:
        check_true(False, "HKCU music_port not written within 20s")
        print(f"  log dump:")
        try:
            print(log_path.read_text(encoding="utf-8", errors="ignore")[:2000])
        except Exception:
            pass

    # T4: exe 服务 NL + cfg + 静态
    print("\n[T4] exe 服务 endpoints")
    if port:
        s, body = http_get(f"http://127.0.0.1:{port}/api/agent/cfg/list")
        check(s, 200, f"/api/agent/cfg/list 200 (got {s})")
        s, j = http_post(f"http://127.0.0.1:{port}/api/agent/cfg/set", {"lyrics.font_size": 50})
        check_true(j.get("ok"), "POST /api/agent/cfg/set ok")
        s, j = http_post(f"http://127.0.0.1:{port}/api/agent/intent", {"text": "歌词大点"})
        check_true(j.get("intent", {}).get("matched"), "POST /api/agent/intent 歌词大点 matched")
        s, j = http_post(f"http://127.0.0.1:{port}/api/agent/intent", {"text": "放周杰伦的晴天"})
        check_true(j.get("intent", {}).get("matched"), "POST /api/agent/intent 放周杰伦的晴天 matched")
        check(j.get("intent", {}).get("payload", {}).get("query"), "周杰伦的晴天",
              f"query=周杰伦的晴天")
        s, _ = http_get(f"http://127.0.0.1:{port}/lyrics")
        check(s, 200, "/lyrics page 200")
        s, _ = http_get(f"http://127.0.0.1:{port}/music-static/lyrics.css")
        check(s, 200, "/music-static/lyrics.css 200")

    # T5: companion /api/music/dispatch 探测 exe
    print("\n[T5] 陪聊 /api/music/dispatch 探测 exe 端口")
    if port:
        s, j = http_get("http://127.0.0.1:18850/api/music/dispatch")
        d = json.loads(j) if s == 200 else {}
        check(d.get("source"), "self", f"companion dispatch source=self (got {d.get('source')!r})")
        check_true(d.get("self_running"), "self_running=True")
        check(d.get("self_port"), port, f"companion dispatch self_port matches exe (got {d.get('self_port')})")

    # T6: nsi 已加 PrisirAI-music-web.exe 行
    print("\n[T6] installer/prisirai.nsi 含 music web exe")
    nsi = (ROOT / "installer/prisirai.nsi").read_text(encoding="utf-8")
    check_true("..\\dist\\PrisirAI-music-web.exe" in nsi,
              "nsi 含 ..\\dist\\PrisirAI-music-web.exe (NSI 反斜杠路径)")

    # cleanup: kill exe
    print("\n[cleanup] killing exe pid=" + str(exe_pid))
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(exe_pid)], capture_output=True, timeout=5)
    except Exception:
        pass

    total = len(results)
    passed = sum(results)
    print(f"\n=== M3.29.6 PyInstaller + nsi: {passed}/{total} pass ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())