#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_p1_2_llm_fallback.py — M3.87 P1-2 safe_exec LLM 兜底(2026-09-25)

设计:
  - 验证 _llm_critical_confirm 在 SAFE_EXEC_LLM_DISABLE=1 时禁用
  - 验证 _classify_safety critical 时调 LLM 兜底
  - 验证 LLM 不可用 → 维持 critical(fail-closed)
  - 验证 LLM 说 safe → 降档到 high(不会放过到 safe)
  - 验证 LLM 说 critical → 维持 critical
  - 全部 mock,不真调 LLM(单测,跑速 < 2s)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
sys.path.insert(0, str(_SRC))


def _setup_env(disable: bool = False, model: str = "m3:minimax",
               timeout: str = "3") -> None:
    """统一设环境变量。"""
    if disable:
        os.environ["SAFE_EXEC_LLM_DISABLE"] = "1"
    else:
        os.environ.pop("SAFE_EXEC_LLM_DISABLE", None)
    os.environ["SAFE_EXEC_LLM_MODEL"] = model
    os.environ["SAFE_EXEC_LLM_TIMEOUT"] = timeout


# ============================================================
# 1. SAFE_EXEC_LLM_DISABLE=1 时 _llm_critical_confirm 返 None
# ============================================================
def test_llm_confirm_disabled_returns_none():
    """环境变量 SAFE_EXEC_LLM_DISABLE=1 → 返 None,不调任何 LLM。"""
    from safe_exec import _llm_critical_confirm

    _setup_env(disable=True)
    try:
        with mock.patch("team_lead_tools._resolve_endpoint") as mock_ep:
            res = _llm_critical_confirm("rm -rf /etc", "heuristic: critical match")
        assert res is None, f"禁用时应返 None,got {res}"
        mock_ep.assert_not_called()
        print(f"[1] OK  SAFE_EXEC_LLM_DISABLE=1 → 返 None")
    finally:
        _setup_env(disable=False)


# ============================================================
# 2. endpoint 错误 → 返 None
# ============================================================
def test_llm_confirm_endpoint_error_returns_none():
    """_resolve_endpoint 返 error → _llm_critical_confirm 返 None。"""
    from safe_exec import _llm_critical_confirm

    _setup_env()
    try:
        with mock.patch("team_lead_tools._resolve_endpoint",
                        return_value={"error": "no base_url for m3"}):
            res = _llm_critical_confirm("rm -rf /", "heuristic match")
        assert res is None, f"endpoint 错误应返 None,got {res}"
        print(f"[2] OK  endpoint error → None")
    finally:
        _setup_env(disable=True)


# ============================================================
# 3. LLM 网络超时 → 返 None(让 caller 维持 critical,fail-closed)
# ============================================================
def test_llm_confirm_network_timeout_returns_none():
    """urllib.urlopen timeout → 返 None,不静默放过。"""
    import urllib.error
    from safe_exec import _llm_critical_confirm

    _setup_env()
    try:
        fake_endpoint = {
            "provider": "minimax",
            "model_id": "minimax",
            "base_url": "https://api.test/v1",
            "api_key": "fake-key",
            "protocol": "openai",
            "model_spec": "m3:minimax",
        }
        with mock.patch("team_lead_tools._resolve_endpoint",
                        return_value=fake_endpoint):
            with mock.patch("urllib.request.urlopen",
                            side_effect=TimeoutError("LLM timeout")):
                res = _llm_critical_confirm("rm -rf /etc", "heuristic")
        assert res is None, f"网络超时应返 None,got {res}"
        print(f"[3] OK  LLM timeout → None")
    finally:
        _setup_env(disable=True)


# ============================================================
# 4. LLM 返 VERDICT: safe → 降档到 safe
# ============================================================
def test_llm_confirm_verdict_safe_returns_safe():
    """LLM 返 'VERDICT: safe' → 返 dict {verdict: safe}。"""
    from safe_exec import _llm_critical_confirm

    _setup_env()
    try:
        fake_endpoint = {
            "provider": "minimax",
            "model_id": "minimax",
            "base_url": "https://api.test/v1",
            "api_key": "fake-key",
            "protocol": "openai",
            "model_spec": "m3:minimax",
        }
        fake_response = {
            "choices": [{"message": {"content": "VERDICT: safe"}}],
        }
        fake_resp_obj = mock.MagicMock()
        fake_resp_obj.read.return_value = json.dumps(fake_response).encode()
        fake_resp_obj.__enter__ = mock.MagicMock(return_value=fake_resp_obj)
        fake_resp_obj.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("team_lead_tools._resolve_endpoint",
                        return_value=fake_endpoint):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp_obj):
                res = _llm_critical_confirm("rm -rf build/", "rm -rf 模式")
        assert res is not None, "LLM 返 safe 应返 dict"
        assert res["verdict"] == "safe", f"应判 safe,got {res}"
        print(f"[4] OK  LLM 'VERDICT: safe' → dict verdict=safe")
    finally:
        _setup_env(disable=True)


# ============================================================
# 5. LLM 返 VERDICT: critical → 维持 critical
# ============================================================
def test_llm_confirm_verdict_critical_returns_critical():
    """LLM 返 'VERDICT: critical' → 返 dict {verdict: critical}。"""
    from safe_exec import _llm_critical_confirm

    _setup_env()
    try:
        fake_endpoint = {
            "provider": "minimax",
            "model_id": "minimax",
            "base_url": "https://api.test/v1",
            "api_key": "fake-key",
            "protocol": "openai",
            "model_spec": "m3:minimax",
        }
        fake_response = {
            "choices": [{"message": {"content": "VERDICT: critical"}}],
        }
        fake_resp_obj = mock.MagicMock()
        fake_resp_obj.read.return_value = json.dumps(fake_response).encode()
        fake_resp_obj.__enter__ = mock.MagicMock(return_value=fake_resp_obj)
        fake_resp_obj.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("team_lead_tools._resolve_endpoint",
                        return_value=fake_endpoint):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp_obj):
                res = _llm_critical_confirm("rm -rf /etc", "rm -rf 系统目录")
        assert res is not None
        assert res["verdict"] == "critical", f"应判 critical,got {res}"
        print(f"[5] OK  LLM 'VERDICT: critical' → 维持 critical")
    finally:
        _setup_env(disable=True)


# ============================================================
# 6. _classify_safety 调 _llm_critical_confirm(critical 时)
# ============================================================
def test_classify_safety_invokes_llm_on_critical():
    """heuristic 报 critical 时,_classify_safety 必须调 LLM 兜底。"""
    from safe_exec import _classify_safety

    _setup_env()
    try:
        with mock.patch("safe_exec._llm_critical_confirm",
                        return_value={"verdict": "safe",
                                      "model": "m3:minimax",
                                      "raw": "VERDICT: safe"}) as mock_confirm:
            res = _classify_safety("rm -rf C:/Windows/System32")

        mock_confirm.assert_called_once()
        # heuristic 命中 'rm -rf 系统路径' → 报 critical,LLM 说 safe → 降档
        assert res["risk"] == "high", f"LLM 说 safe 时应降档到 high,got {res['risk']}"
        assert res["backend"] == "heuristic+llm_override"
        assert res.get("llm_confirm", {}).get("verdict") == "safe"
        print(f"[6] OK  critical + LLM safe → 降档到 high,backend=heuristic+llm_override")
    finally:
        _setup_env(disable=True)


# ============================================================
# 7. LLM 不可用 → 维持 critical(fail-closed)
# ============================================================
def test_classify_safety_maintains_critical_when_llm_fails():
    """heuristic 报 critical + LLM 不可用 → 维持 critical,fail-closed。"""
    from safe_exec import _classify_safety

    _setup_env()
    try:
        with mock.patch("safe_exec._llm_critical_confirm",
                        return_value=None) as mock_confirm:
            res = _classify_safety("rm -rf /etc")

        mock_confirm.assert_called_once()
        # LLM 不可用 → 维持 critical(fail-closed)
        assert res["risk"] == "critical", (
            f"LLM 不可用应维持 critical,got {res['risk']}"
        )
        assert res.get("llm_confirm") is None
        print(f"[7] OK  critical + LLM 不可用 → 维持 critical(fail-closed)")
    finally:
        _setup_env(disable=True)


# ============================================================
# 8. LLM 说 critical → 维持 critical(同意 heuristic)
# ============================================================
def test_classify_safety_maintains_critical_when_llm_agrees():
    """heuristic 报 critical + LLM 也说 critical → 维持 critical。"""
    from safe_exec import _classify_safety

    _setup_env()
    try:
        with mock.patch("safe_exec._llm_critical_confirm",
                        return_value={"verdict": "critical",
                                      "model": "m3:minimax",
                                      "raw": "VERDICT: critical"}):
            res = _classify_safety("rm -rf C:/Windows")

        assert res["risk"] == "critical", (
            f"LLM 同意应维持 critical,got {res['risk']}"
        )
        assert res.get("llm_confirm", {}).get("verdict") == "critical"
        print(f"[8] OK  critical + LLM 也说 critical → 维持 critical")
    finally:
        _setup_env(disable=True)


# ============================================================
# 9. heuristic 不报 critical → 不调 LLM
# ============================================================
def test_classify_safety_does_not_invoke_llm_on_safe():
    """heuristic 报 safe/low/medium/high → 不调 LLM 兜底(快路径)。"""
    from safe_exec import _classify_safety

    _setup_env()
    try:
        with mock.patch("safe_exec._llm_critical_confirm") as mock_confirm:
            # 普通 ls 命令 — heuristic 应判 safe,根本不调 LLM
            res = _classify_safety("ls -la /tmp")
        mock_confirm.assert_not_called()
        print(f"[9] OK  heuristic=safe → _llm_critical_confirm 未调用")
    finally:
        _setup_env(disable=True)


# ============================================================
# 10. SAFE_EXEC_LLM_DISABLE=1 时,即使 critical 也不调 LLM
# ============================================================
def test_classify_safety_disabled_skips_llm():
    """禁用 LLM → critical 走 heuristic-only,team_lead_tools 不被调。"""
    from safe_exec import _classify_safety

    _setup_env(disable=True)
    try:
        # 关键断言:_resolve_endpoint(真正发起 LLM 请求的入口)不应被调用
        with mock.patch("team_lead_tools._resolve_endpoint") as mock_ep:
            res = _classify_safety("rm -rf C:/Windows")
        mock_ep.assert_not_called()
        # 仍然 critical(fail-closed)
        assert res["risk"] == "critical", (
            f"禁用 LLM 时 critical 命令仍应判 critical(fail-closed),got {res['risk']}"
        )
        # 禁用模式:无 llm_confirm 字段(因为根本没发起调用)
        assert res.get("llm_confirm") is None, (
            f"禁用模式不应含 llm_confirm 字段,got {res.get('llm_confirm')}"
        )
        # backend 应保持 heuristic,没有 llm_override
        assert res.get("backend") == "heuristic", (
            f"禁用模式 backend 应是 heuristic,got {res.get('backend')}"
        )
        print(f"[10] OK SAFE_EXEC_LLM_DISABLE=1 → critical 走 heuristic-only,_resolve_endpoint 未调,backend=heuristic")
    finally:
        _setup_env(disable=True)


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== safe_exec M3.87 P1-2 LLM 兜底测试 ===\n")
    print(f"路径: {_SRC}\n")

    tests = [
        test_llm_confirm_disabled_returns_none,
        test_llm_confirm_endpoint_error_returns_none,
        test_llm_confirm_network_timeout_returns_none,
        test_llm_confirm_verdict_safe_returns_safe,
        test_llm_confirm_verdict_critical_returns_critical,
        test_classify_safety_invokes_llm_on_critical,
        test_classify_safety_maintains_critical_when_llm_fails,
        test_classify_safety_maintains_critical_when_llm_agrees,
        test_classify_safety_does_not_invoke_llm_on_safe,
        test_classify_safety_disabled_skips_llm,
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
