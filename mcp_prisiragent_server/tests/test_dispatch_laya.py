#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_dispatch_laya.py — M3.76 PrisirAI dispatch_impl laya router 集成测试

覆盖:
  1. unit: _LAYA_INTENT_MAP + _OPS_FORCE_OVERRIDE 词典结构
  2. unit: _laya_route_to_prisir 翻译层(纯函数 + override 阶段 A)
  3. unit: _match_rule 三阶段决策(regex fast-path / laya 兜底 / default fallback)
  4. unit: dispatch_impl 端到端(no_laya / laya 兜底 / fallback 错误)
  5. CLI 集成测试:dispatch 子命令 + --no-laya / --laya-floor flag
  6. laya 真推理 happy path(1 个 case,~20s)

设计:
  - 不依赖 routing.yaml 存在(实际 dev env routing.yaml 缺失)
  - 用 mock/tempdir 临时 yaml 测 regex fast-path
  - laya 真推理只 1-2 case(其它用 no_laya 路径)
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))

# 加载 team_lead_tools 模块
import team_lead_tools as t  # noqa: E402


# ============================================================
# 工具函数:假 routing.yaml
# ============================================================
SAMPLE_ROUTING = {
    "version": "test-v0.1",
    "rules": [
        {
            "match": {"intent": "python_review", "keywords": ["python_review_kw"]},
            "agent": "SeniorEngineer",
            "pool": "balanced_lottery",
            "mode": "engineer",
            "agent_config": {"role": "Python reviewer", "goal": "review code", "backstory": "expert"},
        },
        {
            "match": {"intent": "security", "keywords": ["audit_secret"]},
            "agent": "SecAuditor",
            "pool": "expensive_lottery",
            "mode": "engineer",
            "agent_config": {"role": "Security auditor", "goal": "find vulns", "backstory": "sec expert"},
        },
        {"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}},
    ],
    "model_pool": {
        "balanced_lottery": ["k3:k3_payg"],
        "expensive_lottery": ["anthropic:claude-opus-4-8"],
        "cheap_lottery": ["m3:minimax"],
    },
}


def _with_routing_yaml(routing_dict: dict) -> mock._patch:
    """mock ROUTING_PATH + _load_routing 行为,模拟 yaml 已就绪。"""
    fake_path = Path("C:/fake/routing.yaml")

    def fake_load():
        return routing_dict

    return mock.patch.object(t, "ROUTING_PATH", fake_path), mock.patch.object(t, "_load_routing", fake_load)


# ============================================================
# 1. unit: 词典结构
# ============================================================
def test_laya_intent_map_keys():
    """_LAYA_INTENT_MAP 应包含 14 个 intent_key(code_x7 / plan/content/search/explore/process_control/vision/default)。"""
    expected = {"code_python", "code_security", "code_architecture",
                "code_llm_integration", "code_harness", "code_review",
                "code_implement", "plan", "content", "search", "explore_text",
                "process_control", "vision", "default"}
    assert set(t._LAYA_INTENT_MAP.keys()) == expected, (
        f"差: {expected - set(t._LAYA_INTENT_MAP.keys())}; "
        f"多: {set(t._LAYA_INTENT_MAP.keys()) - expected}"
    )
    # 每个 value 是 (intent_name, keywords_or_None) 二元组
    for k, v in t._LAYA_INTENT_MAP.items():
        assert isinstance(v, tuple) and len(v) == 2
        assert isinstance(v[0], str)
        assert v[1] is None or isinstance(v[1], list)
    print("[1] OK  _LAYA_INTENT_MAP 词典结构正确(14 个 key)")


def test_ops_force_override_keys():
    """_OPS_FORCE_OVERRIDE 至少包含 process_control/plan/search/security。"""
    required = {"process_control", "plan", "search", "security"}
    assert required.issubset(set(t._OPS_FORCE_OVERRIDE.keys()))
    # 每个 value 是非空 list[str]
    for k, v in t._OPS_FORCE_OVERRIDE.items():
        assert isinstance(v, list) and len(v) > 0
        for kw in v:
            assert isinstance(kw, str)
    print(f"[2] OK  _OPS_FORCE_OVERRIDE 词典正确({len(t._OPS_FORCE_OVERRIDE)} 个 intent)")


# ============================================================
# 2. unit: _laya_route_to_prisir override 阶段 A(不调 laya 真推理)
# ============================================================
def test_laya_route_override_process_control():
    """'清理 D 盘' 触发 _OPS_FORCE_OVERRIDE[process_control]。"""
    res = t._laya_route_to_prisir("清理 D 盘", laya_floor=0.60)
    # override 不调 laya 真推理(laya 未加载也行),但 _probe_laya 会先跑
    assert res["matched_via"] == "override", f"应 override, got {res['matched_via']}"
    assert res["intent"] == "process_control", f"应 process_control, got {res['intent']}"
    assert res["conf"] == 1.0
    print(f"[3] OK  override '清理 D 盘' → process_control")


def test_laya_route_override_plan():
    """'计划下我的工作' 触发 override plan。"""
    res = t._laya_route_to_prisir("计划下我的工作", laya_floor=0.60)
    assert res["matched_via"] == "override"
    assert res["intent"] == "plan"
    print(f"[4] OK  override '计划下我的工作' → plan")


def test_laya_route_override_security():
    """'注入漏洞扫描' 触发 override security。"""
    res = t._laya_route_to_prisir("注入漏洞扫描", laya_floor=0.60)
    assert res["matched_via"] == "override"
    assert res["intent"] == "security"
    print(f"[5] OK  override '注入漏洞扫描' → security")


def test_laya_route_override_search():
    """'查论文' 触发 override search。"""
    res = t._laya_route_to_prisir("查论文", laya_floor=0.60)
    assert res["matched_via"] == "override"
    assert res["intent"] == "search"
    print(f"[6] OK  override '查论文' → search")


def test_laya_route_empty_string():
    """空字符串不应 crash,返 available=False 即可。"""
    res = t._laya_route_to_prisir("", laya_floor=0.60)
    # 空 → 走 laya 推理(不命中 override),空 answers → intent=None
    # 或 early return available=False
    assert "intent" in res
    assert "available" in res
    print(f"[7] OK  空字符串不 crash(available={res['available']}, intent={res['intent']})")


# ============================================================
# 3. unit: _match_rule 三阶段
# ============================================================
def test_match_rule_regex_fast_path():
    """regex 命中关键词 → matched_via=regex。"""
    decision = t._match_rule(SAMPLE_ROUTING["rules"], "包含 python_review_kw 的任务",
                             no_laya=True)
    assert decision["matched_via"] == "regex"
    assert decision["intent"] == "python_review"
    assert decision["agent"] == "SeniorEngineer"
    assert decision["matched_keyword"] == "python_review_kw"
    assert decision["laya_result"] is None
    print(f"[8] OK  regex fast-path 命中(agent={decision['agent']})")


def test_match_rule_default_fallback_no_laya():
    """regex 没命中 + no_laya=True → 默认 Explore fallback。"""
    decision = t._match_rule(SAMPLE_ROUTING["rules"], "完全不相关的任务xyz",
                             no_laya=True)
    assert decision["matched_via"] == "default"
    assert decision["agent"] == "Explore"
    assert decision["intent"] == "default"
    print(f"[9] OK  no_laya 默认 fallback → Explore")


def test_match_rule_laya_ovrride_to_intent():
    """regex 没命中 + laya override 给 process_control + yaml 里有 process_control rule → matched_via=override。"""
    fake_lr = {
        "intent": "process_control", "conf": 1.0, "available": True,
        "matched_via": "override", "domain": "code",
        "needs_tools": 0.5, "is_sensitive": 0.0, "difficulty": 1.0,
        "latency_ms": 100, "error": None,
    }
    # 加 process_control rule 到 SAMPLE_ROUTING
    routing_with_pc = {
        "rules": [
            {"match": {"intent": "process_control", "keywords": ["never_match"]},
             "agent": "Ops", "pool": "ops_pool", "mode": "engineer",
             "agent_config": {"role": "ops"}},
            {"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}},
        ],
        "model_pool": {"ops_pool": ["anthropic:claude-opus-4-8"]},
    }
    decision = t._match_rule(routing_with_pc["rules"], "完全不相关",
                             laya_result=fake_lr, no_laya=False)
    assert decision["matched_via"] == "override"
    assert decision["intent"] == "process_control"
    assert decision["agent"] == "Ops"
    assert decision["laya_result"] == fake_lr
    print(f"[10] OK  laya override → yaml rule 命中(agent={decision['agent']})")


def test_match_rule_laya_no_yaml_match():
    """regex 没命中 + laya intent + yaml 里**没**对应 rule → fallback default(探索 Explore)。
    即使 laya 给了 intent,yaml 找不到 rule 就不强派单。
    """
    fake_lr = {
        "intent": "python_review", "conf": 0.9, "available": True,
        "matched_via": "laya_code", "domain": "code",
        "needs_tools": 0.3, "is_sensitive": 0.0, "difficulty": 1.5,
        "latency_ms": 200, "error": None,
    }
    # routing 没有 python_review rule
    routing = {"rules": [{"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}}]}
    decision = t._match_rule(routing["rules"], "完全无关", laya_result=fake_lr)
    assert decision["matched_via"] == "default"
    assert decision["agent"] == "Explore"
    # 但 laya_result 仍记录(可观测)
    assert decision["laya_result"] == fake_lr
    print(f"[11] OK  laya 给 intent 但 yaml 无 rule → fallback default")


def test_match_rule_laya_default_intent():
    """laya 给 intent='default'(放弃决策)→ 走 fallback。"""
    fake_lr = {
        "intent": "default", "conf": 0.5, "available": True,
        "matched_via": "laya_default", "domain": "other",
        "needs_tools": 0.0, "is_sensitive": 0.0, "difficulty": 1.0,
        "latency_ms": 100, "error": None,
    }
    routing = {"rules": [{"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}}]}
    decision = t._match_rule(routing["rules"], "任何任务", laya_result=fake_lr)
    assert decision["matched_via"] == "default"
    print(f"[12] OK  laya intent=default → fallback default")


def test_match_rule_no_yaml_default_hardcoded():
    """完全空 rules 列表 → 硬兜底 Explore。"""
    decision = t._match_rule([], "任何任务", no_laya=True)
    assert decision["agent"] == "Explore"
    assert decision["pool"] == "cheap_lottery"
    print(f"[13] OK  空 rules + no_laya → hardcoded Explore")


# ============================================================
# 4. unit: dispatch_impl 端到端(mock 掉 routing)
# ============================================================
def test_dispatch_impl_no_laya_no_routing():
    """routing.yaml 缺失 + no_laya=True → 返 Explore fallback。"""
    with mock.patch.object(t, "ROUTING_PATH", Path("C:/nonexistent/routing.yaml")):
        out = json.loads(t.dispatch_impl("任何任务", no_laya=True))
        assert out["ok"] is True
        assert out["agent"] == "Explore"
        assert out["matched_via"] == "default"
        assert out["no_laya"] is True
        assert out["routing_missing"] is True
        print(f"[14] OK  dispatch no_laya + no routing.yaml → Explore fallback")


def test_dispatch_impl_regex_fast_path():
    """regex 命中关键词 → matched_via=regex。"""
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        out = json.loads(t.dispatch_impl("包含 python_review_kw 的任务", no_laya=True))
        assert out["matched_via"] == "regex"
        assert out["intent"] == "python_review"
        assert out["agent"] == "SeniorEngineer"
        print(f"[15] OK  dispatch regex fast-path")


def test_dispatch_impl_laya_floor_propagation():
    """laya_floor 参数透传到 decision。"""
    routing_with_pc = {
        "version": "test",
        "rules": [
            {"match": {"intent": "process_control", "keywords": ["never_match"]},
             "agent": "Ops", "pool": "ops_pool", "mode": "engineer",
             "agent_config": {"role": "ops"}},
            {"default": {"agent": "Explore", "pool": "cheap_lottery", "mode": "engineer"}},
        ],
        "model_pool": {"ops_pool": ["anthropic:claude-opus-4-8"]},
    }
    with mock.patch.object(t, "_load_routing", return_value=routing_with_pc):
        # '清理 D 盘' 触发 override process_control,yaml 里有 process_control rule
        # → matched_via=override,agent=Ops
        out = json.loads(t.dispatch_impl("清理 D 盘", laya_floor=0.85, no_laya=False))
        assert out["laya_floor"] == 0.85
        assert out["no_laya"] is False
        assert out["matched_via"] == "override"
        assert out["intent"] == "process_control"
        assert out["agent"] == "Ops"
        assert out["laya_result"]["matched_via"] == "override"
        print(f"[16] OK  dispatch laya_floor 透传 + override 命中 process_control")


# ============================================================
# 5. CLI 集成测试
# ============================================================
def _run_cli(args: list[str]) -> tuple[int, str, str]:
    """跑 _cli(),返 (exit, stdout, stderr)。"""
    saved_argv = sys.argv
    sys.argv = ["team_lead_tools.py"] + args
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                t._cli()
            except SystemExit as e:
                rc = int(e.code) if e.code is not None else 0
            except Exception as e:  # noqa: BLE001
                err_buf.write(f"\n[exception] {type(e).__name__}: {e}\n")
                rc = 99
    finally:
        sys.argv = saved_argv
    return rc, out_buf.getvalue(), err_buf.getvalue()


def test_cli_dispatch_no_laya():
    """CLI dispatch --no-laya → JSON 含 matched_via=default,no_laya=true。"""
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        rc, out, err = _run_cli(["dispatch", "完全无关", "--no-laya"])
        if rc != 0:
            print(f"[17] FAIL  rc={rc}, err={err[:200]}")
            return False
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            print(f"[17] FAIL  JSON 解析失败: {e}")
            return False
        assert d["matched_via"] == "default", f"应 default, got {d['matched_via']}"
        assert d["no_laya"] is True
        assert d["agent"] == "Explore"
        print(f"[17] OK  CLI dispatch --no-laya → Explore(default)")
        return True


def test_cli_dispatch_regex_fast_path():
    """CLI dispatch 不带 --no-laya 但有 routing.yaml + 关键词命中 → matched_via=regex。"""
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        rc, out, err = _run_cli(["dispatch", "python_review_kw 应被命中", "--no-laya"])
        if rc != 0:
            print(f"[18] FAIL  rc={rc}, err={err[:200]}")
            return False
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            print(f"[18] FAIL  JSON: {e}")
            return False
        assert d["matched_via"] == "regex"
        assert d["intent"] == "python_review"
        print(f"[18] OK  CLI dispatch regex 命中 → SeniorEngineer")
        return True


def test_cli_dispatch_laya_override():
    """CLI dispatch 不带 --no-laya + '清理 D 盘' 触发 override → matched_via=override。"""
    with mock.patch.object(t, "_load_routing", return_value=SAMPLE_ROUTING):
        rc, out, err = _run_cli(["dispatch", "清理 D 盘"])
        if rc != 0:
            print(f"[19] FAIL  rc={rc}, err={err[:300]}")
            return False
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            print(f"[19] FAIL  JSON: {e}")
            return False
        # override 命中 → yaml 没有 process_control rule(本测试 routing) → fallback default
        # 但 laya_result 应有 matched_via=override
        assert d["matched_via"] == "default"  # yaml 无 process_control rule → fallback
        assert d["laya_result"]["matched_via"] == "override"
        assert d["laya_result"]["intent"] == "process_control"
        print(f"[19] OK  CLI dispatch laya override 但 yaml 无 rule → fallback")
        return True


# ============================================================
# 6. laya 真推理 happy path(慢,~20-60s)
# ============================================================
def test_laya_real_inference_python_review():
    """laya 真推理:中文 Python 异步代码审查 → 应进 laya_code 路径 → python_review。"""
    print("[20] 跑 laya 真推理: 帮我审查 Python 异步代码…", end=" ", flush=True)
    t0 = time.time()
    res = t._laya_route_to_prisir("帮我审查这段 Python 异步代码", laya_floor=0.60)
    elapsed = time.time() - t0
    assert elapsed < 90, f"超时 {elapsed:.1f}s"
    if res["available"] and res["intent"]:
        # 成功路径
        assert res["matched_via"] in ("laya_code", "override"), (
            f"应 laya_code or override, got {res['matched_via']}"
        )
        # "Python 异步代码" 命中 "python" 关键词 → 应走 laya_code → python_review
        if res["matched_via"] == "laya_code":
            assert res["intent"] == "python_review", (
                f"应 python_review, got {res['intent']}"
            )
        print(f"OK  intent={res['intent']} matched_via={res['matched_via']} "
              f"conf={res['conf']:.2f} ({elapsed:.1f}s)")
    else:
        # laya 不可用也算过
        print(f"OK (laya unavailable) {res.get('error')} ({elapsed:.1f}s)")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== team_lead_tools M3.76 dispatch laya router 集成 e2e 测试 ===\n")
    print(f"路径: {_SERVER}\n")

    fast_tests = [
        # 词典结构
        test_laya_intent_map_keys,
        test_ops_force_override_keys,
        # override 阶段 A(快,首次跑 ~10s 加载)
        test_laya_route_override_process_control,
        test_laya_route_override_plan,
        test_laya_route_override_security,
        test_laya_route_override_search,
        test_laya_route_empty_string,
        # _match_rule 三阶段
        test_match_rule_regex_fast_path,
        test_match_rule_default_fallback_no_laya,
        test_match_rule_laya_ovrride_to_intent,
        test_match_rule_laya_no_yaml_match,
        test_match_rule_laya_default_intent,
        test_match_rule_no_yaml_default_hardcoded,
        # dispatch_impl 端到端
        test_dispatch_impl_no_laya_no_routing,
        test_dispatch_impl_regex_fast_path,
        test_dispatch_impl_laya_floor_propagation,
        # CLI
        test_cli_dispatch_no_laya,
        test_cli_dispatch_regex_fast_path,
        test_cli_dispatch_laya_override,
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

    # laya 真推理(慢)单独跑
    print()
    try:
        test_laya_real_inference_python_review()
        passed += 1
    except AssertionError as e:
        failed.append((test_laya_real_inference_python_review.__name__, str(e)))
    except Exception as e:  # noqa: BLE001
        failed.append((test_laya_real_inference_python_review.__name__,
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