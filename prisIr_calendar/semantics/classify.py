# -*- coding: utf-8 -*-
"""
classify.py — 跳过规则 (task #4)

核心契约 (travel-time-buddy.md):
    - 删除 = 政策 (永不重做) → dismissed 已由 store 处理, 此处不重判
    - 跳过规则: video_only / all_day_soft_hold / flight_hotel / no_venue / vague / same_workplace

优先级:
    should_skip_event 只看事件本身的"语义属性", 不看时间窗上下文.
    工作场所 (workplace) 判定由 caller (scan_chain / buffer 调度) 注入.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .chain import Event
from .venue import Venue, is_workplace_diff

log = logging.getLogger("prisIr_calendar.semantics.classify")


# ============================================================
# 关键词清单 (拍板决策, task #4)
# ============================================================
VIDEO_ONLY_KEYWORDS = frozenset({
    "zoom",
    "腾讯会议",
    "teams",
    "微信语音",
    "电话",
    # 额外稳妥补充 (对齐产品边界常见的视频会议载体)
    "voov",
    "vooV",
    "腾讯会议",
    "钉钉",
    "飞书",
    "google meet",
    "meet.google",
})

FLIGHT_HOTEL_KEYWORDS = frozenset({
    "flight",
    "航班",
    "飞机",
    "机场",
    "hotel",
    "酒店",
    "airbnb",
    # 补充: 实际对话里用户会说 "飞"
    "飞",
    "值机",
    "登机",
    "check-in",
    "check in",
    "入住",
    "退房",
    "checkout",
})

VAGUE_KEYWORDS = frozenset({
    "downtown",
    "市中心",
    "附近",
    "城区",
    "城里",
    "市区内",
    "随便",
    "某处",
    "city center",
    "downtown area",
    # 补充: 实际对话里用户会说
    "附近",
    "周边",
    "那一带",
    "anywhere",
    "somewhere",
})

# 全天事件阈值: 持续 >= 8h 视为"全天占位"(如外出/休息)
ALL_DAY_MIN_HOURS = 8


# 全部 skip reason (供 task #5 / 前端展示)
SKIP_REASONS = frozenset({
    "video_only",
    "all_day_soft_hold",
    "flight_hotel",
    "no_venue",
    "vague",
    "same_workplace",
})


# ============================================================
# helpers
# ============================================================
def _text_blob(ev: Event) -> str:
    """事件的可搜文本: summary + description. None 视为空串."""
    s = ev.summary or ""
    d = ev.description or ""
    return f"{s}\n{d}".lower()


def _has_any(text: str, kws) -> bool:
    """任一关键词命中(大小写不敏感, 子串匹配)."""
    for kw in kws:
        if kw and kw.lower() in text:
            return True
    return False


def _parse_iso(s: str):
    """轻量 ISO8601 解析, 失败返 None. 复用 chain._parse_iso."""
    from .chain import _parse_iso as _chain_parse
    return _chain_parse(s)


def _is_all_day(ev: Event) -> bool:
    """全天事件判定: 跨午 + 持续 >= ALL_DAY_MIN_HOURS.

    简化规则 (Phase 1):
        - dtstart 当天 00:00 本地 (Asia/Shanghai) 且持续 >= 8h
        - 或 dtstart 当天上午且 dtend 次日凌晨 (跨午夜)
    """
    s = _parse_iso(ev.dtstart_utc)
    e = _parse_iso(ev.dtend_utc)
    if s is None or e is None:
        return False

    from datetime import timedelta
    duration_hours = (e - s).total_seconds() / 3600.0
    if duration_hours < ALL_DAY_MIN_HOURS:
        return False

    # 跨午判定 (Asia/Shanghai): 当天 0:00 - 23:59 本地时间
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    local_start = s.astimezone(tz)
    return (
        local_start.hour == 0
        and local_start.minute == 0
        and duration_hours >= ALL_DAY_MIN_HOURS
    )


# ============================================================
# 公开 API
# ============================================================
def should_skip_event(
    ev: Event,
    *,
    workplace: Optional[Dict[str, Optional[str]]] = None,
) -> Tuple[bool, str]:
    """判定事件是否应跳过 (不入 buffer 调度).

    返回 (skip, reason):
        - (False, "") = 不跳过, 正常 in-person
        - (True, "<reason>") = 跳过, reason ∈ SKIP_REASONS

    跳过优先级 (按文档顺序, 命中即返回):
        1. video_only       — 标题/描述含视频会议关键词
        2. all_day_soft_hold — 全天事件
        3. flight_hotel      — 飞行/酒店关键词
        4. no_venue          — 无 venue 或 normalized 为空
        5. vague             — venue 模糊关键词
        6. same_workplace    — venue 与 workplace 同

    Args:
        ev: 语义层 Event
        workplace: {office: str|None, home: str|None}. None 或 office=None 时
                  同 workplace 判定视为不可比, 不触发 same_workplace skip.

    注意:
        - dismissed 由 store 处理 (list_events 默认过滤), 此函数不重复判
        - workplace 不传 / 空 → 跳过 same_workplace 检查
    """
    # 1) video_only
    if _has_any(_text_blob(ev), VIDEO_ONLY_KEYWORDS):
        return True, "video_only"

    # 2) all_day
    if _is_all_day(ev):
        return True, "all_day_soft_hold"

    # 3) flight_hotel
    if _has_any(_text_blob(ev), FLIGHT_HOTEL_KEYWORDS):
        return True, "flight_hotel"

    # 4) no_venue
    if ev.venue is None or not (ev.venue.normalized or "").strip():
        return True, "no_venue"

    # 5) vague — venue normalized 含模糊关键词
    if _has_any((ev.venue.normalized or "").lower(), VAGUE_KEYWORDS):
        return True, "vague"

    # 6) same_workplace — 仅当 caller 传了 workplace 时判
    if workplace:
        office = (workplace.get("office") or "").strip()
        home = (workplace.get("home") or "").strip()
        # 至少有一个 workplace 字段才有意义
        if office or home:
            if not is_workplace_diff(ev.venue, workplace):
                return True, "same_workplace"

    return False, ""


__all__ = [
    "should_skip_event",
    "SKIP_REASONS",
    "ALL_DAY_MIN_HOURS",
    "VAGUE_KEYWORDS",
    "VIDEO_ONLY_KEYWORDS",
    "FLIGHT_HOTEL_KEYWORDS",
]