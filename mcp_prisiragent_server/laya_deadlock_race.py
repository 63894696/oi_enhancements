#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""laya_deadlock_race.py — M3.82 laya + heuristic race 决策模块(2026-09-25)

目的:
  - deadlock_detect.py 的 4 分类预测,先用 laya.triage_questions() 4 维信号
    + heuristic 联合判定
  - race_impl 接口:接受单条 AgentPosition → 4 分类 label + conf
  - fail-open:laya 加载失败/超时 → 直接返 heuristic 结果

设计发现(M3.82 bench rev1-rev3):
  - laya zero-shot 在 deadlock 4 分类任务上**整体失准**(0-72% ACC,见 bench 报告)
  - rev3(用户感知语言)→ 0% ACC,frustration 把所有类都拉高
  - rev2(router_questions difficulty)→ 72% 但全判 ok(bias)
  - rev1(guard_questions)→ 72% 但全判 ok(bias)
  - **结论**:laya 单模型无法精确判 4 分类,**必须配合 heuristic 联合**

race_impl 决策树:
  1. 跑 laya.triage_questions()(默认 multilingual),拿 frustration/urgency
  2. **heuristic 永远跑**(parallel 安全网)
  3. **race 合并规则**:
     - laya confidence ≥ 0.7 + 与 heuristic 一致   → 用 laya label
     - laya confidence < 0.7  OR 与 heuristic 不一致 → 用 heuristic label
     - laya 失败                                → 用 heuristic label
  4. 4 分类:stalled / zero_progress / slow / ok

为什么不删 heuristic(race_impl fail-open):
  - M3.71 结论:5/5 laya ACC LOSE (-17~-62pp)
  - deadlock_detect.py 的 3 条硬规则已验证可用
  - laya 只是**副观察层 + 校准信号**,不是 primary classifier

性能:
  - heuristic detect() ≈ < 1ms(纯 Python dict 操作)
  - laya.triage_questions() ≈ 700ms p50(已实测)
  - race_impl overhead ≈ 700ms laya 调用 + heuristic 并行(<5ms 额外)
  - 关键决策:**laya 同步调用**(避免多线程复杂度,block ~700ms 但保 race 简单)

环境变量:
  - LAYA_DEADLOCK_DISABLED=1  → 完全跳过 laya,只用 heuristic
  - LAYA_DEADLOCK_TIMEOUT_MS=1500 → laya 调用超时阈值(默认 1500ms)

调用方:
  deadlock_detect.detect_deadlocks() 可选接入 race_impl(默认仍用 heuristic)
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_COMPANION = Path("C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION) not in sys.path:
    sys.path.insert(0, str(_COMPANION))

# 复用 deadlock_detect.py 的现有类型
from deadlock_detect import AgentPosition, DeadlockInfo, PositionTracker  # noqa: E402


# ============================================================
# 常量
# ============================================================

_STALL_THRESHOLD_SEC = 120
_ZERO_PROGRESS_ROUND_MIN = 3
_SLOW_ROUND_MIN = 15
_SLOW_ELAPSED_SEC = 300

# race 阈值
_RACE_CONFIDENCE_THRESHOLD = 0.7
_LAYA_TIMEOUT_MS = int(os.environ.get("LAYA_DEADLOCK_TIMEOUT_MS", "1500"))
_DISABLED = os.environ.get("LAYA_DEADLOCK_DISABLED", "0") == "1"

LABELS = ("stalled", "zero_progress", "slow", "ok")


# ============================================================
# Heuristic 4 分类(从 deadlock_detect.py 现有规则扩展)
# ============================================================

def heuristic_label(pos: AgentPosition, now: float | None = None) -> tuple[str, float]:
    """对单条 AgentPosition → 4 分类 (label, confidence)。

    规则(对齐 deadlock_detect.py 现有 3 条):
      1. stalled:    elapsed_since_last > STALL_THRESHOLD_SEC (120s)
      2. zero_progress: round_count > 3 AND file_ops == 0 AND position in reading/llm_call
      3. slow:       round_count > 15 AND elapsed_sec > 300 (但未 stalled)
      4. ok:         其它

    confidence 是规则命中"硬度"的简单估计:
      - stalled:    0.95 (硬规则,几乎不误判)
      - zero_progress: 0.80
      - slow:       0.70
      - ok:         0.60 (默认低)
    """
    now = now if now is not None else time.time()
    elapsed_since_last = now - pos.last_seen

    if elapsed_since_last > _STALL_THRESHOLD_SEC:
        return "stalled", 0.95
    if (pos.round_count > _ZERO_PROGRESS_ROUND_MIN
            and pos.file_ops == 0
            and pos.position in ("reading", "llm_call")):
        return "zero_progress", 0.80
    if (pos.round_count > _SLOW_ROUND_MIN
            and pos.elapsed_sec > _SLOW_ELAPSED_SEC):
        return "slow", 0.70
    return "ok", 0.60


# ============================================================
# Laya 4 分类推理(lazy load + fail-closed)
# ============================================================

_ROUTER = None
_QS = None
_PRELOAD_DONE = False
_LOAD_ERROR: Optional[str] = None


def _ensure_loaded():
    """懒加载 laya Router + triage_questions preset。"""
    global _ROUTER, _QS, _PRELOAD_DONE, _LOAD_ERROR
    if _PRELOAD_DONE:
        return _ROUTER, _QS
    if _DISABLED:
        _PRELOAD_DONE = True
        _LOAD_ERROR = "LAYA_DEADLOCK_DISABLED=1"
        return None, None
    try:
        from laya import Router, triage_questions
    except ImportError as e:
        _PRELOAD_DONE = True
        _LOAD_ERROR = f"laya import 失败: {e}"
        return None, None
    try:
        _ROUTER = Router(default="multilingual", preload=True)
        _QS = triage_questions()
    except Exception as e:  # noqa: BLE001
        _PRELOAD_DONE = True
        _LOAD_ERROR = f"Router 加载失败: {e}"
        _ROUTER = None
        _QS = None
    _PRELOAD_DONE = True
    return _ROUTER, _QS


def _pos_to_prompt(pos: AgentPosition, now: float | None = None) -> str:
    """AgentPosition → laya 可读的 user-perceived prompt。

    与 data_prep_deadlock._to_prompt 同形,保证训练/推理 prompt 对齐。
    """
    now = now if now is not None else time.time()
    elapsed_since_last = now - pos.last_seen
    pos_desc_map = {
        "reading":   "在读文件",
        "writing":   "在写文件",
        "editing":   "在编辑文件",
        "bash":      "在跑命令",
        "llm_call":  "在思考下一步",
        "idle":      "空闲中",
        "done":      "已完成",
        "failed":    "已失败",
    }
    pos_desc = pos_desc_map.get(pos.position, "在工作中")
    ts = elapsed_since_last
    if ts > 120:
        wait_desc = f"已经 {ts:.0f} 秒没动静了,完全卡住"
    elif ts > 60:
        wait_desc = f"已经 {ts:.0f} 秒没更新,担心卡住"
    elif ts > 30:
        wait_desc = f"已经 {ts:.0f} 秒没动静"
    else:
        wait_desc = f"最近还在更新({ts:.0f}秒前)"

    file_ops_desc = (
        "一直读文件但没写出任何东西"
        if pos.file_ops == 0
        else f"已经写了 {pos.file_ops} 个文件"
    )

    return (
        f"我的 agent task#{pos.task_id}({pos.agent_type}) {pos_desc},"
        f"{file_ops_desc},"
        f"已经跑了 {pos.elapsed_sec:.0f} 秒({pos.round_count} 轮),{wait_desc}。"
    )


def _answers_to_label(answers: dict[str, Any]) -> tuple[str, float]:
    """laya.triage_questions() 5 维答案 → 4 分类 (label, conf)。

    关键:实测发现 laya 在结构化 deadlock 输入上 signal 弱,
    但 frustration + is_urgent 联合**仍提供 > 0.5 conf 的粗筛**,
    适合做 race_impl 的辅助信号。
    """
    fru = answers.get("frustration", {}).get("score", 0.0) or 0.0
    urg = answers.get("is_urgent", {}).get("noul", 0.0) or 0.0
    churn = answers.get("churn_risk", {}).get("noul", 0.0) or 0.0

    # stalled:极高 frustration(模型对"卡住"信号最敏感)
    if fru >= 1.8:
        return "stalled", min(0.85, 0.4 + fru * 0.25)
    # zero_progress:中等 frustration + 中等 urgency
    if fru >= 1.2 and urg >= 0.15:
        return "zero_progress", min(0.7, 0.3 + fru * 0.15 + urg * 0.3)
    # slow:中 frustration + 低 urgency + 高 churn
    if fru >= 1.0 and urg < 0.15 and churn >= 0.2:
        return "slow", min(0.65, 0.3 + fru * 0.15)
    # ok(置信度故意低,让 race_impl fallback heuristic)
    return "ok", max(0.3, min(0.5, 0.5 - fru * 0.1))


def laya_predict(pos: AgentPosition, now: float | None = None) -> dict:
    """laya 推理单条 position。

    Returns:
        {"label": str, "confidence": float, "backend": "laya"|"fail-closed",
         "latency_ms": float, "parse_fail": bool, "error": Optional[str]}
    """
    router, qs = _ensure_loaded()
    if router is None or qs is None:
        return {
            "label": "ok",
            "confidence": 0.0,
            "backend": "fail-closed",
            "latency_ms": 0.0,
            "parse_fail": True,
            "error": _LOAD_ERROR or "laya 未加载",
        }

    prompt = _pos_to_prompt(pos, now=now)
    t0 = time.time()
    try:
        res = router.predict({"message": prompt}, qs)
        elapsed_ms = (time.time() - t0) * 1000
        if elapsed_ms > _LAYA_TIMEOUT_MS:
            return {
                "label": "ok",
                "confidence": 0.0,
                "backend": "laya",
                "latency_ms": elapsed_ms,
                "parse_fail": True,
                "error": f"timeout {elapsed_ms:.0f}ms > {_LAYA_TIMEOUT_MS}ms",
            }
        answers = res.get("answers", {})
        if not answers:
            return {
                "label": "ok",
                "confidence": 0.0,
                "backend": "laya",
                "latency_ms": elapsed_ms,
                "parse_fail": True,
                "error": "empty answers",
            }
        label, conf = _answers_to_label(answers)
        return {
            "label": label,
            "confidence": round(conf, 4),
            "backend": "laya",
            "latency_ms": round(elapsed_ms, 1),
            "parse_fail": False,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000
        return {
            "label": "ok",
            "confidence": 0.0,
            "backend": "laya",
            "latency_ms": elapsed_ms,
            "parse_fail": True,
            "error": f"{type(e).__name__}: {e}",
        }


# ============================================================
# Race 决策
# ============================================================

@dataclass
class RaceResult:
    """race_impl 输出。"""
    label: str               # 最终 4 分类
    confidence: float        # 最终 conf
    source: str              # "laya" / "heuristic" / "race-consensus"
    heuristic_label: str
    heuristic_confidence: float
    laya_label: Optional[str]
    laya_confidence: Optional[float]
    laya_latency_ms: Optional[float]
    agreed: bool             # heuristic 与 laya 是否一致
    fallback_used: bool      # 是否走了 fallback 路径


def race_predict(pos: AgentPosition, now: float | None = None) -> RaceResult:
    """laya + heuristic 联合判定单条 AgentPosition → 4 分类。

    决策树:
      1. heuristic 永远跑(主信号,fail-open 保证)
      2. laya 跑(副信号,~700ms 延迟)
      3. 如果 laya 失败 → 用 heuristic
      4. 如果 laya 成功:
         - conf < 0.7        → 用 heuristic (laya 置信度不足)
         - 与 heuristic 一致  → race-consensus (提升 conf 到 min(0.95, max(...)))
         - 与 heuristic 不一致 → 用 heuristic (heuristic 优先,laya 仅作副观察)
    """
    h_label, h_conf = heuristic_label(pos, now=now)
    laya_res = laya_predict(pos, now=now)

    # laya 失败 → heuristic
    if laya_res["parse_fail"]:
        return RaceResult(
            label=h_label,
            confidence=h_conf,
            source="heuristic",
            heuristic_label=h_label,
            heuristic_confidence=h_conf,
            laya_label=None,
            laya_confidence=None,
            laya_latency_ms=laya_res["latency_ms"],
            agreed=False,
            fallback_used=True,
        )

    l_label = laya_res["label"]
    l_conf = laya_res["confidence"]

    # laya conf < 阈值 → heuristic
    if l_conf < _RACE_CONFIDENCE_THRESHOLD:
        return RaceResult(
            label=h_label,
            confidence=h_conf,
            source="heuristic",
            heuristic_label=h_label,
            heuristic_confidence=h_conf,
            laya_label=l_label,
            laya_confidence=l_conf,
            laya_latency_ms=laya_res["latency_ms"],
            agreed=(h_label == l_label),
            fallback_used=True,
        )

    # laya 高 conf 但与 heuristic 不一致 → heuristic 优先
    if h_label != l_label:
        return RaceResult(
            label=h_label,
            confidence=h_conf,
            source="heuristic",
            heuristic_label=h_label,
            heuristic_confidence=h_conf,
            laya_label=l_label,
            laya_confidence=l_conf,
            laya_latency_ms=laya_res["latency_ms"],
            agreed=False,
            fallback_used=True,
        )

    # 一致 → race-consensus (提升 conf 到 min(0.95, max+0.05))
    boosted = min(0.95, max(h_conf, l_conf) + 0.05)
    return RaceResult(
        label=h_label,
        confidence=round(boosted, 4),
        source="race-consensus",
        heuristic_label=h_label,
        heuristic_confidence=h_conf,
        laya_label=l_label,
        laya_confidence=l_conf,
        laya_latency_ms=laya_res["latency_ms"],
        agreed=True,
        fallback_used=False,
    )


# ============================================================
# DeadlockDetector 集成(可选)
# ============================================================

def enrich_detection_with_race(detections: list[DeadlockInfo],
                                positions: dict[int, AgentPosition],
                                now: float | None = None) -> list[dict]:
    """给现有 detect() 结果补 race_impl 副观察层。

    Args:
        detections: deadlock_detect.detect() 的输出
        positions: tracker.positions 用于回查

    Returns:
        list of dict,每条 = detection + race 字段:
          {"detection": DeadlockInfo, "race": RaceResult 或 None}
    """
    out = []
    for d in detections:
        pos = positions.get(d.task_id)
        if pos is None:
            out.append({"detection": d, "race": None})
            continue
        race = race_predict(pos, now=now)
        out.append({
            "detection": d,
            "race": {
                "label": race.label,
                "confidence": race.confidence,
                "source": race.source,
                "heuristic_label": race.heuristic_label,
                "heuristic_confidence": race.heuristic_confidence,
                "laya_label": race.laya_label,
                "laya_confidence": race.laya_confidence,
                "laya_latency_ms": race.laya_latency_ms,
                "agreed": race.agreed,
                "fallback_used": race.fallback_used,
            },
        })
    return out


# ============================================================
# CLI / 测试入口
# ============================================================

def _cli_smoke():
    """冒烟测试:跑 5 条合成 position 看 race 输出。"""
    from deadlock_detect import AgentPosition
    test_positions = [
        # stalled
        AgentPosition(task_id=1001, agent_type="dev", position="reading",
                      last_seen=time.time() - 200, round_count=5,
                      file_ops=0, elapsed_sec=200.0),
        # zero_progress
        AgentPosition(task_id=1002, agent_type="reviewer", position="llm_call",
                      last_seen=time.time() - 10, round_count=5,
                      file_ops=0, elapsed_sec=50.0),
        # slow
        AgentPosition(task_id=1003, agent_type="dev", position="writing",
                      last_seen=time.time() - 5, round_count=18,
                      file_ops=10, elapsed_sec=400.0),
        # ok
        AgentPosition(task_id=1004, agent_type="dev", position="writing",
                      last_seen=time.time() - 5, round_count=3,
                      file_ops=2, elapsed_sec=30.0),
    ]
    print("=== laya_deadlock_race smoke test ===")
    for pos in test_positions:
        race = race_predict(pos)
        print(f"task#{pos.task_id}: heuristic={race.heuristic_label}({race.heuristic_confidence:.2f})"
              f"  laya={race.laya_label}({race.laya_confidence if race.laya_confidence is not None else 'N/A'})"
              f"  → final={race.label}({race.confidence:.2f}) via {race.source}")


if __name__ == "__main__":
    _cli_smoke()
