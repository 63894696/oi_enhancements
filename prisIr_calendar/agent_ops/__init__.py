# -*- coding: utf-8 -*-
"""
prisIr_calendar.agent_ops — Agent 层 API (task #5)

> 设计文档: docs/prisIr-calendar-design.md
> 产品边界: docs/prisIr-calendar-product-scope.md
> 写入目录: docs/prisir-calendar-write-target.md
> 行为契约: travel-time-buddy.md (9 条)

职责:
    - reader.py:   读取 API (list_window / get_event / get_today_view)
    - writer.py:   写入 API (add_user_event / dismiss_event / insert_buffer)
    - travel_buffer.py: 主入口 TravelBufferAgent (scan_and_protect / ask_user)
    - prompts.py:  一次一问话术模板

不要做:
    - 不接 HTTP (Phase 2 才加 http_api.py)
    - 不直接调 LLM (decision 由 caller 注入)
    - 不修改 task #2/#3/#4/#7 已落地的文件 (schema.sql / store.py /
      semantics/* / tools/maps_vendor.py / calendar_maps_gates.py /
      user_profile.py)

依赖:
    - prisIr_calendar.store.CalendarStore
    - prisIr_calendar.semantics.{venue, chain, classify}
    - prisIr_calendar.tools.maps_vendor.{geocode, eta}
    - user_profile.{load_travel_profile, append_dismissed_buffer}
"""
from __future__ import annotations

from .reader import (
    list_window,
    get_event,
    get_today_view,
)
from .writer import (
    add_user_event,
    dismiss_event,
    insert_buffer,
)
from .travel_buffer import (
    TravelBufferAgent,
    ScanReport,
    Ambiguity,
)
from .prompts import (
    PROMPT_AMBIGUOUS_PLACE,
    PROMPT_UNKNOWN_WFH,
    PROMPT_UNCLEAR_MODE,
    PROMPT_BUFFER_DESTROYS_DAY,
    one_at_a_time,
    PromptKind,
)

__all__ = [
    # reader
    "list_window",
    "get_event",
    "get_today_view",
    # writer
    "add_user_event",
    "dismiss_event",
    "insert_buffer",
    # travel_buffer
    "TravelBufferAgent",
    "ScanReport",
    "Ambiguity",
    # prompts
    "PROMPT_AMBIGUOUS_PLACE",
    "PROMPT_UNKNOWN_WFH",
    "PROMPT_UNCLEAR_MODE",
    "PROMPT_BUFFER_DESTROYS_DAY",
    "one_at_a_time",
    "PromptKind",
]
