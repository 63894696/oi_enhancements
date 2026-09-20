# -*- coding: utf-8 -*-
"""
reader.py — Agent 层读取 API (task #5)

公共 API:
    list_window(store, *, days=14, include_dismissed=False) -> list[Event]
    get_event(store, event_id) -> Event | None
    get_today_view(store, *, user_profile) -> TodayView

对齐契约:
    - travel-time-buddy.md 契约 #1: 14 天时间窗 (默认 14 天)
    - 默认过滤 dismissed (include_dismissed=False)
    - venue 字段已 resolve (通过 semantics.venue.resolve_venue)

Event 是 semantics.chain.Event 的别名 (语义层已定义)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from prisIr_calendar.semantics.chain import Event
from prisIr_calendar.semantics.venue import Venue, resolve_venue
from prisIr_calendar.store import CalendarStore, EventRecord

log = logging.getLogger("prisIr_calendar.agent_ops.reader")


# ============================================================
# TodayView — UI 直接渲染的视图(供 task #6 用)
# ============================================================
@dataclass
class TodayView:
    """今日视图: events + 每事件对应的 buffers + chain 信息.

    字段:
        scope_start / scope_end: 视图窗口 (UTC ISO8601)
        events:  按 dtstart 升序的事件列表(已 resolve venue)
        buffers_by_event: event_id → BufferRecord 列表
        chain_links: scan_chain 识别的相邻 in-person 链
    """
    scope_start: str
    scope_end: str
    events: List[Event] = field(default_factory=list)
    buffers_by_event: Dict[str, List[Any]] = field(default_factory=dict)
    chain_links: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """供 JSON 序列化的轻量字典(Phase 2 http_api 用)."""
        return {
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "events": [_event_to_dict(e) for e in self.events],
            "buffers_by_event": {
                k: [_buffer_to_dict(b) for b in v]
                for k, v in self.buffers_by_event.items()
            },
            "chain_links": [_chain_link_to_dict(c) for c in self.chain_links],
        }


def _event_to_dict(ev: Event) -> Dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "summary": ev.summary,
        "description": ev.description,
        "dtstart_utc": ev.dtstart_utc,
        "dtend_utc": ev.dtend_utc,
        "venue": _venue_to_dict(ev.venue) if ev.venue else None,
        "dismissed": ev.dismissed,
        "source": ev.source,
    }


def _venue_to_dict(v: Venue) -> Dict[str, Any]:
    return {
        "raw": v.raw,
        "normalized": v.normalized,
        "lat": v.lat,
        "lng": v.lng,
        "geocode_source": v.geocode_source,
        "venue_id": v.venue_id,
    }


def _buffer_to_dict(b: Any) -> Dict[str, Any]:
    """BufferRecord → dict. buffer 是 store.BufferRecord."""
    return {
        "buffer_id": b.buffer_id,
        "event_id": b.event_id,
        "type": b.type,
        "duration_min": b.duration_min,
        "inserted_before": b.inserted_before,
        "inserted_after": b.inserted_after,
        "metadata": b.metadata,
    }


def _chain_link_to_dict(c: Any) -> Dict[str, Any]:
    """ChainLink → dict."""
    return {
        "origin_event_id": c.origin_event_id,
        "next_event_id": c.next_event_id,
        "gap_minutes": c.gap_minutes,
    }


# ============================================================
# 内部: EventRecord → Event 转换 + venue resolve
# ============================================================
async def _record_to_event(store: CalendarStore, rec: EventRecord) -> Event:
    """EventRecord → Event, 同步 resolve venue (用 store.venue_id 命中缓存).

    没有 venue_id 的事件 → Event.venue=None, 由 caller (classify) 判 no_venue.
    """
    venue: Optional[Venue] = None
    if rec.venue_id:
        # 命中 venue_id → 直接读缓存,不调 geocode
        venue = await _lookup_venue_by_id(store, rec.venue_id)
    elif rec.description and _looks_like_address(rec.description):
        # 兜底: 从 description 里提地址(用户原话常用地址做描述)
        # 这里只做轻量识别,不主动 resolve(避免烧 key)
        # 真实 resolve 留给 scan_and_protect 走
        pass

    return Event(
        event_id=rec.event_id,
        summary=rec.summary,
        description=rec.description,
        dtstart_utc=rec.dtstart_utc,
        dtend_utc=rec.dtend_utc,
        venue=venue,
        dismissed=rec.dismissed,
        workplace_diff=rec.workplace_diff,
        source=rec.source,
    )


async def _lookup_venue_by_id(store: CalendarStore, venue_id: str) -> Optional[Venue]:
    """按 venue_id 查 venues 表缓存."""
    import sqlite3
    conn = sqlite3.connect(str(store.db_path), isolation_level=None, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT raw_name, canonical_name, latitude, longitude, vendor "
            "FROM venues WHERE venue_id = ? LIMIT 1",
            (venue_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return Venue(
        raw=row["raw_name"],
        normalized=row["canonical_name"] or row["raw_name"],
        lat=row["latitude"],
        lng=row["longitude"],
        geocode_source=row["vendor"],
        venue_id=venue_id,
    )


def _looks_like_address(text: str) -> bool:
    """轻量识别: 文本是否像地址(中文字符 + 长度足够)."""
    if not text or len(text) < 4:
        return False
    # 含行政区划关键词 或 数字+号 → 倾向地址
    keys = ("市", "区", "县", "街", "路", "号", "楼", "大厦", "广场")
    return any(k in text for k in keys)


# ============================================================
# 公开 API
# ============================================================
async def list_window(
    store: CalendarStore,
    *,
    days: int = 14,
    include_dismissed: bool = False,
    now: Optional[datetime] = None,
) -> List[Event]:
    """14 天时间窗查询(契约 #1).

    Args:
        store: CalendarStore 实例
        days: 时间窗长度(默认 14 天)
        include_dismissed: 是否含 dismissed (默认 False)
        now: 锚定时刻 (默认 utcnow); 测试可注入固定时刻

    Returns:
        按 dtstart 升序的 Event 列表(已 resolve venue)
    """
    if days <= 0:
        return []
    anchor = now or datetime.now(timezone.utc)
    start = anchor
    end = anchor + timedelta(days=days)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    records = store.list_events(start_iso, end_iso, include_dismissed=include_dismissed)
    events: List[Event] = []
    for rec in records:
        events.append(await _record_to_event(store, rec))
    return events


async def get_event(
    store: CalendarStore,
    event_id: str,
) -> Optional[Event]:
    """单事件查询,含 venue resolve (按 venue_id 命中缓存).

    Args:
        store: CalendarStore 实例
        event_id: 事件 UUID

    Returns:
        Event 或 None (不存在)
    """
    rec = store.get_event(event_id)
    if rec is None:
        return None
    return await _record_to_event(store, rec)


async def get_today_view(
    store: CalendarStore,
    *,
    user_profile: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    days: int = 14,
) -> TodayView:
    """今日视图: events + buffers + chains,供 UI 直接渲染.

    "今日" = 锚定当日 00:00 → 当日 + days 天 (UTC; Phase 1 简化,
    Phase 2 可读 user_profile.timezone 转本地时区).

    时间窗 = [UTC 当天 00:00, UTC 当天 00:00 + days 天).
    默认 14 天, 跟 UI(timeline.html/timeline.js)对齐。

    Args:
        store: CalendarStore
        user_profile: 出行画像 (从 user_profile.load_travel_profile() 读)
        now: 锚定时刻(默认 utcnow)
        days: 时间窗长度(默认 14 天; 跟 list_window 对齐)

    Returns:
        TodayView, 含 events / buffers_by_event / chain_links
    """
    from prisIr_calendar.semantics.chain import scan_chain

    anchor = now or datetime.now(timezone.utc)
    # 简化: 用 UTC 当天 00:00 → 当天 + days 天
    day_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=days)
    start_iso = day_start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end_iso = day_end.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # 复用 list_window, 不要重复 list_events
    # 用 day_start (UTC 00:00) 作为锚, 让窗口从当天 00:00 起算
    events = await list_window(
        store, days=days, include_dismissed=False, now=day_start,
    )

    buffers_by_event: Dict[str, List[Any]] = {}
    for ev in events:
        buffers_by_event[ev.event_id] = store.list_buffers(ev.event_id)

    # chain 识别 — 给前端展示"上一场是谁" / "起点选哪个"
    # scan_chain 内部已处理跨日不连的语义, 这里直接传全部 events 即可
    workplace = (user_profile or {}).get("workplace_addresses") or {}
    chain_links = []
    if events:
        chain_links = await scan_chain(events, workplace=workplace, default_mode="car")

    return TodayView(
        scope_start=start_iso,
        scope_end=end_iso,
        events=events,
        buffers_by_event=buffers_by_event,
        chain_links=chain_links,
    )


__all__ = [
    "TodayView",
    "list_window",
    "get_event",
    "get_today_view",
]
