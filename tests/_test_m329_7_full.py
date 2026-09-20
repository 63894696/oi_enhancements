# -*- coding: utf-8 -*-
"""
M3.29.7 全链路 e2e 套件 — 顺序执行 M3.29 所有子测试

执行顺序:
  1. _test_m329_1_backend.py
  2. _test_m329_3_lyrics_window.py
  3. _test_m329_4_tray.py
  4. _test_m329_5_agent_cfg.py
  5. _test_m329_6_package.py

注意:M3.29.2 走 Puppeteer 真实浏览器(慢,且依赖 chromium 二进制),
     默认不跑(被排除);运行时人工确认 M3.29.2 已通过即可。

回滚预案:整套 M3.29 回滚见 memory 文件「回滚预案」节,本脚本只报告。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

tests = [
    "_test_m329_1_backend.py",
    "_test_m329_3_lyrics_window.py",
    "_test_m329_4_tray.py",
    "_test_m329_5_agent_cfg.py",
    "_test_m329_6_package.py",
]


def ensure_music_web() -> bool:
    """确保 music web 真在跑:HKCU 端口探测 → 死则调 /api/music/start → 探测 HKCU 新端口。

    不依赖 dispatch(后者读 HKCU 后返 self_port,但 server 死了 HKCU 不一定清,
    dispatch 仍会返死端口 — 必须配合 socket probe 才准)。"""
    import socket as _sock
    import time as _t
    import urllib.request
    import winreg

    def _port_alive(p: int) -> bool:
        try:
            with _sock.create_connection(("127.0.0.1", p), timeout=1.0):
                return True
        except OSError:
            return False

    def _read_hkcu_port() -> int | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
                return int(winreg.QueryValueEx(k, "music_port")[0])
        except Exception:
            return None

    # 1) 直接读 HKCU + probe
    p = _read_hkcu_port()
    if p and _port_alive(p):
        return True

    # 2) HKCU 没端口或端口死了 — 调 /api/music/start 兜底启动
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:18850/api/music/start",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"  [ensure_music_web] start call failed: {e}")
        return False

    # 3) 等 music web 启动 + 写 HKCU + 端口可达
    for _ in range(40):  # 12s
        _t.sleep(0.3)
        p = _read_hkcu_port()
        if p and _port_alive(p):
            return True
    print(f"  [ensure_music_web] no alive port after start (HKCU={p})")
    return False


def main() -> int:
    results = []
    for t in tests:
        print(f"\n{'='*60}\n=== {t} ===\n{'='*60}")
        # 测前确保 music web 在跑(除 M3.29.6 之外)
        if t != "_test_m329_6_package.py":
            ok = ensure_music_web()
            if not ok:
                print(f"  [WARN] music web not running before {t}")
        r = subprocess.run(
            [sys.executable, f"tests/{t}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=180,
        )
        last_line = ""
        for line in (r.stdout + r.stderr).splitlines():
            if "pass" in line.lower() and "===" in line:
                last_line = line
        print(last_line if last_line else f"rc={r.returncode}")
        # 调试:如果 rc != 0 打印尾部
        if r.returncode != 0:
            print("  --- FAILED stdout tail ---")
            for line in r.stdout.splitlines()[-15:]:
                print(f"    {line}")
            print("  --- FAILED stderr tail ---")
            for line in r.stderr.splitlines()[-10:]:
                print(f"    {line}")
        results.append((t, r.returncode, last_line))
        # 测后再确认 music web 在跑(给下一个测用)
        if t == "_test_m329_6_package.py":
            ok = ensure_music_web()
            if not ok:
                print(f"  [WARN] could not restart music web after {t}")

    print(f"\n{'='*60}")
    print(f"M3.29 全链路 e2e 套件汇总")
    print(f"{'='*60}")
    for t, rc, line in results:
        sym = "✅" if rc == 0 else "❌"
        print(f"  {sym} {t}: {line}")
    fails = sum(1 for _, rc, _ in results if rc != 0)
    print(f"\n{len(results) - fails}/{len(results)} sub-tests pass")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())