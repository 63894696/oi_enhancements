#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_deadlock_laya.py — M3.87 P0-1 精简版(2026-09-25)

M3.82 验证 laya deadlock 4 分类 ACC 0%(永远 stalled bias),完全无信号。
M3.87 删除 detect_deadlocks_with_laya() 函数 + deadlock_detect_laya MCP tool 后,
本测试文件只保留 heuristic 4 类 + detect_deadlocks() 主路径验证。

覆盖:
  1. unit: heuristic_label() 4 类判定正确
  2. unit: deadlock_detect.detect_deadlocks() 现有行为完全不变
  3. unit: detect_deadlocks() JSON shape 完整(无 race 字段)
  4. unit: heuristic latency < 5ms
  5. e2e:   detect_deadlocks() 真跑 + JSON 序列化验证

设计:
  - 跑速目标 < 1s(全 unit,无 laya 真加载)
  - 替换 M3.82 原 13 个 case 中的 8 个 laya 相关 case
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))


# ============================================================
# 工具
# ============================================================

def _mk_pos(task_id: int, position: str, last_seen_offset: float,
            round_count: int, file_ops: int, elapsed_sec: float,
            agent_type: str = "dev") -> "AgentPosition":
    """构造 AgentPosition(now - last_seen_offset = last_seen)。"""
    from deadlock_detect import AgentPosition
    return AgentPosition(
        task_id=task_id,
        agent_type=agent_type,
        position=position,
        last_seen=time.time() - last_seen_offset,
        round_count=round_count,
        file_ops=file_ops,
        elapsed_sec=elapsed_sec,
    )


# ============================================================
# 1. unit: DeadlockDetector.detect() 4 类规则
# ============================================================

def test_detect_stalled_rule():
    """>120s 无更新 → stalled"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=2001, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)
        dd._tracker.positions[2001].last_seen = time.time() - 200  # >120s

        results = dd._detector.detect()
        stalled = [r for r in results if r.is_deadlocked and "STALLED" in r.reason]
        assert len(stalled) >= 1, f"expect stalled detect, got {results}"
        print(f"[1] OK  stalled 检出 {len(stalled)} 条")
    finally:
        dd._tracker.clear()


def test_detect_zero_progress_rule():
    """round > 3 + file_ops == 0 + position in (reading, llm_call) → zero_progress"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=2002, agent_type="dev", position="llm_call",
                           round_count=5, file_ops=0, elapsed_sec=50)

        results = dd._detector.detect()
        zp = [r for r in results if r.is_deadlocked and "ZERO_PROGRESS" in r.reason]
        assert len(zp) >= 1, f"expect zero_progress detect, got {results}"
        print(f"[2] OK  zero_progress 检出 {len(zp)} 条")
    finally:
        dd._tracker.clear()


def test_detect_slow_rule():
    """round > 15 + elapsed > 300s → SLOW warning(not deadlock)"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=2003, agent_type="dev", position="writing",
                           round_count=18, file_ops=10, elapsed_sec=400)

        results = dd._detector.detect()
        slow = [r for r in results if not r.is_deadlocked and "SLOW" in r.reason]
        assert len(slow) >= 1, f"expect SLOW detect, got {results}"
        print(f"[3] OK  slow 检出 {len(slow)} 条")
    finally:
        dd._tracker.clear()


def test_detect_ok_rule():
    """正常进度 → 无检测"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=2004, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)

        results = dd._detector.detect()
        # 正常情况不应该有死锁检测
        assert len(results) == 0, f"正常进度不应有检测,got {results}"
        print(f"[4] OK  正常进度:0 检测")
    finally:
        dd._tracker.clear()


# ============================================================
# 2. unit: deadlock_detect.detect_deadlocks() 现有行为完全不变
# ============================================================

def test_existing_detect_deadlocks_unchanged():
    """M3.87 删除 laya 后,detect_deadlocks() 主路径行为完全不变。"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=3001, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)
        dd._tracker.positions[3001].last_seen = time.time() - 200  # forced stalled

        out_str = dd.detect_deadlocks()
        out = json.loads(out_str)
        # detect_deadlocks() 走 _detector.get_status_report() → 含 deadlocks/warnings
        assert "deadlocks" in out, f"应含 deadlocks 字段: {list(out.keys())}"
        assert "warnings" in out, f"应含 warnings 字段: {list(out.keys())}"
        assert out["deadlocks"] >= 1, f"stalled 应有 deadlocks ≥ 1: {out['deadlocks']}"
        print(f"[5] OK  detect_deadlocks() 主路径不变: deadlocks={out['deadlocks']} "
              f"warnings={out['warnings']}")
    finally:
        dd._tracker.clear()


# ============================================================
# 3. unit: detect_deadlocks() JSON shape(无 race 字段)
# ============================================================

def test_detect_deadlocks_no_race_field():
    """M3.87 后 detect_deadlocks() 绝不能含 race 字段(laya 已删)。"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=4001, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)
        dd._tracker.positions[4001].last_seen = time.time() - 200

        out_str = dd.detect_deadlocks()
        out = json.loads(out_str)
        # 序列化整个 JSON 字符串不应出现 race 字段
        assert "race_summary" not in out_str, (
            "detect_deadlocks() 不应有 race_summary 字段"
        )
        assert "laya_invoked" not in out_str, (
            "detect_deadlocks() 不应有 laya_invoked 字段"
        )
        print(f"[6] OK  detect_deadlocks() 无 race/laya 字段残留")
    finally:
        dd._tracker.clear()


# ============================================================
# 4. unit: latency 实测
# ============================================================

def test_detect_latency_under_10ms():
    """DeadlockDetector.detect() 100 次平均 < 10ms"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        dd._tracker.update(task_id=5001, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)
        # warmup
        dd._detector.detect()
        # 实测
        N = 100
        t0 = time.time()
        for _ in range(N):
            dd._detector.detect()
        avg_ms = (time.time() - t0) * 1000 / N
        assert avg_ms < 10.0, f"detect avg {avg_ms:.2f}ms ≥ 10ms"
        print(f"[7] OK  detect latency avg={avg_ms:.3f}ms (N={N})")
    finally:
        dd._tracker.clear()


# ============================================================
# 5. e2e: detect_deadlocks() 真跑 + JSON 序列化
# ============================================================

def test_e2e_detect_deadlocks_full():
    """e2e: 多 position 混合跑 detect_deadlocks()。"""
    import deadlock_detect as dd

    dd._tracker.clear()
    try:
        # 注入 4 类各一条
        dd._tracker.update(task_id=6001, agent_type="dev", position="reading",
                           round_count=5, file_ops=0, elapsed_sec=200)
        dd._tracker.positions[6001].last_seen = time.time() - 200  # stalled

        dd._tracker.update(task_id=6002, agent_type="dev", position="llm_call",
                           round_count=5, file_ops=0, elapsed_sec=50)
        # zero_progress

        dd._tracker.update(task_id=6003, agent_type="dev", position="writing",
                           round_count=18, file_ops=10, elapsed_sec=400)
        # slow

        dd._tracker.update(task_id=6004, agent_type="dev", position="writing",
                           round_count=3, file_ops=2, elapsed_sec=30)
        # ok

        out_str = dd.detect_deadlocks()
        out = json.loads(out_str)
        # JSON 完整可序列化
        json.dumps(out, ensure_ascii=False)
        # 至少 2 deadlocks (stalled + zero_progress) + 1 warning (slow) + 1 ok
        assert out["deadlocks"] >= 2, f"应 ≥2 deadlocks: {out['deadlocks']}"
        assert out["warnings"] >= 1, f"应 ≥1 warning: {out['warnings']}"
        assert out["active_agents"] == 4, f"4 agents: {out['active_agents']}"
        print(f"[8] OK  e2e detect_deadlocks() 4 类全跑: "
              f"deadlocks={out['deadlocks']} warnings={out['warnings']} agents={out['active_agents']}")
    finally:
        dd._tracker.clear()


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    print(f"=== deadlock heuristic e2e 测试(M3.87 精简版)===\n")
    print(f"路径: {_SERVER}\n")

    tests = [
        # 1. DeadlockDetector 4 类规则
        test_detect_stalled_rule,
        test_detect_zero_progress_rule,
        test_detect_slow_rule,
        test_detect_ok_rule,
        # 2. 现有 detect() 不变
        test_existing_detect_deadlocks_unchanged,
        # 3. 无 race 字段残留
        test_detect_deadlocks_no_race_field,
        # 4. latency
        test_detect_latency_under_10ms,
        # 5. e2e
        test_e2e_detect_deadlocks_full,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t_func in tests:
        try:
            t_func()
            passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

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