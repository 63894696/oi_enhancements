# -*- coding: utf-8 -*-
"""
venue.py — 地址识别 + workplace 判定 (task #4)

核心契约 (travel-time-buddy.md):
    - 用户说地址 → 走 maps_vendor.geocode → 落 venues 表缓存
    - workplace = {office: str, home: str}
    - venue 与 workplace 相同 → 不需要插 buffer (same_workplace skip)

数据布局:
    - venues 表 schema.sql: (venue_id PK, raw_name, canonical_name, address,
      latitude, longitude, vendor, vendor_place_id, created_at, updated_at)
    - raw_name 存用户原话 / 口语名 "国贸 SK"
    - canonical_name 存 geocode 反查的 formatted_address
    - vendor 存 "amap" / "google" / "manual"

复用:
    - maps_vendor.geocode(address) -> {ok, lat, lng, formatted_address}
    - CalendarStore (db_path + 已有 init_schema)

不做的:
    - 不接 Nominatim / OpenStreetMap (产品边界)
    - 不做地理围栏 / 距离矩阵 (Phase 1 单地址够用)
    - 不写真实 key (沿用 maps_vendor 的 env / ~/.prisIr 习惯)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 task #7 已落地的 geocode
from prisIr_calendar.tools import maps_vendor

log = logging.getLogger("prisIr_calendar.semantics.venue")


# ============================================================
# 数据类
# ============================================================
@dataclass
class Venue:
    """已识别的地点(geocode 结果 + 缓存命中标记)."""
    raw: str                    # 原始字符串 "北京市朝阳区建国路 88 号"
    normalized: str             # 标准化形式 (geocode.formatted_address 或 raw 兜底)
    lat: Optional[float]        # None = geocode 失败
    lng: Optional[float]
    geocode_source: Optional[str]  # "amap" | "google" | None
    venue_id: Optional[str] = None  # 命中 venues 表时填入


# ============================================================
# venues 表读写 — 复用 store.db_path, 不直接依赖私有方法
# ============================================================

# 应用层串行化锁: 同一进程多线程并发写 venues 时互斥,
# 防 SQLite "database is locked" 串行化冲突. 跨进程仍靠 WAL.
_VENUES_LOCK = threading.Lock()


def _now_iso() -> str:
    """UTC ISO8601, 秒精度. 与 store._utc_now_iso 保持一致."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _connect(store) -> sqlite3.Connection:
    """开一个独立 sqlite3 连接, 复用 store 的 db_path 与 busy_timeout.

    为什么不直接用 store._connect():
        - task #2 拍板的 store.py 是冻结文件, 不允许改.
        - 我们要的"读 venues 缓存 + 写一行 INSERT"是极小操作,
          与 store 主路径解耦反而更稳. 不进 store._lock 也合理
          (venues 写有自己的应用层 _VENUES_LOCK).
    """
    conn = sqlite3.connect(
        str(store.db_path), isolation_level=None, timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def _lookup_venue_unlocked(store, raw: str) -> Optional[Venue]:
    """查 venues 表缓存(无锁, 调用方负责加锁).

    Phase 1 只按 raw_name 精确匹配. 后续 Phase 2 可加模糊匹配 / 同义名.
    """
    raw_norm = (raw or "").strip()
    if not raw_norm:
        return None
    with _connect(store) as conn:
        row = conn.execute(
            "SELECT venue_id, raw_name, canonical_name, address, "
            "latitude, longitude, vendor, vendor_place_id "
            "FROM venues WHERE raw_name = ? LIMIT 1",
            (raw_norm,),
        ).fetchone()
    if not row:
        return None
    return Venue(
        raw=row["raw_name"],
        normalized=row["canonical_name"] or row["address"] or row["raw_name"],
        lat=row["latitude"],
        lng=row["longitude"],
        geocode_source=row["vendor"],
        venue_id=row["venue_id"],
    )


def _upsert_venue_unlocked(store, v: Venue) -> str:
    """写入或更新 venues 表(无锁). 返回 venue_id.

    策略:
        - 按 raw_name 唯一索引(idx_venues_raw_name 已建) → 同 raw 二次写
          走 UPDATE 而非 INSERT, 避免 IntegrityError.
        - lat/lng 为 None 也能写(vendor='manual' 占位场景, Phase 2 用).
    """
    now = _now_iso()
    raw_norm = (v.raw or "").strip()
    if not raw_norm:
        raise ValueError("venue.raw must be non-empty")

    with _connect(store) as conn:
        # 1) 先查已有
        existing = conn.execute(
            "SELECT venue_id FROM venues WHERE raw_name = ? LIMIT 1",
            (raw_norm,),
        ).fetchone()
        if existing:
            venue_id = existing["venue_id"]
            conn.execute(
                """
                UPDATE venues SET
                    canonical_name = ?,
                    address = ?,
                    latitude = ?,
                    longitude = ?,
                    vendor = ?,
                    vendor_place_id = ?,
                    updated_at = ?
                WHERE venue_id = ?
                """,
                (
                    v.normalized,
                    v.normalized,
                    v.lat,
                    v.lng,
                    v.geocode_source,
                    None,
                    now,
                    venue_id,
                ),
            )
            return venue_id

        # 2) INSERT 新行
        venue_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO venues (
                venue_id, raw_name, canonical_name, address,
                latitude, longitude, vendor, vendor_place_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                venue_id, raw_norm, v.normalized, v.normalized,
                v.lat, v.lng, v.geocode_source, None,
                now, now,
            ),
        )
    return venue_id


# ============================================================
# 公开 API
# ============================================================
async def resolve_venue(
    raw: str,
    *,
    store,
    prefer_vendor: Optional[str] = None,
) -> Venue:
    """地址识别: 缓存 → geocode → 写库 → 返回 Venue.

    Args:
        raw: 原始地址字符串, e.g. "国贸 SK" / "北京市朝阳区建国路 88 号"
        store: CalendarStore 实例(提供 db_path 与已 init 的 schema)
        prefer_vendor: 预留, "amap" / "google". Phase 1 走 maps_vendor 自动路由.

    Returns:
        Venue. 失败时 lat/lng = None, geocode_source = None.
        调用方(should_skip_event)据此判 no_venue / vague.

    失败语义:
        - geocode ok=False (双 vendor 都挂) → 不写表, 返回 Venue(lat=None, lng=None)
        - 网络异常 / 超时 → 同上, 不阻塞语义层
    """
    raw_norm = (raw or "").strip()
    if not raw_norm:
        return Venue(
            raw="", normalized="", lat=None, lng=None,
            geocode_source=None, venue_id=None,
        )

    # 1) 缓存命中
    with _VENUES_LOCK:
        cached = _lookup_venue_unlocked(store, raw_norm)
    if cached is not None:
        # 缓存命中 → 直接返回, 不调 geocode
        return cached

    # 2) 缓存未命中 → 走 maps_vendor.geocode
    try:
        geo = await maps_vendor.geocode(raw_norm)
    except Exception as e:  # noqa: BLE001 — 语义层绝不抛
        log.warning("resolve_venue: geocode(%r) exception: %s", raw_norm, e)
        geo = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if geo.get("ok"):
        lat = float(geo["lat"])
        lng = float(geo["lng"])
        normalized = str(geo.get("formatted_address") or raw_norm)
        # maps_vendor.geocode 内部 cache 已知命中, source 在 cache_key 里.
        # Phase 1 不暴露 source → 用 None 占位. Phase 2 可让 geocode 返回 source.
        venue = Venue(
            raw=raw_norm,
            normalized=normalized,
            lat=lat,
            lng=lng,
            geocode_source=None,  # maps_vendor.geocode 当前未返 source
            venue_id=None,
        )
    else:
        # 失败 → 占位 Venue(全 None)
        venue = Venue(
            raw=raw_norm,
            normalized=raw_norm,  # 失败时 fallback 到原 raw, 让 vague / no_venue 兜底
            lat=None,
            lng=None,
            geocode_source=None,
            venue_id=None,
        )

    # 3) 写 venues 表(失败也要记一条 raw_name 占位, 避免反复调 geocode 烧 key)
    with _VENUES_LOCK:
        try:
            venue_id = _upsert_venue_unlocked(store, venue)
            venue = Venue(
                raw=venue.raw,
                normalized=venue.normalized,
                lat=venue.lat,
                lng=venue.lng,
                geocode_source=venue.geocode_source,
                venue_id=venue_id,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("resolve_venue: upsert(%r) fail: %s", raw_norm, e)
            # 写失败不阻塞 — Venue 已可返回, 仅 venue_id 为 None

    return venue


def is_workplace_diff(venue: Venue, workplace: Dict[str, Optional[str]]) -> bool:
    """workplace 判定: 该 venue 是否与 office/home 不同?

    Args:
        venue: 已解析的 Venue. normalized 为空时视为"未识别", 与 workplace diff.
        workplace: {"office": str|None, "home": str|None}
                   任一字段为 None / 空串 → 不参与比较.

    Returns:
        True  = 该 venue 与 workplace 不同 (需要算 buffer)
        False = 与 workplace 相同  (skip same_workplace)

    比较规则:
        - venue.lat/lng 都已知 → 按坐标距离 <= WORKPLACE_PROXIMITY_METERS (默认 50m) 判同
        - 否则按 normalized 字符串(小写、去空白)严格相等判同
        - 都不匹配 → diff
    """
    WORKPLACE_PROXIMITY_METERS = 50.0

    if not workplace or not venue or not venue.normalized:
        # venue 未识别 → 视为 diff (需要 geocode 后才能判定, classify 决定是否进)
        return True

    # 1) 坐标法 (有 lat/lng 且 workplace 字段也有坐标时最稳)
    wp_coords = _extract_workplace_coords(workplace)
    if venue.lat is not None and venue.lng is not None and wp_coords:
        for wp_lat, wp_lng in wp_coords:
            d = _haversine_meters(venue.lat, venue.lng, wp_lat, wp_lng)
            if d <= WORKPLACE_PROXIMITY_METERS:
                return False

    # 2) 字符串法 (归一化后严格相等)
    norm_v = _norm(venue.normalized)
    for key in ("office", "home"):
        wp_str = (workplace.get(key) or "").strip()
        if not wp_str:
            continue
        if _norm(wp_str) == norm_v:
            return False

    return True


# ============================================================
# helpers
# ============================================================
def _norm(s: str) -> str:
    """归一化字符串比较: 小写 + 去空白 + 全角/半角统一."""
    return "".join((s or "").lower().split())


def _extract_workplace_coords(
    workplace: Dict[str, Optional[str]],
) -> List[Tuple[float, float]]:
    """从 workplace dict 里拆坐标.

    Phase 1 支持两种来源:
        - "{slot}_lat" / "{slot}_lng" 字段 (Phase 2 user_profile 已规划)
        - "<name>@lat,lng" 字符串里 (兜底, 兼容手工 dict)

    没有坐标字段 → 返回 [], 走字符串比较.
    """
    out: List[Tuple[float, float]] = []
    for slot in ("office", "home"):
        lat = workplace.get(f"{slot}_lat")
        lng = workplace.get(f"{slot}_lng")
        if lat is not None and lng is not None:
            try:
                out.append((float(lat), float(lng)))
                continue
            except (TypeError, ValueError):
                pass
        # 兜底: "<name>@lat,lng"
        v = workplace.get(slot)
        if v and "@" in v:
            _, coord_part = v.rsplit("@", 1)
            if "," in coord_part:
                try:
                    lat_s, lng_s = coord_part.split(",", 1)
                    out.append((float(lat_s.strip()), float(lng_s.strip())))
                except (TypeError, ValueError):
                    pass
    return out


def _haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """球面距离 (米). 简化版: 用 haversine 公式, 地球半径 6371000m."""
    import math
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(a))


__all__ = [
    "Venue",
    "resolve_venue",
    "is_workplace_diff",
]