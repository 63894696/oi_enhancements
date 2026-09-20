# -*- coding: utf-8 -*-
"""
prisIr_calendar.tools — Calendar 工具集

Phase 1 交付:
    - maps_vendor: 高德 / Google Maps 双 vendor 路由(国内 / 海外)

设计原则(对齐 docs/prisIr-calendar-product-scope.md):
    - 双 vendor(amap + google),不接 Mapbox / Bing / 百度
    - 国内走 amap,海外走 google,首次会话授权绑 key 落 ~/.prisIr/maps_keys.json
    - vendor 失败 5xx 自动 fallback 到对家
    - LRU 1000 + TTL 1h 缓存,避免重复查询烧 key 配额
    - 不做路线规划多段路径(Phase 1 单段 ETA 够用)
    - 不做实时交通事件订阅(只算 ETA)
"""
from __future__ import annotations

__all__ = ["maps_vendor"]
