# -*- coding: utf-8 -*-
# learning_progress.py — 教学进度追踪(2026-09-05 P2)
#
# 记录 quiz 作答结果(主题/对错/时间),沉淀本地 learning_progress.json,
# 下次对话注入「掌握度摘要」让 AI 自适应教学(已会的跳过、薄弱的多练)。
#
# 数据流:web 端 quiz 卡片作答 → POST /api/quiz_result → record_result()
#   → 存 {topic, question, correct, ts};AI 出题时在 quiz JSON 里带 topic 字段。
#
# 红线对齐:纯本地 json(与 user_profile 同目录);无 LLM 成本(记录是
# 确定性落盘,摘要注入是字符串拼接);用户可看可清。
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_MAX_RECORDS = 500       # 总量上限,防膨胀(超出丢最旧)
_SUMMARY_TOPICS = 8      # 摘要里最多列几个主题
_RECENT_WINDOW = 20      # 掌握度按最近 N 条算


def _progress_path() -> Path:
    root = os.environ.get("PRISIR_DATA_DIR") or str(Path.home() / ".local" / "share" / "prisir")
    return Path(root) / "learning_progress.json"


def load_records() -> list[dict]:
    """读全部作答记录 [{topic,question,correct,ts},...]。失败返回 []。"""
    try:
        p = _progress_path()
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(records: list[dict]) -> None:
    try:
        p = _progress_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def record_result(topic: str, question: str, correct: bool) -> bool:
    """落一条作答记录。topic 空则归 '未分类'。返回是否成功。"""
    try:
        records = load_records()
        records.append({
            "topic": (topic or "").strip()[:40] or "未分类",
            "question": (question or "").strip()[:120],
            "correct": bool(correct),
            "ts": time.time(),
        })
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]
        _save(records)
        return True
    except Exception:  # noqa: BLE001
        return False


def topic_stats() -> dict[str, dict]:
    """按主题聚合:{topic: {total, correct, recent_correct, recent_total}}。

    recent_* 只算该主题最近 _RECENT_WINDOW 条,反映当前掌握度而非历史总量。
    """
    stats: dict[str, dict] = {}
    for r in load_records():
        t = r.get("topic") or "未分类"
        s = stats.setdefault(t, {"total": 0, "correct": 0,
                                 "_recent": []})
        s["total"] += 1
        if r.get("correct"):
            s["correct"] += 1
        s["_recent"].append(bool(r.get("correct")))
    for t, s in stats.items():
        recent = s.pop("_recent")[-_RECENT_WINDOW:]
        s["recent_total"] = len(recent)
        s["recent_correct"] = sum(recent)
    return stats


def mastery_block() -> str:
    """掌握度摘要,注入 system prompt。无记录返回空串(不占上下文)。

    格式(对齐 user_profile.profile_block 风格):
      【学习进度(最近作答)】
      - Python 基础: 15 题对 12(近期 80%)
      - 数据结构: 6 题对 2(近期 33%)← 薄弱,建议多练
    """
    stats = topic_stats()
    if not stats:
        return ""
    # 按题量排序,取前 N 个主题
    rows = sorted(stats.items(), key=lambda kv: kv[1]["total"], reverse=True)
    rows = rows[:_SUMMARY_TOPICS]
    lines = ["【学习进度(quiz 作答统计,供自适应教学参考)】"]
    for topic, s in rows:
        pct = round(100 * s["correct"] / s["total"]) if s["total"] else 0
        rpct = (round(100 * s["recent_correct"] / s["recent_total"])
                if s["recent_total"] else 0)
        weak = "  ← 薄弱,优先出题巩固" if rpct < 60 and s["recent_total"] >= 3 else ""
        lines.append(
            f"- {topic}: 共 {s['total']} 题对 {s['correct']}({pct}%),"
            f"近期 {s['recent_total']} 题 {rpct}%{weak}")
    return "\n".join(lines)


def clear() -> bool:
    """清空全部进度(用户主动重置)。文件删除即清。"""
    try:
        p = _progress_path()
        if p.is_file():
            p.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False
