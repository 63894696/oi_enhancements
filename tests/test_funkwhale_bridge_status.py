"""
Phase A 只读扩展 Python wrapper 测试 — Funkwhale 当前播放(Subsonic 协议)

回归门禁:确保 Funkwhale 扩展不破坏已有 Phase A 联合测试。
执行 `python -m pytest tests/test_funkwhale_bridge_status.py`。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "extensions" / "funkwhale-bridge-status"
INDEX_JS = EXT_DIR / "index.js"


def _has_node():
    return shutil.which("node") is not None


def _has_npm():
    return shutil.which("npm") is not None


def _ensure_deps():
    """确保扩展 node_modules 已装,无则安装(@c/sdk/@prisir/extension-sdk)。"""
    if not (EXT_DIR / "node_modules").exists():
        if not _has_npm():
            pytest.skip("npm 不可用,无法安装扩展依赖")
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=EXT_DIR,
            check=True,
            capture_output=True,
        )


def test_js_syntax_parses():
    """Funkwhale index.js Node 24 语法解析无 syntax error。"""
    if not _has_node():
        pytest.skip("node 不可用")
    r = subprocess.run(
        ["node", "--check", str(INDEX_JS)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"syntax error:\n{r.stderr}"


def test_node_unit_tests_all_pass():
    """Node 单测:7 用例全绿(SDK 三次复用薄壳)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js")],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"unit fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 7" in r.stdout, f"expected 仅 7 单测:\n{r.stdout}"


def test_node_e2e_all_pass():
    """Node E2E:mock Subsonic server @ 随机端口 + 4 用例全绿(.view 后缀验证)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js"), "--e2e"],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"e2e fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 11" in r.stdout, f"expected 11/11 全绿:\n{r.stdout}"
    assert "E2E 错密码" in r.stdout, "e2e 错密码分支应被执行"


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 funkwhale.health / .now-playing / .license 三个命令。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
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
        ["node", "-e", inline],
        capture_output=True, text=True,
        cwd=str(EXT_DIR),
    )
    assert r.returncode == 0, f"load failed: {r.stderr}"
    assert "REGISTERED:[" in r.stdout, r.stdout
    for cmd in ("funkwhale.health", "funkwhale.now-playing", "funkwhale.license"):
        assert cmd in r.stdout, f"missing command {cmd}: {r.stdout}"