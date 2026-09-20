# -*- coding: utf-8 -*-
"""
chain.py — 链式起点识别 (task #4)

核心契约 (travel-time-buddy.md):
    - 两场都是 in-person (venue 存在 + 非 dismissed + workplace_diff)
    - 时间相邻: 下一场在上一场结束后 0-4 小时内
    - 默认起点 = 上一场的 venue, 不是 workplace
    - 早晨首场: 起点 = workplace.office
    - 晚上最后一场: 终点 = workplace.home
    - dismissed 的事件视为"位置不可用", 跳过, 起点回退到 workplace

Event 类型 (语义层独立 dataclass):
    - 不直接 import EventRecord, 与 store 解耦
    - API 层 (task #5) 负责从 EventRecord 转 Event
    - 字段最小集: id / 时间 / summary / description / venue / dismissed / workplace_diff

API:
    scan_chain(events, *, workplace, default_mode) -> list[ChainLink]
    choose_origin_for_event(event, prior_event, workplace) -> str
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .venue import Venue

log = logging.getLogger("prisIr_calendar.semantics.chain")


# ============================================================
# 数据类
# ============================================================
@dataclass
class Event:
    """语义层事件类型 (解耦 store.EventRecord).

    API 层 (task #5) 从 EventRecord 转过来, 字段语义保持一致但字段更少.
    """
    event_id: str
    summary: str
    description: Optional[str]
    dtstart_utc: str            # ISO8601 字符串
    dtend_utc: str              # ISO8601 字符串
    venue: Optional[Venue] = None  # 已 resolve 的 Venue, None = 无地点
    dismissed: bool = False
    workplace_diff: Optional[Any] = None  # 与 EventRecord.workplace_diff 同义
    source: str = "user"


@dataclass
class ChainLink:
    """两场相邻 in-person 事件的链式起点."""
    origin_event_id: str
    origin_venue: Venue
    next_event_id: str
    next_venue: Venue
    gap_minutes: int            # 两场间隔 (分钟)


# ============================================================
# helpers
# ============================================================
def _parse_iso(s: str) -> Optional[datetime]:
    """解析 ISO8601 → tz-aware datetime (UTC). 失败返回 None."""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _gap_minutes(prev_end: str, next_start: str) -> Optional[int]:
    """两事件间隔 (分钟). 负数 (重叠) 视为相邻 (返回 0)."""
    e = _parse_iso(prev_end)
    s = _parse_iso(next_start)
    if e is None or s is None:
        return None
    delta_s = (s - e).total_seconds()
    if delta_s < 0:
        return 0
    return int(delta_s // 60)


def _same_day_local(ev: Event, tz_name: str = "Asia/Shanghai") -> bool:
    """判定两事件是否同日(本地时间).

    Phase 1 默认 Asia/Shanghai, 与 store.EventRecord.timezone 默认一致.
    """
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    a = _parse_iso(ev.dtstart_utc)
    if a is None:
        return False
    return a.astimezone(tz).date()


def _same_day(a: Event, b: Event) -> bool:
    """两事件是否同日(按各自 timezone 字段, 失败时 UTC)."""
    # 简化: 都按 Asia/Shanghai 比日期 — 产品边界默认国内.
    # Phase 2 可读 ev.timezone 字段细分.
    ta = _parse_iso(a.dtstart_utc)
    tb = _parse_iso(b.dtstart_utc)
    if ta is None or tb is None:
        return False
    # 都按 UTC 比 date — 跨时区场景 Phase 1 暂不处理
    return ta.astimezone(timezone.utc).date() == tb.astimezone(timezone.utc).date()


def _has_in_person_venue(ev: Event) -> bool:
    """事件是否有可用的 in-person venue (venue 存在 + 非 dismissed)."""
    if ev.dismissed:
        return False
    if ev.venue is None:
        return False
    if not ev.venue.normalized:
        return False
    return True


def _is_morning(ev: Event) -> bool:
    """是否"早晨首场" (0:00-12:00 本地时间).

    拍板决策 (task #4 描述):
        "早晨首场事件 0:00-12:00 → 用 office, 12:00 后用 office 也行(宽松)"
    简化: 用 UTC 12:00 (= 北京时间 20:00) 之前都算 "早晨",
    对国内用户足够宽松. Phase 2 可精细化.
    """
    t = _parse_iso(ev.dtstart_utc)
    if t is None:
        return False
    # 按 UTC 时辰判: 0-12 UTC = 8-20 北京时间, 早晨窗口足够宽
    return t.astimezone(timezone.utc).hour < 12


# ============================================================
# 公开 API
# ============================================================
def choose_origin_for_event(
    event: Event,
    prior_event: Optional[Event],
    workplace: Dict[str, Optional[str]],
) -> str:
    """返回 event 的起点 venue 字符串(normalized).

    规则 (按优先级):
        1. 有上一场 in-person 且时间相邻(<= 4h) → 起点 = 上一场 venue
        2. 早晨首场 (prior_event is None) → 起点 = workplace.office
        3. 否则 → 起点 = workplace.office (宽松, 无 prior 也算早晨)

    Args:
        event: 当前事件
        prior_event: 上一场事件, None = 当天首场
        workplace: {office: str|None, home: str|None}, 任一字段可空

    Returns:
        normalized 字符串 (给 maps_vendor.eta 当 origin 用).
        若 workplace.office 也空 → 返回 event 自身的 venue (兜底, 由 caller 判 skip)
    """
    office = (workplace or {}).get("office") or ""

    # 规则 1: 上一场 in-person 且相邻
    if prior_event is not None and _has_in_person_venue(prior_event):
        gap = _gap_minutes(prior_event.dtend_utc, event.dtstart_utc)
        if gap is not None and 0 <= gap <= 240:  # 0-4h
            return prior_event.venue.normalized

    # 规则 2/3: 兜底 → workplace.office
    if office:
        return office

    # 兜底兜底: 用 event 自身的 venue (让 caller 决定是否走 skip)
    if event.venue and event.venue.normalized:
        return event.venue.normalized

    return ""


async def scan_chain(
    events: List[Event],
    *,
    workplace: Dict[str, Optional[str]],
    default_mode: str = "car",
) -> List[ChainLink]:
    """扫描时间窗内事件, 识别相邻 in-person 链式起点.

    算法:
        - 输入 events 已按 dtstart_utc 升序排列 (caller 保证)
        - 遍历相邻对 (i, i+1):
            * 都是 in-person (venue 存在 + 非 dismissed)
            * gap <= 4h
            * 同日 (不跨天连)
            → 生成 ChainLink
        - 跨日事件链不连 (Phase 1: 同日才 chain)
        - 已 dismissed 的事件不参与链 (起点回退到 workplace, 由 choose_origin 内部处理)

    Args:
        events: 已按 dtstart_utc 升序的 Event 列表 (建议包含 dismissed, 由本函数过滤)
        workplace: {office, home} — 起点回退用
        default_mode: Phase 1 不用, 留给 Phase 2 buffer 计算. 兜底 "car"

    Returns:
        list[ChainLink]. 可能为空 (仅 1 场会议 / 全 dismissed / 无 venue).
    """
    if not events:
        return []
    out: List[ChainLink] = []

    for i in range(len(events) - 1):
        prev = events[i]
        curr = events[i + 1]

        # 两边都要 in-person
        if not _has_in_person_venue(prev):
            continue
        if not _has_in_person_venue(curr):
            continue

        # 同日
        if not _same_day(prev, curr):
            continue

        gap = _gap_minutes(prev.dtend_utc, curr.dtstart_utc)
        if gap is None or gap > 240:  # 不相邻 (超过 4h)
            continue

        out.append(ChainLink(
            origin_event_id=prev.event_id,
            origin_venue=prev.venue,
            next_event_id=curr.event_id,
            next_venue=curr.venue,
            gap_minutes=gap,
        ))

    return out


__all__ = [
    "Event",
    "ChainLink",
    "scan_chain",
    "choose_origin_for_event",
    "_parse_iso",       # 测试可 import
    "_gap_minutes",     # 测试可 import
]