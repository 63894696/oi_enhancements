#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_perf_conf_p2.py — M3.87 P2 perf_conf conf<0.85 改"不确定"(2026-09-25)

设计:
  - 用 mock adapter 不真加载模型
  - 验证 _parse_output 路径下 risk_confidence 字段 + action 强制 review
  - 覆盖 5 个分支:
    1. conf >= 0.85 → risk_confidence="high", action 走原值
    2. conf 0.6~0.85 → risk_confidence="medium", action 强制 review
    3. conf < 0.6 → risk_confidence="low", action 强制 review
    4. conf is None → risk_confidence="none", action 强制 review
    5. high conf + risk=critical → action 保留 alert(原本就支持)
  - 全部 mock,跑速 < 1s
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_COMPANION = _HERE.parent
sys.path.insert(0, str(_COMPANION))


def _fake_adapter(risk: str = "critical", risk_conf: str = "0.5",
                  action: str = "alert", action_conf: str = "0.5",
                  jb: str = "no", jb_conf: str = "0.0") -> mock.MagicMock:
    """构造一个 fake adapter,classify() 返指定 raw 文本。"""
    raw = (
        f"Safety: {risk}:{risk_conf}\n"
        f"Jailbreak: {jb}:{jb_conf}\n"
        f"Action: {action}:{action_conf}"
    )
    fake_res = {"raw": raw, "tokens": 50}
    fake_adapter_obj = mock.MagicMock()
    fake_adapter_obj.classify = mock.MagicMock(return_value=fake_res)
    return fake_adapter_obj


def _fake_sample() -> dict:
    """构造一个最小合法 sample。"""
    return {
        "ts": 1234567890.0,
        "cpu": {"pct": 50.0},
        "memory": {"used_pct": 60.0, "available_gb": 8.0},
        "net": {"nics": [{"nic": "Wi-Fi", "isup": True}]},
        "crash": {"bugcheck_count": 0, "kp41_count": 0},
        "system": {"uptime_s": 3600},
        "proc": {"top": []},
    }


# ============================================================
# 1. conf >= 0.85 → risk_confidence="high", action 走原值
# ============================================================
def test_high_conf_keeps_action():
    """conf=0.92 + critical → risk_confidence=high, action=alert 保留。"""
    from classify_perf import classify_perf

    adapter = _fake_adapter(risk="critical", risk_conf="0.92",
                            action="alert", action_conf="0.88")
    out = classify_perf(adapter, _fake_sample())
    assert out["risk"] == "critical"
    assert out["risk_confidence"] == "high"
    assert out["uncertain"] is False
    assert out["action"] == "alert", (
        f"high conf 时应保留 alert,got {out['action']}"
    )
    print(f"[1] OK  conf=0.92 → risk_confidence=high, action=alert 保留")


# ============================================================
# 2. conf 0.6~0.85 → risk_confidence="medium", action 强制 review
# ============================================================
def test_medium_conf_forces_review():
    """conf=0.7 + critical → risk_confidence=medium, action=review(强制)。"""
    from classify_perf import classify_perf

    adapter = _fake_adapter(risk="critical", risk_conf="0.7",
                            action="alert", action_conf="0.65")
    out = classify_perf(adapter, _fake_sample())
    assert out["risk"] == "critical"
    assert out["risk_confidence"] == "medium"
    assert out["uncertain"] is True
    # 即使 model 想说 alert,中等 conf 时强制 review
    assert out["action"] == "review", (
        f"medium conf 时应强制 review,got {out['action']}"
    )
    print(f"[2] OK  conf=0.7 → risk_confidence=medium, action=review(强制)")


# ============================================================
# 3. conf < 0.6 → risk_confidence="low", action 强制 review
# ============================================================
def test_low_conf_forces_review():
    """conf=0.4 + high → risk_confidence=low, action=review。"""
    from classify_perf import classify_perf

    adapter = _fake_adapter(risk="high", risk_conf="0.4",
                            action="alert", action_conf="0.4")
    out = classify_perf(adapter, _fake_sample())
    assert out["risk"] == "high"
    assert out["risk_confidence"] == "low"
    assert out["uncertain"] is True
    assert out["action"] == "review"
    print(f"[3] OK  conf=0.4 → risk_confidence=low, action=review")


# ============================================================
# 4. conf is None(parse_fail) → risk_confidence="none"
# ============================================================
def test_parse_fail_marks_uncertain():
    """raw 不含 conf → parse_fail → risk_confidence=none, action=review。"""
    from classify_perf import classify_perf

    # 不带 :0.5 → risk_conf 解析为 None
    fake_res = {"raw": "Safety: critical\nJailbreak: no\nAction: alert",
                "tokens": 10}
    adapter = mock.MagicMock()
    adapter.classify = mock.MagicMock(return_value=fake_res)
    out = classify_perf(adapter, _fake_sample())
    assert out["risk"] == "critical"
    assert out["risk_confidence"] == "none"
    assert out["uncertain"] is True
    assert out["action"] == "review"
    assert out["parse_fail"] is True
    print(f"[4] OK  parse_fail → risk_confidence=none, action=review")


# ============================================================
# 5. high conf + safe → action 保留 keep
# ============================================================
def test_high_conf_safe_keeps_action():
    """conf=0.95 + safe → risk_confidence=high, action=keep。"""
    from classify_perf import classify_perf

    adapter = _fake_adapter(risk="safe", risk_conf="0.95",
                            action="keep", action_conf="0.92")
    out = classify_perf(adapter, _fake_sample())
    assert out["risk"] == "safe"
    assert out["risk_confidence"] == "high"
    assert out["uncertain"] is False
    assert out["action"] == "keep"
    print(f"[5] OK  conf=0.95 + safe → risk_confidence=high, action=keep")


# ============================================================
# 6. 阈值常量正确(0.85)
# ============================================================
def test_threshold_constant():
    """PERF_CONF_THRESHOLD = 0.85(M3.87 P2 决策)。"""
    from classify_perf import PERF_CONF_THRESHOLD
    assert PERF_CONF_THRESHOLD == 0.85, (
        f"PERF_CONF_THRESHOLD 应为 0.85,got {PERF_CONF_THRESHOLD}"
    )
    print(f"[6] OK  PERF_CONF_THRESHOLD={PERF_CONF_THRESHOLD}")


# ============================================================
# 7. uncertain 字段在 conf=None 时为 True
# ============================================================
def test_uncertain_field_with_none_conf():
    """rc=None → uncertain=True(risk_confidence=none 也算 uncertain)。"""
    from classify_perf import classify_perf

    # raw 不带 :xxx → rc=None
    fake_res = {"raw": "Safety: high\nJailbreak: no\nAction: alert",
                "tokens": 10}
    adapter = mock.MagicMock()
    adapter.classify = mock.MagicMock(return_value=fake_res)
    out = classify_perf(adapter, _fake_sample())
    assert out["uncertain"] is True
    assert out["risk_confidence"] == "none"
    print(f"[7] OK  conf=None → uncertain=True,risk_confidence=none")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== classify_perf M3.87 P2 置信度分级测试 ===\n")
    print(f"路径: {_COMPANION}\n")

    tests = [
        test_high_conf_keeps_action,
        test_medium_conf_forces_review,
        test_low_conf_forces_review,
        test_parse_fail_marks_uncertain,
        test_high_conf_safe_keeps_action,
        test_threshold_constant,
        test_uncertain_field_with_none_conf,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append((t.__name__, str(e) or "AssertionError without message"))
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
