# -*- coding: utf-8 -*-
"""
prisIr_calendar — PrisirAI 日历模块 (Phase 1)

> 核心原则:唯一源、不外同步、agent 语义优先、ICS 仅作导出格式。

Phase 1 交付:
    - schema.sql: 4 张表的 DDL (events / buffers / ledger / venues)
    - store.py:   CalendarStore (sqlite3 + WAL + asyncio.Lock) + ICS 导出

Phase 2 集成:
    - http_api.py: FastAPI 暴露,沿用 companion/music/port_registry.py 模式

task #22 (2026-09-19):
    - scheduler.py: TravelBufferScheduler, stdlib cron -> 周期跑 scan_and_protect

依赖:
    - icalendar>=7.0        (ICS 序列化)
    - recurring-ical-events (循环事件展开)
    - sqlite3 (stdlib)
    - zoneinfo (stdlib, Python 3.9+)
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["CalendarStore"]

# task #22:scheduler 公开
try:  # noqa: SIM105
    from prisIr_calendar.scheduler import TravelBufferScheduler  # noqa: F401
    __all__.append("TravelBufferScheduler")
except Exception:  # noqa: BLE001
    # scheduler 加载失败不应阻塞 store 单独使用
    pass