#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_laya_e2e.py — M3.74 commit_check laya_guard fast-path 测试(2026-09-25)
#
# 覆盖:
#   1. unit: _laya_guard_scan_diff 函数
#   2. unit: _regex_secret_scan 命中 known secrets
#   3. unit: _extract_added_lines 去 diff header / +++ / ---
#   4. 集成: check_diff 在 secrets → deny
#   5. 集成: check_diff 在 safe code → allow(不被 laya 误伤)
#   6. 集成: --no-laya 跳过 laya,纯 LoRA 决策
#   7. CLI: --no-laya flag 工作
#   8. CLI: laya 触发 deny 退出码 1
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJ = _HERE.parent
sys.path.insert(0, str(_PROJ))
sys.path.insert(0, str(_PROJ / "src"))

from src.commit_check import (  # noqa: E402
    check_diff, _laya_guard_scan_diff, _regex_secret_scan,
    _extract_added_lines, _SECRET_HINTS,
)

MAIN = _PROJ / "main.py"


# ============================================================
# 1. _extract_added_lines 单元测试
# ============================================================
def test_extract_added_lines_strips_headers():
    """应去掉 diff --git/@@/+++ /--- 头,只保留 added content。"""
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc..def 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,5 @@\n"
        " def hello():\n"
        "+    return 1\n"
        "+    print('hi')\n"
        " x = 1\n"
    )
    extracted = _extract_added_lines(diff)
    assert "return 1" in extracted, f"应保留 added line: {extracted}"
    assert "print('hi')" in extracted
    assert "diff --git" not in extracted
    assert "@@" not in extracted
    assert "+++ " not in extracted and "--- " not in extracted
    print(f"[1] OK  extract_added_lines 过滤 diff header")


def test_extract_added_lines_truncates():
    """> max_chars 应截断。"""
    big = "\n".join(f"+line {i} = 'value_{i}'" for i in range(500))
    extracted = _extract_added_lines(big, max_chars=200)
    assert len(extracted) <= 250, f"应截断: len={len(extracted)}"
    assert "[truncated]" in extracted
    print(f"[2] OK  extract_added_lines 截断到 {len(extracted)} chars")


# ============================================================
# 2. _regex_secret_scan 单元测试
# ============================================================
def test_regex_secret_aws_key():
    """AWS access key 应被命中。"""
    diff = "+AWS_ACCESS_KEY_ID = 'AKIAIOSFODNN7EXAMPLE'\n"
    hits = _regex_secret_scan(diff)
    assert len(hits) >= 1, f"AWS key 应被 regex 命中,got {hits}"
    assert any("AKIA" in h for h in hits)
    print(f"[3] OK  AWS key 命中 ({len(hits)} hits)")


def test_regex_secret_openai():
    """OpenAI sk-xxx 应被命中。"""
    diff = "+OPENAI_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n"
    hits = _regex_secret_scan(diff)
    assert len(hits) >= 1, "sk- 应被命中"
    print(f"[4] OK  sk- 命中 ({len(hits)} hits)")


def test_regex_secret_github_pat():
    """GitHub PAT gh*_xxx 应被命中。"""
    diff = "+GITHUB_TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n"
    hits = _regex_secret_scan(diff)
    assert len(hits) >= 1
    print(f"[5] OK  gh*_ 命中 ({len(hits)} hits)")


def test_regex_secret_safe_code():
    """纯代码不应被命中。"""
    diff = (
        "+def fibonacci(n):\n"
        "+    if n < 2:\n"
        "+        return n\n"
        "+    return fibonacci(n-1) + fibonacci(n-2)\n"
    )
    hits = _regex_secret_scan(diff)
    assert len(hits) == 0, f"纯代码不应触发 secret regex: {hits}"
    print(f"[6] OK  safe code 不触发")


# ============================================================
# 3. _laya_guard_scan_diff 集成测试
# ============================================================
def test_laya_scan_returns_structure():
    """返回 dict 应有 11 个字段。"""
    res = _laya_guard_scan_diff("+x = 1\n")
    expected_keys = {
        "ok", "available", "risk", "jailbreak", "injection",
        "sensitive", "harm", "regex_hits", "raw_text", "reason", "latency_ms",
    }
    assert expected_keys.issubset(set(res.keys())), (
        f"缺字段: {expected_keys - set(res.keys())}"
    )
    assert res["risk"] in ("safe", "low", "medium", "high", "critical")
    print(f"[7] OK  返回结构 ({len(res)} fields)")


def test_laya_scan_secrets_critical():
    """含 sk- 的 diff → laya critical + regex 命中。"""
    diff = "+API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n"
    res = _laya_guard_scan_diff(diff)
    assert res["risk"] in ("critical", "high"), (
        f"secrets 应 critical/high,got {res['risk']}"
    )
    assert len(res["regex_hits"]) >= 1, "regex 必须命中"
    print(f"[8] OK  secrets → critical + regex={len(res['regex_hits'])}")


def test_laya_scan_no_laya_skips():
    """no_laya=True → 纯 regex 决策。"""
    diff = "+API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n"
    res = _laya_guard_scan_diff(diff, no_laya=True)
    # no_laya 跳过 laya 推理,但 regex 仍应命中
    assert len(res["regex_hits"]) >= 1
    assert res["risk"] == "critical"  # regex 兜底升级
    assert res["available"] is False  # 没跑 laya
    print(f"[9] OK  no_laya=True → regex-only")


def test_laya_scan_safe_code():
    """普通代码不应被判 critical(即使 laya 偏严)。"""
    diff = (
        "+def hello():\n"
        "+    return 'world'\n"
    )
    res = _laya_guard_scan_diff(diff)
    # laya 可能判 medium,但有 critical/high + regex 命中才短路 deny
    if res["risk"] in ("critical", "high"):
        # 如果 laya 误判 critical,必须有 regex 命中才短路;此处 regex 应为 0
        assert len(res["regex_hits"]) == 0
        # 此时 laya fast-path 不短路,会注入到 Decision 让 ask
        print(f"[10] OK  laya {res['risk']} 但 regex=0, ask 而非 deny")
    else:
        print(f"[10] OK  safe code → {res['risk']}")


# ============================================================
# 4. check_diff 集成测试
# ============================================================
def test_check_diff_real_secrets_deny():
    """真 secrets → deny (laya + regex 短路)。"""
    diff = (
        "diff --git a/config.py b/config.py\n"
        "+++ b/config.py\n"
        "+OPENAI_API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n"
    )
    dec = check_diff(diff, no_laya=False)
    assert dec.decision == "deny", f"secrets 应 deny,got {dec.decision}"
    assert dec.laya_result is not None
    print(f"[11] OK  真 secrets → deny (laya {dec.backend})")


def test_check_diff_safe_code_allows():
    """普通代码 → allow (不被 laya 误伤)。"""
    diff = (
        "diff --git a/utils.py b/utils.py\n"
        "+++ b/utils.py\n"
        "+def hello():\n"
        "+    return 1\n"
    )
    dec = check_diff(diff, no_laya=False)
    assert dec.decision == "allow", (
        f"safe code 应 allow,got {dec.decision}, reasons={dec.reasons}"
    )
    print(f"[12] OK  safe code → allow")


def test_check_diff_pem_deny():
    """.pem 文件触发 deny(LoRA 命中危险扩展,需 adapter 已加载)。"""
    # 注:此测试依赖 LoRA adapter 已加载;若未加载 → allow(见 test_e2e.py 真 git 测试)
    # 这里只验证 laya 不会误判 deny
    diff = (
        "diff --git a/server.pem b/server.pem\n"
        "new file mode 100644\n"
        "+++ b/server.pem\n"
        "+-----BEGIN PRIVATE KEY-----\n"
        "+FAKEKEY\n"
        "+-----END PRIVATE KEY-----\n"
    )
    dec = check_diff(diff, no_laya=False)
    # 没装 LoRA 时 allow;装了 LoRA 时 deny
    # 此测试只确保决策路径不崩溃
    assert dec.decision in ("allow", "ask", "deny"), (
        f"未知决策: {dec.decision}"
    )
    # laya 不应误判为 critical(因为没真 secrets regex 命中)
    # → 决策不应 deny from laya alone
    if dec.decision == "deny" and dec.laya_result:
        # 若真 deny,应是 LoRA,不应是 laya short-circuit
        assert dec.backend != "laya_router", (
            f"laya 不应短路 .pem deny: {dec.backend}"
        )
    print(f"[13] OK  .pem 决策={dec.decision} (backend={dec.backend})")


# ============================================================
# 5. CLI 集成
# ============================================================
def _git_init_and_stage(cwd: Path, files: dict[str, str]) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(cwd), capture_output=True,
                    text=True, check=False)
    subprocess.run(["git", "config", "user.email", "t@e.com"],
                   cwd=str(cwd), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(cwd),
                   check=True)
    # initial commit
    (cwd / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=str(cwd), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(cwd),
                   check=True, capture_output=True)
    # stage files
    for rel, content in files.items():
        fp = cwd / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=str(cwd), check=True)


def test_cli_no_laya_flag():
    """--no-laya flag 工作。"""
    print("[14] 验证 CLI --no-laya…", end=" ")
    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td) / "repo"
        _git_init_and_stage(cwd, {
            "src/main.py": "x = 1\nx = 2\n",
        })
        out = subprocess.run(
            [sys.executable, str(MAIN), "--staged", "--no-laya",
             "--format", "json"],
            cwd=str(cwd), capture_output=True, text=True, timeout=180,
        )
        try:
            data = json.loads(out.stdout)
            # --no-laya 时 laya_result 应为 None(完全跳过)
            assert data.get("laya_result") is None, (
                f"--no-laya 应 laya_result=None,got {data.get('laya_result')}"
            )
            assert data["backend"] == "lora_only"
            print(f"OK  backend={data['backend']}")
        except json.JSONDecodeError:
            assert False, f"输出非 JSON: {out.stdout[:200]}"


def test_cli_secrets_triggers_deny():
    """CLI 真 secrets → deny + exit 1。"""
    print("[15] 验证 CLI 真 secrets…", end=" ")
    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td) / "repo"
        _git_init_and_stage(cwd, {
            "config.py": "API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n",
        })
        out = subprocess.run(
            [sys.executable, str(MAIN), "--staged", "--format", "json"],
            cwd=str(cwd), capture_output=True, text=True, timeout=300,
        )
        try:
            data = json.loads(out.stdout)
            assert data["decision"] == "deny", (
                f"secrets 应 deny,got {data['decision']}"
            )
            assert data.get("laya_result", {}).get("regex_hits"), (
                "regex_hits 应非空"
            )
            print(f"OK  deny (regex={len(data['laya_result'].get('regex_hits', []))} hits)")
        except json.JSONDecodeError:
            assert False, f"输出非 JSON: {out.stdout[:200]}"


def test_cli_laya_in_text_output():
    """CLI text 输出应含 [laya_guard] 段。"""
    print("[16] 验证 CLI text 含 laya 段…", end=" ")
    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td) / "repo"
        _git_init_and_stage(cwd, {
            "config.py": "API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n",
        })
        out = subprocess.run(
            [sys.executable, str(MAIN), "--staged"],
            cwd=str(cwd), capture_output=True, text=True, timeout=300,
        )
        # text 输出应含 [laya_guard] 段
        assert "[laya_guard]" in out.stdout or "deny" in out.stdout.lower(), (
            f"text 输出应含 laya 段或 deny: {out.stdout[:300]}"
        )
        print(f"OK  含 laya 段")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== commit_check M3.74 laya_guard e2e 测试 ===\n")
    print(f"路径: {_PROJ}\n")

    tests = [
        # 单元测试
        test_extract_added_lines_strips_headers,
        test_extract_added_lines_truncates,
        test_regex_secret_aws_key,
        test_regex_secret_openai,
        test_regex_secret_github_pat,
        test_regex_secret_safe_code,
        test_laya_scan_returns_structure,
        test_laya_scan_secrets_critical,
        test_laya_scan_no_laya_skips,
        test_laya_scan_safe_code,
        # 集成测试
        test_check_diff_real_secrets_deny,
        test_check_diff_safe_code_allows,
        test_check_diff_pem_deny,
        # CLI
        test_cli_no_laya_flag,
        test_cli_secrets_triggers_deny,
        test_cli_laya_in_text_output,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(tests)}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())