"""
Phase A 只读扩展 Python wrapper 测试 — Vaultwarden 自托管密码管理器(OAuth2 client_credentials)

回归门禁:确保 Vaultwarden 扩展不破坏已有 Phase A 联合测试。
执行 `python -m pytest tests/test_vaultwarden_bridge_status.py`。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "extensions" / "vaultwarden-bridge-status"
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
    """Vaultwarden index.js Node 24 语法解析无 syntax error。"""
    if not _has_node():
        pytest.skip("node 不可用")
    r = subprocess.run(
        ["node", "--check", str(INDEX_JS)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"syntax error:\n{r.stderr}"


def test_node_unit_tests_all_pass():
    """Node 单测:9 用例全绿(OAuth2 client_credentials 颁发 + token cache + SDK 复用)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js")],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"unit fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 9" in r.stdout, f"expected 9 单测:\n{r.stdout}"


def test_node_e2e_all_pass():
    """Node E2E:mock Vaultwarden server @ 随机端口 + 6 用例全绿(OAuth2 + Bearer + 401)。"""
    if not _has_node():
        pytest.skip("node 不可用")
    _ensure_deps()
    r = subprocess.run(
        ["node", str(EXT_DIR / "__tests__" / "run.js"), "--e2e"],
        cwd=EXT_DIR,
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"e2e fail:\n{r.stdout}\n{r.stderr}"
    assert "✓ 15" in r.stdout, f"expected 15/15 全绿:\n{r.stdout}"
    assert "E2E 错 client_secret" in r.stdout, "e2e 错 client_secret OAuth2 颁发失败分支应被执行"
    assert "E2E token cache 命中" in r.stdout, "e2e cache 命中分支应被执行"


def test_extension_register_commands_via_sdk():
    """验证 PrisIrExt 注册了 vault.health / .profile / .folders / .ciphers 四个命令。"""
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
    for cmd in ("vault.health", "vault.profile", "vault.folders", "vault.ciphers"):
        assert cmd in r.stdout, f"missing command {cmd}: {r.stdout}"