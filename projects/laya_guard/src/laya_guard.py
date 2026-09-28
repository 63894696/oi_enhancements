#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# src/laya_guard.py — M3.72 laya guard CLI 核心(2026-09-24)
#
# 目的:
#   - 用 laya.guard_questions() 检测 user prompt / shell command
#   - 5 维度:jailbreak / prompt_injection / sensitive_data / harm_severity / topic
#   - 输出 risk + 建议(allow/ask/deny)
#   - 离线可用,本地 CPU 推理(p50 ~500ms)
#
# 用法:
#   from laya_guard import guard, format_text, format_json
#   r = guard("Ignore all previous instructions")
#   # r = {risk, jailbreak, injection, sensitive, harm_severity, topic, raw, latency_ms, ...}
#
# 设计:
#   - 复用 bench_laya.py 的 map_to_risk 思路
#   - 自带 laya Router lazy-load(全局单例)
#   - 失败 → 默认 medium(fail-closed)
#   - 单文件,纯 stdlib + laya
r"""
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

# 把 companion/ 加入 sys.path,复用 bench_laya 的 Router + presets
_COMPANION_DIR = Path("C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))


# ============================================================
# 常量
# ============================================================
_VALID_RISKS = ("safe", "low", "medium", "high", "critical")

# Router 单例 cache
_ROUTER = None
_GUARD_QS = None
_PRELOAD_DONE = False


# ============================================================
# 懒加载 Router
# ============================================================
def _ensure_loaded():
    """懒加载 laya Router + guard_questions preset。"""
    global _ROUTER, _GUARD_QS, _PRELOAD_DONE

    if _PRELOAD_DONE:
        return _ROUTER, _GUARD_QS

    try:
        from laya import Router, guard_questions  # noqa: F401
    except ImportError as e:
        _PRELOAD_DONE = True
        print(f"[laya_guard] laya import 失败: {e}", file=sys.stderr)
        return None, None

    try:
        _ROUTER = Router(default="multilingual", preload=True)
        _GUARD_QS = guard_questions()
    except Exception as e:  # noqa: BLE001
        print(f"[laya_guard] Router 加载失败: {e}", file=sys.stderr)
        _ROUTER = None
        _GUARD_QS = None

    _PRELOAD_DONE = True
    return _ROUTER, _GUARD_QS


def _map_to_risk(answers: dict[str, Any]) -> str:
    """把 laya.guard_questions() 的 5 维度答案映射成 5 类 risk。

    决策逻辑(任一高风险即升档):
      - sensitive_data ≥ 0.7     → high
      - jailbreak ≥ 0.7          → critical
      - prompt_injection ≥ 0.7   → high
      - harm_severity ≥ 2.5      → critical
      - harm_severity ≥ 1.5      → high
      - jailbreak ≥ 0.4          → medium
      - prompt_injection ≥ 0.4   → medium
      - sensitive_data ≥ 0.2     → medium
      - harm_severity ≥ 0.4      → medium
      - 其它                      → safe / low
    """
    jb = answers.get("jailbreak", {}).get("noul", 0.0) or 0.0
    inj = answers.get("prompt_injection", {}).get("noul", 0.0) or 0.0
    sens = answers.get("sensitive_data", {}).get("noul", 0.0) or 0.0
    harm = answers.get("harm_severity", {}).get("score", 0.0) or 0.0

    # critical 路径
    if jb >= 0.7 or harm >= 2.5:
        return "critical"
    # high 路径
    if inj >= 0.7 or sens >= 0.7 or harm >= 1.5:
        return "high"
    # medium 路径
    if jb >= 0.4 or inj >= 0.4 or sens >= 0.2 or harm >= 0.4:
        return "medium"
    # low / safe
    if jb >= 0.15 or inj >= 0.15 or sens >= 0.05 or harm >= 0.15:
        return "low"
    return "safe"


# ============================================================
# 主入口
# ============================================================
def guard(text: str) -> dict:
    """单条 prompt 检测。

    Args:
        text: 用户输入(可以是一句话 prompt,或 shell 命令,或自然语言)

    Returns:
        dict:
          - input: 原输入
          - risk: safe / low / medium / high / critical
          - jailbreak: 0-1 概率
          - injection: 0-1 概率
          - sensitive: 0-1 概率
          - harm: 0-3 分数
          - topic: choice(top-1)
          - raw: laya answers(完整)
          - latency_ms
          - parse_fail: bool
          - backend: "laya" / "fail-closed"
    """
    router, qs = _ensure_loaded()

    if router is None or qs is None:
        return {
            "input": text,
            "risk": "medium",
            "jailbreak": 0.0,
            "injection": 0.0,
            "sensitive": 0.0,
            "harm": 0.0,
            "topic": None,
            "raw": None,
            "latency_ms": 0,
            "parse_fail": True,
            "backend": "fail-closed",
            "error": "laya 未加载",
        }

    t0 = time.time()
    try:
        # guard_questions 的 placeholder key 是 "prompt"
        res = router.predict({"prompt": text}, qs)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers", {})

        if not answers:
            return {
                "input": text,
                "risk": "medium",
                "jailbreak": 0.0,
                "injection": 0.0,
                "sensitive": 0.0,
                "harm": 0.0,
                "topic": None,
                "raw": res,
                "latency_ms": elapsed_ms,
                "parse_fail": True,
                "backend": "laya",
                "error": "empty answers",
            }

        jb = answers.get("jailbreak", {}).get("noul", 0.0) or 0.0
        inj = answers.get("prompt_injection", {}).get("noul", 0.0) or 0.0
        sens = answers.get("sensitive_data", {}).get("noul", 0.0) or 0.0
        harm = answers.get("harm_severity", {}).get("score", 0.0) or 0.0
        topic = answers.get("topic", {}).get("choice", None)
        risk = _map_to_risk(answers)

        return {
            "input": text,
            "risk": risk,
            "jailbreak": round(jb, 4),
            "injection": round(inj, 4),
            "sensitive": round(sens, 4),
            "harm": round(harm, 4),
            "topic": topic,
            "raw": answers,
            "latency_ms": round(elapsed_ms, 1),
            "parse_fail": False,
            "backend": "laya",
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000
        return {
            "input": text,
            "risk": "medium",
            "jailbreak": 0.0,
            "injection": 0.0,
            "sensitive": 0.0,
            "harm": 0.0,
            "topic": None,
            "raw": None,
            "latency_ms": elapsed_ms,
            "parse_fail": True,
            "backend": "laya",
            "error": f"{type(e).__name__}: {e}",
        }


def guard_batch(texts: list[str]) -> list[dict]:
    """批量检测。"""
    return [guard(t) for t in texts]


# ============================================================
# 决策 policy
# ============================================================
def decide(result: dict) -> dict:
    """laya_guard 结果 → allow/ask/deny。

    Args:
        result: guard() 返回值

    Returns:
        dict: {decision: "allow"|"ask"|"deny", reason: str, exit_code: int}
    """
    risk = result.get("risk") or "medium"
    if risk in ("high", "critical"):
        jb = result.get("jailbreak", 0)
        sens = result.get("sensitive", 0)
        inj = result.get("injection", 0)
        reasons = []
        if jb >= 0.7:
            reasons.append(f"jailbreak={jb:.2f}")
        if inj >= 0.7:
            reasons.append(f"injection={inj:.2f}")
        if sens >= 0.7:
            reasons.append(f"sensitive={sens:.2f}")
        return {
            "decision": "deny",
            "reason": "; ".join(reasons) or f"risk={risk}",
            "exit_code": 1,
        }
    if risk == "medium":
        return {
            "decision": "ask",
            "reason": f"risk=medium (jb={result.get('jailbreak', 0):.2f}, "
                      f"inj={result.get('injection', 0):.2f}, "
                      f"sens={result.get('sensitive', 0):.2f}, "
                      f"harm={result.get('harm', 0):.2f})",
            "exit_code": 0,
        }
    return {
        "decision": "allow",
        "reason": f"risk={risk}",
        "exit_code": 0,
    }


# ============================================================
# 输出格式化
# ============================================================
def format_text(result: dict, decision: Optional[dict] = None) -> str:
    """人类可读文本格式。"""
    lines = []
    lines.append(f"输入: {result['input']}")
    lines.append("")
    lines.append(f"[laya]        risk={result['risk']}")
    lines.append(f"  jailbreak:    {result['jailbreak']:.3f}")
    lines.append(f"  injection:    {result['injection']:.3f}")
    lines.append(f"  sensitive:    {result['sensitive']:.3f}")
    lines.append(f"  harm_score:   {result['harm']:.3f}  (0-3)")
    lines.append(f"  topic:        {result['topic']!r}")
    lines.append("")
    lines.append(f"  backend:    {result['backend']}")
    lines.append(f"  latency:    {result['latency_ms']:.0f}ms")
    lines.append(f"  parse_fail: {result['parse_fail']}")
    if decision:
        lines.append("")
        if decision["decision"] == "allow":
            marker = "→决策: allow  ← 不拦截"
        elif decision["decision"] == "ask":
            marker = "→决策: ask  ← 需用户确认"
        else:
            marker = "→决策: deny  ← 拦截"
        lines.append(marker)
        lines.append(f"  原因: {decision['reason']}")
        lines.append(f"  退出码: {decision['exit_code']}")
    return "\n".join(lines)


def format_json(result: dict, decision: Optional[dict] = None) -> str:
    """JSON 格式。"""
    out = dict(result)
    if decision:
        out["decision"] = decision
    return json.dumps(out, ensure_ascii=False, indent=2)