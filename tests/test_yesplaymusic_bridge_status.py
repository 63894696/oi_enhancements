# -*- coding: utf-8 -*-
"""tests/test_yesplaymusic_bridge_status.py — Phase A 只读扩展单测(调 Node 测试 runner)。

设计:
  - 实际逻辑单测在 extensions/yesplaymusic-bridge-status/__tests__/run.js(8 单 + 3 E2E)
  - E2E 起临时 Node http server mock,不依赖真实 YesPlayMusic 启动
  - 本文件是 Pytest wrapper,确保 CI 跑测试时也覆盖这个扩展

跑法:
  python -m pytest tests/test_yesplaymusic_bridge_status.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_JS = ROOT / "extensions" / "yesplaymusic-bridge-status" / "__tests__" / "run.js"
NODE = shutil.which("node") or "node"


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
    assert "✓ 8" in r.stdout, r.stdout


def test_node_e2e_all_pass():
    """3 个 Node E2E(mock HTTP server 模拟 YesPlayMusic 响应)全绿。"""
    r = _run(["--e2e"])
    assert r.returncode == 0, (
        f"node E2E failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert "✓ 11" in r.stdout, r.stdout


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 yesplaymusic.health / .status / .current-track 三个命令。"""
    ext_dir = ROOT / "extensions" / "yesplaymusic-bridge-status"
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
    for cmd in ("yesplaymusic.health", "yesplaymusic.status", "yesplaymusic.current-track"):
        assert cmd in r.stdout, f"missing command {cmd}: {r.stdout}"


def test_js_syntax_parses():
    """node --check 不报错。"""
    r = subprocess.run(
        [NODE, "--check", str(RUN_JS.parent.parent / "index.js")],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0, f"syntax error: {r.stderr}"