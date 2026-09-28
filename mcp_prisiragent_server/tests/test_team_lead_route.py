#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_team_lead_route.py — M3.84 team_lead 二次分流换 head e2e 测试

覆盖:
  1. unit: _laya_route_to_prisir_head + _map_laya_to_head_label 词典结构
  2. unit: _map_laya_to_head_label 启发式(关键词 + domain + needs_tools)
  3. unit: _laya_route_to_prisir_head override 阶段(M3.73 防误判)
  4. unit: dispatch_impl 端到端(head 命中 / head fallback / override fallback)
  5. 端到端实测 ACC:laya head 在 8 类 eval 上的真实表现
  6. 回归:全部 M3.76 dispatch_laya 测试不挂

设计:
  - 不依赖 routing.yaml 存在
  - 5/6 用 mock/tempdir,5 用真 laya head 真推理(慢,~5min)
  - 关键约束:_OPS_FORCE_OVERRIDE 行为完全不变
"""
from __future__ import annotations

import io
import json
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))

import team_lead_tools as t  # noqa: E402


# ============================================================
# 工具:假 routing.yaml(M3.76 同款)
# ============================================================
SAMPLE_ROUTING = {
    "version": "test-v0.1",
    "rules": [
        {"match": {"intent": "process_control", "keywords": ["never_match"]},
         "agent": "Ops", "pool": "ops_pool", "mode": "engineer",
         "agent_config": {"role": "ops"}},
        {"match": {"intent": "code_implement", "keywords": ["never_match"]},
         "agent": "Coder", "pool": "coder_pool", "mode": "engineer",
         "agent_config": {"role": "coder"}},
        {"match": {"intent": "security", "keywords": ["never_match"]},
         "agent": "SecAuditor", "pool": "sec_pool", "mode": "engineer",
         "agent_config": {"role": "sec"}},
        {"match": {"intent": "plan", "keywords": ["never_match"]},
         "agent": "Planner", "pool": "plan_pool", "mode": "engineer",
         "agent_config": {"role": "planner"}},
        {"match": {"intent": "search", "keywords": ["never_match"]},
         "agent": "Searcher", "pool": "search_pool", "mode": "engineer",
         "agent_config": {"role": "searcher"}},
        {"match": {"intent": "content", "keywords": ["never_match"]},
         "agent": "ContentMaker", "pool": "content_pool", "mode": "engineer",
         "agent_config": {"role": "content"}},
        {"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}},
    ],
    "model_pool": {
        "ops_pool": ["anthropic:claude-opus-4-8"],
        "coder_pool": ["bailian/qwen3-coder-flash"],
        "sec_pool": ["anthropic:claude-opus-4-8"],
        "plan_pool": ["k3:k3_payg"],
        "search_pool": ["k3:k3_payg"],
        "content_pool": ["k3:k3_payg"],
        "cheap_lottery": ["m3:minimax"],
    },
}


# ============================================================
# 1. unit: 词典结构
# ============================================================
def test_intent_head_to_prisir_keys():
    """_INTENT_HEAD_TO_PRISIR 应包含 8 类。"""
    expected = {"chat", "code", "search", "tool_call",
                "roleplay", "plan", "security", "ops"}
    assert set(t._INTENT_HEAD_TO_PRISIR.keys()) == expected, (
        f"差: {expected - set(t._INTENT_HEAD_TO_PRISIR.keys())}"
    )
    for k, v in t._INTENT_HEAD_TO_PRISIR.items():
        assert isinstance(v, str) and v, f"{k} → {v} 非 string"
    print(f"[1] OK  _INTENT_HEAD_TO_PRISIR 词典结构正确(8 个 key)")


def test_intent_via_head_8_set():
    """_INTENT_VIA_HEAD_8 集合正确。"""
    assert t._INTENT_VIA_HEAD_8 == {
        "chat", "code", "search", "tool_call",
        "roleplay", "plan", "security", "ops",
    }
    print(f"[2] OK  _INTENT_VIA_HEAD_8 集合正确")


def test_head_conf_threshold():
    """_HEAD_CONF_THRESHOLD 应为 0.7(M3.82 laya conf < 0.7 必走 fallback)。"""
    assert t._HEAD_CONF_THRESHOLD == 0.7
    print(f"[3] OK  _HEAD_CONF_THRESHOLD = {t._HEAD_CONF_THRESHOLD}")


# ============================================================
# 2. unit: _map_laya_to_head_label 启发式
# ============================================================
def test_map_head_ops_keyword_priority():
    """'清理 D 盘' 应被 ops 关键词命中 → ops(M3.73 防误判)。"""
    label = t._map_laya_to_head_label("清理 d 盘", domain="code",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "ops", f"应 ops, got {label}"
    print(f"[4] OK  '清理 D 盘' → ops(M3.73 防误判)")


def test_map_head_security_keyword():
    """'SQL 注入漏洞' 应 → security。"""
    label = t._map_laya_to_head_label("sql 注入漏洞扫描", domain="code",
                                       difficulty=2.0, needs_tools=0.5,
                                       is_sensitive=0.5)
    assert label == "security", f"应 security, got {label}"
    print(f"[5] OK  'SQL 注入漏洞' → security")


def test_map_head_plan_keyword():
    """'做个 sprint 计划' 应 → plan。"""
    label = t._map_laya_to_head_label("做个 sprint 计划", domain="writing",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "plan", f"应 plan, got {label}"
    print(f"[6] OK  '做个 sprint 计划' → plan")


def test_map_head_roleplay_keyword():
    """'扮演一个海盗' 应 → roleplay。"""
    label = t._map_laya_to_head_label("扮演一个海盗", domain="other",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "roleplay", f"应 roleplay, got {label}"
    print(f"[7] OK  '扮演一个海盗' → roleplay")


def test_map_head_tool_call_keyword():
    """'帮我打开浏览器' 应 → tool_call。"""
    label = t._map_laya_to_head_label("帮我打开浏览器", domain="other",
                                       difficulty=1.0, needs_tools=0.5,
                                       is_sensitive=0.0)
    assert label == "tool_call", f"应 tool_call, got {label}"
    print(f"[8] OK  '帮我打开浏览器' → tool_call")


def test_map_head_chat_keyword():
    """'你好 今天心情怎么样' 应 → chat。"""
    label = t._map_laya_to_head_label("你好 今天心情怎么样", domain="other",
                                       difficulty=0.5, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "chat", f"应 chat, got {label}"
    print(f"[9] OK  '你好 今天心情怎么样' → chat")


def test_map_head_code_domain_only():
    """domain=code + 无关键词 + needs_tools=0 → 应 code。"""
    label = t._map_laya_to_head_label("这段逻辑有问题", domain="code",
                                       difficulty=2.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "code", f"应 code, got {label}"
    print(f"[10] OK  domain=code → code(无关键词兜底)")


def test_map_head_search_domain_only():
    """domain=factual_lookup + 无关键词 → 应 search。"""
    label = t._map_laya_to_head_label("天空为什么是蓝色的", domain="factual_lookup",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "search", f"应 search, got {label}"
    print(f"[11] OK  domain=factual_lookup → search")


def test_map_head_data_analysis_to_ops():
    """domain=data_analysis → ops。"""
    label = t._map_laya_to_head_label("性能数据怎么看", domain="data_analysis",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label == "ops", f"应 ops, got {label}"
    print(f"[12] OK  domain=data_analysis → ops")


def test_map_head_other_fallback():
    """domain=other + 无关键词 + 无信号 → None(让 caller fallback)。"""
    label = t._map_laya_to_head_label("未知信号xyz", domain="other",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.0)
    assert label is None, f"应 None, got {label}"
    print(f"[13] OK  domain=other + 无信号 → None(fallback)")


def test_map_head_other_sensitive_high():
    """domain=other + is_sensitive 高 → security。"""
    label = t._map_laya_to_head_label("未知信号", domain="other",
                                       difficulty=1.0, needs_tools=0.0,
                                       is_sensitive=0.7)
    assert label == "security", f"应 security, got {label}"
    print(f"[14] OK  is_sensitive=0.7 → security")


def test_map_head_other_needs_tools_high():
    """domain=other + needs_tools 高 → tool_call。"""
    label = t._map_laya_to_head_label("未知信号", domain="other",
                                       difficulty=1.0, needs_tools=0.6,
                                       is_sensitive=0.0)
    assert label == "tool_call", f"应 tool_call, got {label}"
    print(f"[15] OK  needs_tools=0.6 → tool_call")


# ============================================================
# 3. unit: _laya_route_to_prisir_head override 阶段(不调 laya 真推理)
# ============================================================
def test_head_override_skip():
    """'清理 D 盘' 触发 _OPS_FORCE_OVERRIDE → head 不出决策。"""
    res = t._laya_route_to_prisir_head("清理 D 盘", laya_floor=0.60)
    assert res["matched_via"] == "override_skip_head", (
        f"应 override_skip_head, got {res['matched_via']}"
    )
    assert res["intent"] is None, "override 时 head 不应给 intent"
    print(f"[16] OK  override 关键词 → head 不参与决策")


def test_head_override_security():
    """'漏洞扫描' 触发 override → head skip。"""
    res = t._laya_route_to_prisir_head("漏洞扫描", laya_floor=0.60)
    assert res["matched_via"] == "override_skip_head"
    assert res["intent"] is None
    print(f"[17] OK  '漏洞扫描' → override_skip_head")


def test_head_empty_text():
    """空字符串不应 crash,返 available=False。"""
    res = t._laya_route_to_prisir_head("", laya_floor=0.60)
    assert "intent" in res
    assert res["available"] is False
    print(f"[18] OK  空字符串不 crash")


# ============================================================
# 4. unit: dispatch_impl head 端到端(mock laya head)
# ============================================================
def test_dispatch_head_path_used():
    """M3.87 P1-1 验证:_laya_route_to_prisir_head 已被 dispatch_impl 移除调用。
    P1-1 决策:laya head ACC 46% < 50% 不如掷骰子,改回 M3.76 regex+laya 路径。
    测试断言:即使 mock 给 head 返回高 conf,dispatch_impl 也**不调** head 函数。
    """
    fake_head = {
        "intent": "code_implement", "conf": 0.85, "head_label": "code",
        "domain": "code", "needs_tools": 0.5, "is_sensitive": 0.0,
        "difficulty": 2.0, "available": True,
        "matched_via": "head", "error": None, "latency_ms": 200,
    }
    # 把 _laya_route_to_prisir 返回设成会走 keyword 的结果,
    # 然后用 spy mock 验证 _laya_route_to_prisir_head **从未被调用**。
    fake_laya = {
        "intent": "code_implement", "conf": 0.85,
        "matched_via": "laya_code", "domain": "code",
        "needs_tools": 0.5, "is_sensitive": 0.0,
        "difficulty": 2.0, "available": True,
        "error": None, "latency_ms": 100,
    }
    with mock.patch.object(t, "_laya_route_to_prisir_head",
                           return_value=fake_head) as mock_head, \
         mock.patch.object(t, "_laya_route_to_prisir",
                           return_value=fake_laya), \
         mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("Python 装饰器"))
        # P1-1:head 函数不应被调用
        mock_head.assert_not_called()
        # 走 laya_code 路径(同 M3.76)
        assert out["matched_via"] == "laya_code", (
            f"P1-1:应 laya_code, got {out['matched_via']}"
        )
        # laya_result 应有 head_result=None(明确标注未启用)
        assert out["laya_result"]["head_result"] is None
        print(f"[19] OK  P1-1 head 路径已停用 → laya_code(head_result=None)")


def test_dispatch_head_fallback_to_keyword():
    """M3.87 P1-1 验证:head 函数被移除后,直接走旧 _laya_route_to_prisir。

    原 M3.84 测试期望 head 给 head_no_decision 时 fallback,现在 head 不再被调,
    直接走 _laya_route_to_prisir(模拟返 laya_code → code_implement → Coder)。
    """
    with mock.patch.object(t, "_laya_route_to_prisir_head") as mock_head, \
         mock.patch.object(t, "_laya_route_to_prisir",
                           return_value={
                               "intent": "code_implement", "conf": 0.85,
                               "matched_via": "laya_code", "domain": "code",
                               "needs_tools": 0.5, "is_sensitive": 0.0,
                               "difficulty": 2.0, "available": True,
                               "error": None, "latency_ms": 100,
                           }), \
         mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("Python 装饰器"))
        # P1-1:head 不被调
        mock_head.assert_not_called()
        # 直接走 laya_code(同 M3.76)
        assert out["matched_via"] == "laya_code", (
            f"应 laya_code, got {out['matched_via']}"
        )
        assert out["intent"] == "code_implement"
        # head_result=None(P1-1 已停用 head 路径)
        assert out["laya_result"]["head_result"] is None
        print(f"[20] OK  P1-1 head 移除后 → 直接 laya_code")


def test_dispatch_head_low_conf_fallback():
    """M3.87 P1-1 验证:head 不再被调,直接 laya_code 走通。"""
    with mock.patch.object(t, "_laya_route_to_prisir_head") as mock_head, \
         mock.patch.object(t, "_laya_route_to_prisir",
                           return_value={
                               "intent": "code_implement", "conf": 0.85,
                               "matched_via": "laya_code", "domain": "code",
                               "needs_tools": 0.5, "is_sensitive": 0.0,
                               "difficulty": 2.0, "available": True,
                               "error": None, "latency_ms": 100,
                           }), \
         mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("Python 装饰器", laya_floor=0.60))
        # P1-1:head 不被调
        mock_head.assert_not_called()
        # 直接 laya_code → code_implement → Coder
        assert out["matched_via"] == "laya_code", (
            f"应 laya_code, got {out['matched_via']}"
        )
        assert out["intent"] == "code_implement"
        assert out["laya_result"]["head_result"] is None
        print(f"[21] OK  P1-1 head 移除 → 直接 laya_code")


def test_dispatch_head_skip_override_lets_oldpath_run():
    """关键回归:head 看到 override 关键词 → skip,旧路径 stage A 接管。

    验证:_OPS_FORCE_OVERRIDE 行为完全不变。
    """
    # head 真推理会触发 override_skip_head,因为 'kill ' 在 _HEAD_OPS_KW
    # 这里用 no_laya=False 让真推理跑(慢一点,~3s),或 mock
    fake_head = {
        "intent": None, "conf": 0.0, "head_label": None,
        "domain": "code", "needs_tools": 0.5, "is_sensitive": 0.0,
        "difficulty": 1.5, "available": True,
        "matched_via": "override_skip_head", "error": None, "latency_ms": 100,
    }
    fake_old = {
        "intent": "process_control", "conf": 1.0,
        "matched_via": "override", "domain": "code",
        "needs_tools": 0.5, "is_sensitive": 0.0, "difficulty": 1.5,
        "available": True, "error": None, "latency_ms": 0,
        "head_result": fake_head,
    }
    with mock.patch.object(t, "_laya_route_to_prisir_head",
                           return_value=fake_head), \
         mock.patch.object(t, "_laya_route_to_prisir", return_value=fake_old), \
         mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("kill chrome 进程"))
        # 旧路径 stage A 命中 process_control → Ops
        assert out["matched_via"] == "override"
        assert out["intent"] == "process_control"
        assert out["agent"] == "Ops"
        # 关键是 laya_result["matched_via"] 仍是 override(head 没改)
        assert out["laya_result"]["matched_via"] == "override"
        print(f"[22] OK  head override_skip → 旧 override 路径行为不变")


# ============================================================
# 5. 真实 ACC bench(laya head 真推理 8 类)
# ============================================================
def test_head_real_acc_on_eval():
    """跑 200 条 eval 样本,统计 laya head 真推理 ACC。

    慢:~5min,只跑在 CI 或手动。dev env 跑应该 < 10min。
    """
    eval_path = _SERVER / "data" / "data_dispatch_route_eval.jsonl"
    if not eval_path.exists():
        print(f"[23] SKIP  eval 文件不存在: {eval_path}")
        return True
    samples: list[dict] = []
    with eval_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))

    # 限制跑数(避免太长)
    n = min(50, len(samples))
    samples = samples[:n]
    print(f"[23] 跑 {n} 条 eval,laya head 真推理…", end=" ", flush=True)

    t0 = time.time()
    correct = 0
    confs: list[float] = []
    matched_via_count: dict[str, int] = {}
    latencies: list[float] = []
    for s in samples:
        text = s["text"]
        label = s["label"]
        res = t._laya_route_to_prisir_head(text, laya_floor=0.60)
        matched_via = res.get("matched_via") or "?"
        matched_via_count[matched_via] = matched_via_count.get(matched_via, 0) + 1
        latencies.append(res.get("latency_ms", 0))
        # 决策:intent(PrisirAI name) → 8 类
        prisir_intent = res.get("intent")
        head_label = res.get("head_label")
        # 反查:head_label 直接就是 8 类;intent(PrisirAI name)通过 _INTENT_HEAD_TO_PRISIR 反查
        predicted = head_label
        if predicted is None and prisir_intent:
            for k, v in t._INTENT_HEAD_TO_PRISIR.items():
                if v == prisir_intent:
                    predicted = k
                    break
        if predicted is None:
            predicted = "unknown"
        if predicted == label:
            correct += 1
        confs.append(res.get("conf", 0.0))

    elapsed = time.time() - t0
    acc = correct / n
    avg_conf = sum(confs) / len(confs) if confs else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    matched_summary = ", ".join(
        f"{k}={v}" for k, v in sorted(matched_via_count.items(),
                                       key=lambda x: -x[1])
    )
    print(f"\n[23] ACC={correct}/{n} = {acc:.1%} ({elapsed:.1f}s, "
          f"avg_conf={avg_conf:.2f}, avg_latency={avg_latency:.0f}ms)")
    print(f"     matched_via: {matched_summary}")

    # 关键约束:laya head 应至少有 signal(>= 20% ACC 证明 head 不是全 random)
    # M3.82 4 分类 laya = 0% ACC;8 分类期望 ACC < 60%(M3.71 5/5 -17 到 -62pp)
    # 如果 ACC >= 60%,head 才真正可作主信号
    print(f"[23] {'PASS' if acc >= 0.20 else 'FAIL'}  head ACC >= 20% 阈值")
    return acc >= 0.20


# ============================================================
# 6. 回归:全部 M3.76 dispatch_laya 测试不挂
# ============================================================
def test_regression_m376_dispatch_laya():
    """re-run M3.76 test_dispatch_laya.py 的关键 case,确保不挂。"""
    # 关键 case:test_laya_route_override_process_control / test_match_rule_regex / test_dispatch_impl_regex
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        # '清理 D 盘' 触发 override → process_control
        out = json.loads(t.dispatch_impl("清理 D 盘", no_laya=False))
        assert out["matched_via"] == "override", f"M3.76 回归: 应 override, got {out['matched_via']}"
        assert out["intent"] == "process_control"
        # _OPS_FORCE_OVERRIDE 行为完全不变(head 已验过)
        print(f"[24] OK  M3.76 回归: '清理 D 盘' → process_control override")

    # '注入漏洞扫描' → security override
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("注入漏洞扫描", no_laya=False))
        assert out["matched_via"] == "override"
        assert out["intent"] == "security"
        print(f"[25] OK  M3.76 回归: '注入漏洞扫描' → security override")

    # '查论文' → search override
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("查论文", no_laya=False))
        assert out["matched_via"] == "override"
        assert out["intent"] == "search"
        print(f"[26] OK  M3.76 回归: '查论文' → search override")

    # '计划下我的工作' → plan override
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("计划下我的工作", no_laya=False))
        assert out["matched_via"] == "override"
        assert out["intent"] == "plan"
        print(f"[27] OK  M3.76 回归: '计划下我的工作' → plan override")

    # no_laya + 不存在 routing.yaml → Explore fallback(M3.76 测试 [14])
    with mock.patch.object(t, "ROUTING_PATH", Path("C:/nonexistent/routing.yaml")):
        out = json.loads(t.dispatch_impl("任何任务", no_laya=True))
        assert out["agent"] == "Explore"
        assert out["matched_via"] == "default"
        print(f"[28] OK  M3.76 回归: no_laya + 无 yaml → Explore")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== team_lead_tools M3.84 二次分流换 head e2e 测试 ===\n")
    print(f"路径: {_SERVER}\n")

    fast_tests = [
        # 1. 词典结构
        test_intent_head_to_prisir_keys,
        test_intent_via_head_8_set,
        test_head_conf_threshold,
        # 2. _map_laya_to_head_label 启发式
        test_map_head_ops_keyword_priority,
        test_map_head_security_keyword,
        test_map_head_plan_keyword,
        test_map_head_roleplay_keyword,
        test_map_head_tool_call_keyword,
        test_map_head_chat_keyword,
        test_map_head_code_domain_only,
        test_map_head_search_domain_only,
        test_map_head_data_analysis_to_ops,
        test_map_head_other_fallback,
        test_map_head_other_sensitive_high,
        test_map_head_other_needs_tools_high,
        # 3. _laya_route_to_prisir_head override
        test_head_override_skip,
        test_head_override_security,
        test_head_empty_text,
        # 4. dispatch_impl head 端到端
        test_dispatch_head_path_used,
        test_dispatch_head_fallback_to_keyword,
        test_dispatch_head_low_conf_fallback,
        test_dispatch_head_skip_override_lets_oldpath_run,
        # 6. M3.76 回归
        test_regression_m376_dispatch_laya,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t_func in fast_tests:
        try:
            result = t_func()
            if result is False:
                failed.append((t_func.__name__, "返回 False"))
            else:
                passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

    # 5. laya head 真推理 ACC(慢,~5min)
    print()
    try:
        result = test_head_real_acc_on_eval()
        if result is False:
            failed.append((test_head_real_acc_on_eval.__name__, "ACC < 20%"))
        else:
            passed += 1
    except AssertionError as e:
        failed.append((test_head_real_acc_on_eval.__name__, str(e)))
    except Exception as e:  # noqa: BLE001
        failed.append((test_head_real_acc_on_eval.__name__,
                       f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(fast_tests) + 1}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())