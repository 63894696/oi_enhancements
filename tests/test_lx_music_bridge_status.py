# -*- coding: utf-8 -*-
"""tests/test_lx_music_bridge_status.py — Phase A 只读扩展单测(调 Node 测试 runner)。

设计:
  - 实际逻辑单测在 extensions/lx-music-bridge-status/__tests__/run.js 里(8 项 + 3 项 E2E)
  - 本文件是 Pytest wrapper,确保 CI 跑测试时也覆盖这个扩展
  - 加 --lx-e2e 才跑真 LX Desktop 端到端;默认 skip

跑法:
  python -m pytest tests/test_lx_music_bridge_status.py -v
  python -m pytest tests/test_lx_music_bridge_status.py -v --lx-e2e  # 加 LX 真跑
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_JS = (
    ROOT / "extensions" / "lx-music-bridge-status" / "__tests__" / "run.js"
)
NODE = shutil.which("node") or "node"


def pytest_addoption(parser):
    parser.addoption(
        "--lx-e2e", action="store_true", default=False,
        help="跑 LX Desktop 端到端测试(需 LX 在 127.0.0.1:23330)",
    )


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, str(RUN_JS), *args],
        capture_output=True, text=True, timeout=60,
        cwd=str(ROOT),
    )


def test_node_unit_tests_all_pass():
    """8 个 Node 端单测全绿。"""
    r = _run([])
    assert r.returncode == 0, (
        f"node unit tests failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    # 必须看到 8 个 ✓ + 0 ✗
    assert "[结果] ✓ 8  ✗ 0" in r.stdout or "✓ 8" in r.stdout, r.stdout


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 lx.status / lx.lyric / lx.health 三个命令。"""
    ext_dir = ROOT / "extensions" / "lx-music-bridge-status"
    inline = (
        "const sdk = require('@prisir/extension-sdk');"
        "const captured = [];"
        "sdk.PrisIrExt = class { constructor() {}"
        "  registerCommand(n, fn) { captured.push(n); }"
        "  registerPanel() {} start() { return Promise.resolve(); } };"
        "require('./index.js');"
        "console.log('REGISTERED:' + JSON.stringify(captured.sort()));"
    )
    r = subprocess.run(
        [NODE, "-e", inline],
        capture_output=True, text=True, timeout=10,
        cwd=str(ext_dir),
    )
    assert r.returncode == 0, f"load failed: {r.stderr}"
    assert "REGISTERED:[" in r.stdout, r.stdout
    for cmd in ("lx.status", "lx.lyric", "lx.health"):
        assert cmd in r.stdout, f"missing command {cmd}: {r.stdout}"


def test_js_syntax_parses():
    """node --check 不报错。"""
    r = subprocess.run(
        [NODE, "--check", str(RUN_JS.parent.parent / "index.js")],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0, f"syntax error: {r.stderr}"


@pytest.mark.skipif(
    not Path("/proc/1").exists() and not Path("C:/Users/Administrator/oi_enhancements").exists(),
    reason="skip — gate by env PRISIR_LX_E2E=1 to enable",
)
def test_lx_real_e2e_all_pass():
    """LX Desktop 真实端到端:status + lyric + health 都过。
    通过 PRISIR_LX_E2E=1 开启;默认 skip。"""
    import os
    if os.environ.get("PRISIR_LX_E2E") != "1":
        pytest.skip("PRISIR_LX_E2E!=1,默认 skip")
    r = _run(["--e2e"])
    assert r.returncode == 0, (
        f"LX E2E failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert "✓ 11  ✗ 0" in r.stdout or "✓ 11" in r.stdout, r.stdout