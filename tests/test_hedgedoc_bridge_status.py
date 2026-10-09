"""
Phase A 只读扩展 Python wrapper 测试 — HedgeDoc 协同 Markdown(Bearer Token)

回归门禁:确保 HedgeDoc 扩展不破坏已有 Phase A 联合测试。
执行 `python -m pytest tests/test_hedgedoc_bridge_status.py`。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "extensions" / "hedgedoc-bridge-status"
INDEX_JS = EXT_DIR / "index.js"


def _has_node():
    return shutil.which("node") is not None


def _has_npm():
    return shutil.which("npm") is not None


def _ensure_deps():
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
    """HedgeDoc index.js Node 24 语法解析无 syntax error。"""
    if not _has_node():
        pytest.skip("node 不可用")
    r = subprocess.run(
        ["node", "--check", str(INDEX_JS)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"syntax error:\n{r.stderr}"


def test_node_unit_tests_all_pass():
    """Node 单测:7 用例全绿(env default/override/无 token/不可达/SDK 复用/query 拼接)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js")],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"unit fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 7" in r.stdout, f"expected 7 单测:\n{r.stdout}"


def test_node_e2e_all_pass():
    """Node E2E:mock HedgeDoc v2 @ 随机端口 + 13 用例全绿(Bearer + 根 HTML 探活 + 直数组 envelope + 401)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js"), "--e2e"],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"e2e fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 13" in r.stdout, f"expected 13/13 全绿:\n{r.stdout}"
    assert "is_hedgedoc=true" in r.stdout, "根 HTML 探活路径应被执行"
    assert "content_included=false" in r.stdout, "content 字段必须不被抓(产品级 P0)"


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 hedgedoc.health / .notes / .user 三个命令。"""
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
    for cmd in ("hedgedoc.health", "hedgedoc.notes", "hedgedoc.user"):
        assert cmd in r.stdout, f"missing command {cmd}: {r.stdout}"