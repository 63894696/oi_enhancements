#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_laya_router.py — M3.73 intent_router laya fast-path 测试(2026-09-24)
#
# 覆盖:
#   1. laya helper 函数(_laya_route_to_5x6)单元测试
#   2. classify_and_route 加 laya 输出(backend="laya_router")
#   3. --no-laya flag 强制 LoRA 路径
#   4. --laya-floor 阈值正确触发 fallback
#   5. CLI 集成
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJ = _HERE.parent
sys.path.insert(0, str(_PROJ / "src"))

from intent_router import (  # noqa: E402
    classify_and_route, _laya_route_to_5x6, _laya_classify_route,
)

MAIN = _PROJ / "main.py"


# ============================================================
# 1. _laya_route_to_5x6 单元测试
# ============================================================
def test_laya_route_easy_code():
    """difficulty=easy + domain=code → code + fast。"""
    answers = {
        "difficulty": {"score": 0.8},
        "domain": {"choice": "code", "probabilities": {"code": 0.9, "other": 0.1}},
        "needs_tools": {"noul": 0.1},
        "is_sensitive": {"noul": 0.0},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert intent == "code", f"easy code → intent=code, got {intent}"
    assert task in ("fast", "general"), f"easy → fast/general, got {task}"
    assert 0.0 <= conf <= 1.0, f"conf out of range: {conf}"
    print(f"[1] OK  code+fast, conf={conf:.3f}")


def test_laya_route_hard_writing():
    """difficulty=hard + domain=writing → chat + long。"""
    answers = {
        "difficulty": {"score": 2.8},
        "domain": {"choice": "writing", "probabilities": {"writing": 0.85}},
        "needs_tools": {"noul": 0.0},
        "is_sensitive": {"noul": 0.0},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert intent == "chat", f"writing → chat, got {intent}"
    assert task == "long", f"hard → long, got {task}"
    print(f"[2] OK  chat+long, conf={conf:.3f}")


def test_laya_route_needs_tools_forces_tool_call():
    """needs_tools ≥ 0.6 → 强制 tool_call(覆盖 chat 默认)。"""
    answers = {
        "difficulty": {"score": 1.0},
        "domain": {"choice": "general_knowledge", "probabilities": {"general_knowledge": 0.8}},
        "needs_tools": {"noul": 0.8},
        "is_sensitive": {"noul": 0.0},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert intent == "tool_call", (
        f"needs_tools 0.8 应强制 tool_call,got {intent}"
    )
    print(f"[3] OK  needs_tools→tool_call, conf={conf:.3f}")


def test_laya_route_sensitive_forces_general():
    """is_sensitive ≥ 0.7 → 强制 task=general(慢路径,需审核)。"""
    answers = {
        "difficulty": {"score": 0.5},  # 原本是 fast
        "domain": {"choice": "code", "probabilities": {"code": 0.9}},
        "needs_tools": {"noul": 0.0},
        "is_sensitive": {"noul": 0.9},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert task == "general", (
        f"sensitive 应强制 task=general,got {task}"
    )
    print(f"[4] OK  sensitive→general, conf={conf:.3f}")


def test_laya_route_factual_lookup():
    """domain=factual_lookup → search。"""
    answers = {
        "difficulty": {"score": 1.0},
        "domain": {"choice": "factual_lookup", "probabilities": {"factual_lookup": 0.9}},
        "needs_tools": {"noul": 0.0},
        "is_sensitive": {"noul": 0.0},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert intent == "search", f"factual_lookup → search, got {intent}"
    print(f"[5] OK  search, conf={conf:.3f}")


def test_laya_route_data_analysis():
    """domain=data_analysis → tool_call。"""
    answers = {
        "difficulty": {"score": 1.5},
        "domain": {"choice": "data_analysis", "probabilities": {"data_analysis": 0.7}},
        "needs_tools": {"noul": 0.5},
        "is_sensitive": {"noul": 0.0},
    }
    intent, task, conf = _laya_route_to_5x6(answers)
    assert intent == "tool_call", f"data_analysis → tool_call, got {intent}"
    print(f"[6] OK  tool_call+general, conf={conf:.3f}")


# ============================================================
# 2. classify_and_route 集成测试(实际跑 laya + LoRA)
# ============================================================
def test_classify_with_laya_returns_laya_result():
    """classify_and_route 默认应跑 laya,backend=lora_fallback 或 laya_router。"""
    print("[7] 验证 classify_and_route 含 laya_result…", end=" ")
    result = classify_and_route("Write a Python script to reverse a list",
                                no_laya=False)
    assert "laya_result" in result, "无 laya_result 字段"
    assert "backend" in result, "无 backend 字段"
    assert result["backend"] in ("laya_router", "lora_fallback", "lora_only"), (
        f"未知 backend: {result['backend']}"
    )
    # laya_result 应有 latency_ms
    if result["laya_result"]:
        assert "latency_ms" in result["laya_result"]
        assert "laya_conf" in result["laya_result"]
        print(f"OK  backend={result['backend']}, "
              f"intent={result['intent']}, task={result['task_type']}, "
              f"laya_conf={result['laya_result'].get('laya_conf', 0):.3f}")


def test_classify_no_laya_skips_laya():
    """--no-laya flag 应跳过 laya,直接 LoRA。"""
    print("[8] 验证 --no-laya 跳过 laya…", end=" ")
    result = classify_and_route("ls D:/Temp", no_laya=True)
    assert result["backend"] == "lora_only", (
        f"--no-laya 应 backend=lora_only,got {result['backend']}"
    )
    assert result["laya_result"] is None, (
        f"--no-laya 应 laya_result=None,got {result['laya_result']}"
    )
    print(f"OK  backend=lora_only, intent={result['intent']}, "
          f"task={result['task_type']}")


def test_high_laya_conf_uses_laya():
    """laya 高置信度场景:应直接用 laya,backend=laya_router。"""
    print("[9] 验证高 laya_conf 走 laya_router…", end=" ")
    # 选简单编程查询(domain=code, difficulty=easy, needs_tools=0)
    result = classify_and_route(
        "Write a Python script to reverse a list",
        laya_floor=0.30,  # 降低门槛确保 laya 命中
    )
    # laya 应对 code 域置信度高,应走 laya_router
    if result["laya_result"]:
        laya_conf = result["laya_result"].get("laya_conf", 0)
        if laya_conf >= 0.30:
            assert result["backend"] == "laya_router", (
                f"laya_conf={laya_conf} >= 0.30 应 backend=laya_router,got {result['backend']}"
            )
            print(f"OK  laya_conf={laya_conf:.3f} ≥ 0.30 → backend=laya_router")
        else:
            print(f"OK  laya_conf={laya_conf:.3f} < 0.30 → fallback (预期行为)")


# ============================================================
# 3. CLI 集成
# ============================================================
def test_cli_with_laya():
    """CLI 默认启用 laya。"""
    print("[10] 验证 CLI 含 laya_result…", end=" ")
    out = subprocess.run(
        [sys.executable, str(MAIN), "--text", "Reverse a list in Python",
         "--format", "json"],
        capture_output=True, text=True, timeout=180,
    )
    try:
        parsed = json.loads(out.stdout)
        assert "laya_result" in parsed, "JSON 无 laya_result"
        assert "backend" in parsed
        assert parsed["backend"] in ("laya_router", "lora_fallback", "lora_only")
        print(f"OK  backend={parsed['backend']}, "
              f"laya_conf={parsed['laya_result'].get('laya_conf', 0):.3f}"
              if parsed.get("laya_result")
              else f"OK  backend={parsed['backend']} (no laya)")
    except json.JSONDecodeError:
        assert False, f"输出非 JSON: {out.stdout[:200]}"


def test_cli_no_laya_flag():
    """CLI --no-laya 应跳过 laya。"""
    print("[11] 验证 CLI --no-laya…", end=" ")
    out = subprocess.run(
        [sys.executable, str(MAIN), "--text", "ls D:/Temp",
         "--no-laya", "--format", "json"],
        capture_output=True, text=True, timeout=120,
    )
    try:
        parsed = json.loads(out.stdout)
        assert parsed["backend"] == "lora_only"
        assert parsed["laya_result"] is None
        print(f"OK  backend=lora_only")
    except json.JSONDecodeError:
        assert False, f"输出非 JSON: {out.stdout[:200]}"


def test_cli_laya_floor_high():
    """CLI --laya-floor=1.0(极高)→ 必 fallback LoRA。"""
    print("[12] 验证 CLI --laya-floor=1.0…", end=" ")
    out = subprocess.run(
        [sys.executable, str(MAIN), "--text", "Reverse a list",
         "--laya-floor", "1.0", "--format", "json"],
        capture_output=True, text=True, timeout=180,
    )
    try:
        parsed = json.loads(out.stdout)
        # laya_conf 通常 < 1.0,应 fallback
        assert parsed["backend"] in ("lora_fallback", "lora_only"), (
            f"laya_floor=1.0 时几乎必 fallback,got {parsed['backend']}"
        )
        print(f"OK  backend={parsed['backend']}")
    except json.JSONDecodeError:
        assert False, f"输出非 JSON: {out.stdout[:200]}"


# ============================================================
# 主入口
# ============================================================
def main():
    print(f"=== intent_router M3.73 laya fast-path 测试 ===\n")
    print(f"路径: {_PROJ}\n")

    tests = [
        # 单元测试
        test_laya_route_easy_code,
        test_laya_route_hard_writing,
        test_laya_route_needs_tools_forces_tool_call,
        test_laya_route_sensitive_forces_general,
        test_laya_route_factual_lookup,
        test_laya_route_data_analysis,
        # 集成测试
        test_classify_with_laya_returns_laya_result,
        test_classify_no_laya_skips_laya,
        test_high_laya_conf_uses_laya,
        # CLI 集成
        test_cli_with_laya,
        test_cli_no_laya_flag,
        test_cli_laya_floor_high,
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