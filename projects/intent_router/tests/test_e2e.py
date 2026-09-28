#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_e2e.py — M3.69 intent_router 端到端测试(2026-09-24)
#
# 目的:
#   - 跑 10 个 test case 验证路由映射正确
#   - 不写 unit tests(规则);只写 e2e,跑一次完整推理
#
# 覆盖场景:
#   1. disk_cleanup 类(中文自然语言 → tool_call+general)
#   2. 危险命令(rm -rf) → tool_call+general (disk_cleanup 路径)
#   3. perf 查询 → tool_call+fast
#   4. 简单聊天 → chat+general
#   5. IDE 类 → code+code_call
#   6. 知识问答 → code+code_qa
#   7. web 搜索 → search+general
#   8. 命令查询 → search+fast
#   9. roleplay → roleplay+general
#  10. log 查询(变体)
#
# 验收:
#   - pytest 风格的 PASS/FAIL 输出
#   - **不强制**预测 model 输出完全一致(0.6B 有时漂)
#   - **强制**:路由 spec/executor 必须落到期望区间
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJ = _HERE.parent
sys.path.insert(0, str(_PROJ / "src"))

from intent_router import classify_and_route, route_map, format_text  # noqa: E402


# (text, expected_intent_in, expected_task_in, expected_executor, description)
# expected_intent_in / expected_task_in 可以是 set(命中其一即通过,处理 0.6B 偶尔漂移)
TEST_CASES: list[tuple[str, set, set, set, str]] = [
    ("清理 D 盘垃圾",
     {"tool_call"}, {"general", "fast"}, {"companion_tool_call", "perf_guard"},
     "中文磁盘清理"),
    ("rm -rf C:/Windows",
     {"tool_call"}, {"general", "fast"},
     {"companion_tool_call", "perf_guard"},
     "危险命令 → disk_cleanup"),
    ("查最近 1h 性能",
     {"tool_call", "search"}, {"fast", "general", "code_call"},
     {"perf_guard", "companion_tool_call", "web_search", "ide_tool"},
     "perf 监控查询"),
    ("你好",
     {"chat"}, {"general", "fast"}, {"companion_jev"},
     "简单聊天"),
    ("帮我写个 Python 函数计算斐波那契",
     {"code"}, {"code_call", "code_qa", "long"},
     {"ide_tool", "rag", "code_helper"},
     "code_call 类任务"),
    ("什么是装饰器?",
     {"code", "chat", "search"}, {"code_qa", "general"},
     {"rag", "companion_jev", "web_search"},
     "code_qa 类知识问答"),
    ("搜索一下今天的新闻",
     {"search"}, {"general", "fast"}, {"web_search", "fastlane"},
     "web 搜索"),
    ("查 git 命令用法",
     {"code", "search", "tool_call"}, {"fast", "general", "code_qa"},
     {"fastlane", "companion_tool_call", "web_search", "rag",
      "code_helper", "ide_tool"},
     "fast-path 命令查询"),
    ("扮演一个中世纪骑士和我对话",
     {"roleplay", "chat"}, {"general", "creative", "fast"},
     {"companion_jev"},
     "roleplay 角色扮演"),
    ("查看今天的日志有没有错误",
     {"tool_call", "search", "code"}, {"general", "fast", "code_call"},
     {"companion_tool_call", "perf_guard", "web_search",
      "ide_tool", "code_helper"},
     "log 查询"),
]


def run_one(idx: int, text: str, expected_intents: set,
            expected_tasks: set, expected_executors: set,
            desc: str) -> tuple[bool, dict, str]:
    """跑一条 case,返 (passed, result, msg)。"""
    print(f"\n[Test #{idx}] {desc}")
    print(f"  input: {text!r}")
    try:
        t0 = time.time()
        result = classify_and_route(text, use_conf=True)
        elapsed = time.time() - t0
    except Exception as e:
        msg = f"  [FAIL] exception: {type(e).__name__}: {e}"
        print(msg)
        return False, {}, msg

    intent = result["intent"]
    task = result["task_type"]
    executor = result["route"]["executor"]
    conf = result["confidence"]

    print(f"  → intent={intent} ({result['intent_conf']:.2f})  "
          f"task={task} ({result['task_conf']:.2f})  "
          f"executor={executor}  "
          f"conf={conf:.3f}  "
          f"latency={elapsed:.1f}s")

    # 校验
    checks = []
    intent_ok = intent in expected_intents
    checks.append(("intent in " + str(expected_intents), intent_ok))
    task_ok = task in expected_tasks
    checks.append(("task in " + str(expected_tasks), task_ok))
    exec_ok = executor in expected_executors
    checks.append(("executor in " + str(expected_executors), exec_ok))

    all_ok = all(c[1] for c in checks)
    for name, ok in checks:
        marker = "OK" if ok else "FAIL"
        print(f"    [{marker}] {name}")
    if not all_ok:
        msg = f"  [FAIL] intent={intent} task={task} executor={executor}"
        print(msg)
    else:
        msg = "  [PASS]"
        print(msg)
    return all_ok, result, msg


def main() -> int:
    print("=" * 60)
    print("M3.69 intent_router e2e 测试")
    print("=" * 60)

    # 路由表 dump
    print(f"\n路由映射表共 {len(route_map())} 条:")
    for r in route_map():
        print(f"  ({r['intent']:>10}, {r['task']:>10}) → "
              f"{r['spec_short']:<25} [{r['executor']}]")

    results = []
    print("\n" + "=" * 60)
    print("跑测试 case")
    print("=" * 60)

    for idx, (text, ei, et, ee, desc) in enumerate(TEST_CASES, 1):
        passed, result, msg = run_one(idx, text, ei, et, ee, desc)
        results.append({
            "idx": idx,
            "text": text,
            "desc": desc,
            "passed": passed,
            "intent": result.get("intent"),
            "task": result.get("task_type"),
            "executor": result.get("route", {}).get("executor"),
            "confidence": result.get("confidence"),
            "intent_conf": result.get("intent_conf"),
            "task_conf": result.get("task_conf"),
        })

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    n_pass = sum(1 for r in results if r["passed"])
    n_total = len(results)
    print(f"通过: {n_pass}/{n_total}")

    # 写报告
    report_path = _PROJ / "tests" / "report_e2e.json"
    report_path.write_text(
        json.dumps({
            "summary": {"pass": n_pass, "total": n_total,
                        "pass_rate": round(n_pass / n_total, 3)},
            "route_map_size": len(route_map()),
            "results": results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n报告写入: {report_path}")

    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
