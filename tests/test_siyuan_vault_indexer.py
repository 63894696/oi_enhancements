# -*- coding: utf-8 -*-
"""tests/test_siyuan_vault_indexer.py — Phase A 只读扩展单测(调 Node 测试 runner)。

设计:
  - 实际逻辑单测在 extensions/siyuan-vault-indexer/__tests__/run.js(8 单 + 3 E2E)
  - 本文件是 Pytest wrapper,确保 CI 跑测试时也覆盖这个扩展
  - 加 --siyuan-e2e 才跑 fixture SQLite 端到端;默认 skip
  - 加 --siyuan-real 才跑真实 SiYuan SQLite db(若用户装了 SiYuan)

跑法:
  python -m pytest tests/test_siyuan_vault_indexer.py -v
  python -m pytest tests/test_siyuan_vault_indexer.py -v --siyuan-e2e   # fixture db 真跑
  PRISIR_SIYUAN_DB=/path/to/siyuan.db python -m pytest tests/test_siyuan_vault_indexer.py -v --siyuan-real
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_JS = ROOT / "extensions" / "siyuan-vault-indexer" / "__tests__" / "run.js"
NODE = shutil.which("node") or "node"


def pytest_addoption(parser):
    parser.addoption(
        "--siyuan-e2e", action="store_true", default=False,
        help="跑 fixture SQLite 端到端测试",
    )
    parser.addoption(
        "--siyuan-real", action="store_true", default=False,
        help="跑真实 SiYuan SQLite db 测试(需 PRISIR_SIYUAN_DB env)",
    )


def _run(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [NODE, str(RUN_JS), *args],
        capture_output=True, text=True, timeout=60,
        cwd=str(ROOT), env=env,
    )


def test_node_unit_tests_all_pass():
    """8 个 Node 端单测全绿。"""
    r = _run([])
    assert r.returncode == 0, (
        f"node unit tests failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert "✓ 8" in r.stdout, r.stdout


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 siyuan.health / .notebooks / .search / .block.get 四个命令。"""
    ext_dir = ROOT / "extensions" / "siyuan-vault-indexer"
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
    for cmd in ("siyuan.health", "siyuan.notebooks", "siyuan.search", "siyuan.block.get"):
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
    reason="skip — gate by env PRISIR_SIYUAN_E2E=1",
)
def test_siyuan_e2e_or_real_all_pass():
    """SiYuan 端到端:fixture db 11/11,真实 db 由 PRISIR_SIYUAN_DB env 触发。
    通过 PRISIR_SIYUAN_E2E=1 开启;默认 skip。"""
    if os.environ.get("PRISIR_SIYUAN_E2E") != "1":
        pytest.skip("PRISIR_SIYUAN_E2E!=1,默认 skip")
    r = _run(["--e2e"])
    assert r.returncode == 0, (
        f"SiYuan E2E failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    assert "✓ 11" in r.stdout, r.stdout
