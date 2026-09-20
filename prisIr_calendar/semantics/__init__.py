# -*- coding: utf-8 -*-
"""
prisIr_calendar.semantics — 语义层 (Phase 1, task #4)

> 设计文档: docs/prisIr-calendar-design.md
> 产品边界: docs/prisIr-calendar-product-scope.md

职责(对齐 travel-time-buddy.md 9 条契约):
    - venue.py: 地址识别 + workplace 判定 + venues 表缓存读写
    - chain.py:  上一场 in-person 作为起点 / 起点推断
    - classify.py: 跳过规则 (video_only / all_day / flight_hotel / vague / same_workplace)

不要做:
    - 不接 HTTP (这是纯语义层, 协议层 task #5)
    - 不调 LLM (deterministic + 正则 + maps_vendor.geocode)
    - 不修改 task #2/#3/#7 已落地的文件 (schema.sql / store.py / maps_vendor.py)

API 边界 (供 task #5 http_api.py 调用):
    resolve_venue(raw, *, store, prefer_vendor=None) -> Venue
    is_workplace_diff(venue, workplace)             -> bool
    scan_chain(events, *, workplace, default_mode)   -> list[ChainLink]
    choose_origin_for_event(event, prior, workplace) -> str
    should_skip_event(event)                         -> tuple[bool, str]

依赖:
    - prisIr_calendar.store.CalendarStore  (venues 表读写)
    - prisIr_calendar.tools.maps_vendor.geocode  (地址 → lat/lng, 已落地的 25 测试绿)
    - user_profile.load_travel_profile    (默认 mode 兜底)
"""
from __future__ import annotations

from .venue import (
    Venue,
    is_workplace_diff,
    resolve_venue,
)
from .chain import (
    ChainLink,
    Event,
    choose_origin_for_event,
    scan_chain,
)
from .classify import (
    ALL_DAY_MIN_HOURS,
    SKIP_REASONS,
    VAGUE_KEYWORDS,
    VIDEO_ONLY_KEYWORDS,
    FLIGHT_HOTEL_KEYWORDS,
    should_skip_event,
)

__all__ = [
    # venue
    "Venue",
    "resolve_venue",
    "is_workplace_diff",
    # chain
    "Event",
    "ChainLink",
    "scan_chain",
    "choose_origin_for_event",
    # classify
    "should_skip_event",
    "SKIP_REASONS",
    "ALL_DAY_MIN_HOURS",
    "VAGUE_KEYWORDS",
    "VIDEO_ONLY_KEYWORDS",
    "FLIGHT_HOTEL_KEYWORDS",
]