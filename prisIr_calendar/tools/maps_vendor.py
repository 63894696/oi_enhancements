# -*- coding: utf-8 -*-
"""
maps_vendor.py — 高德(amap)+ Google Maps 双 vendor 路由(2026-09-19, M-Cal task #7)

对齐 docs/prisIr-calendar-write-target.md §3 + 用户拍板:
  - 高德(amap)走国内,Google 走海外,detect_region 按地址关键字 + 坐标范围判
  - 首次会话授权绑 key,落 ~/.prisIr/maps_keys.json(沿用 keys.db 文件家族惯例)
  - vendor 失败(5xx / 网络异常)→ fallback 到对家
  - LRU 1000 entries + TTL 1h 缓存,避免重复查询烧 key 配额
  - mode: driving / walking / transit / bicycling(对齐各 vendor 的 mode 字符串)

设计要点:
  - 注册表(VENDORS)对照 companion_llm_providers._register 模式:模块级 dict +
    装饰器/直接赋值,允许 Phase 2 加 Mapbox / Bing 时不改 eta 主入口
  - HTTP 走 aiohttp(对齐 companion/music/lyric_provider.py 的项目惯例)
  - key 读取:env 优先 → ~/.prisIr/maps_keys.json 兜底,完全不读源码常量
  - 所有外部 IO(async)用 aiohttp,同步入口(get_region / detect_region / key 读写)保持纯函数

不要做的:
  - 不接 Mapbox / Bing / 百度
  - 不做路线规划多段路径
  - 不做实时交通事件订阅
  - 不写真实 key 到仓库
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple, Union

log = logging.getLogger("prisIr_calendar.maps_vendor")


# ============================================================
# 路径常量
# ============================================================

KEYS_FILE = Path.home() / ".prisIr" / "maps_keys.json"

# 默认端点(可被 env override,便于自部署 / 测试桩)
DEFAULT_AMAP_BASE = os.environ.get("PRISIR_AMAP_BASE", "https://restapi.amap.com")
DEFAULT_GOOGLE_BASE = os.environ.get("PRISIR_GOOGLE_BASE", "https://maps.googleapis.com")

# 超时(秒)— aiohttp.ClientTimeout
DEFAULT_TIMEOUT_S = float(os.environ.get("PRISIR_MAPS_TIMEOUT_S", "8.0"))

# 缓存容量 + TTL
CACHE_MAX_ENTRIES = 1000
CACHE_TTL_S = 3600.0


# ============================================================
# Mode 字符串映射(Prisir → vendor)
# ============================================================

AMAP_MODE_MAP: Dict[str, str] = {
    "driving": "driving",
    "walking": "walking",
    "transit": "transit",
    "bicycling": "bicycling",   # 高德实际叫 bicycling 也支持
}

GOOGLE_MODE_MAP: Dict[str, str] = {
    "driving": "driving",
    "walking": "walking",
    "transit": "transit",
    "bicycling": "bicycling",
}

SUPPORTED_MODES = ("driving", "walking", "transit", "bicycling")


# ============================================================
# detect_region — 国内/海外判断
# ============================================================

# 中文地址强国内信号(优先级最高)
_CN_KEYWORDS = (
    "中国", "中华人民共和国", "大陆", "内地", "国内",
    "省", "市", "区", "县", "镇", "村",  # 行政区划后缀
    "北京市", "上海市", "广州市", "深圳市", "杭州市", "南京市", "苏州市", "成都市",
    "武汉市", "重庆市", "西安市", "天津市", "青岛市", "大连市", "厦门市", "宁波市",
    "香港", "澳门", "台湾", "台北", "高雄",
)

# 英文地址海外信号(中文地址不会带)
_INTL_KEYWORDS = (
    "USA", "United States", "U.S.", "U.S.A.",
    "UK", "United Kingdom", "England", "Scotland", "Wales",
    "Japan", "Tokyo", "Osaka", "Kyoto",
    "Korea", "Seoul", "Busan",
    "Singapore", "Malaysia", "Kuala Lumpur",
    "Thailand", "Bangkok",
    "Vietnam", "Hanoi", "Ho Chi Minh",
    "Indonesia", "Jakarta", "Bali",
    "Philippines", "Manila",
    "India", "Mumbai", "Delhi", "Bangalore",
    "Australia", "Sydney", "Melbourne",
    "New Zealand", "Auckland",
    "Canada", "Toronto", "Vancouver", "Montreal",
    "Germany", "Berlin", "Munich",
    "France", "Paris", "Lyon",
    "Italy", "Rome", "Milan",
    "Spain", "Madrid", "Barcelona",
    "Netherlands", "Amsterdam",
    "Switzerland", "Zurich", "Geneva",
    "Russia", "Moscow",
    "Brazil", "Rio", "Sao Paulo",
    "Mexico", "Mexico City",
)

# 中国大陆常用 lat/lng 粗范围(覆盖本土,不含港澳台及争议区,
# 那些区域由 address 关键字再二次确认)
_CN_LAT_RANGE = (18.0, 54.0)
_CN_LNG_RANGE = (73.0, 135.0)


def detect_region(address_or_latlng: Union[str, Tuple[float, float], List[float], Dict[str, Any], None]) -> Literal["cn", "intl"]:
    """国内/海外判断(纯函数,无 IO)。

    输入:
        - str 地址(中文 / 英文 / 混合)
        - tuple/list/dict 坐标: (lat, lng) 或 {"lat": ..., "lng": ...}

    优先级:
        1. 坐标范围:在中国大陆 lat/lng 范围内 → "cn"
        2. 中文地址强信号(CN_KEYWORDS)→ "cn"
        3. 英文地址海外信号(INTL_KEYWORDS)→ "intl"
        4. 中文汉字存在 → "cn"(兜底,中文几乎都是国内)
        5. 否则 "intl"
    """
    if address_or_latlng is None:
        return "intl"

    # 1. 坐标模式
    lat: Optional[float] = None
    lng: Optional[float] = None
    if isinstance(address_or_latlng, (tuple, list)) and len(address_or_latlng) >= 2:
        try:
            lat = float(address_or_latlng[0])
            lng = float(address_or_latlng[1])
        except (TypeError, ValueError):
            lat = lng = None
    elif isinstance(address_or_latlng, dict):
        try:
            if "lat" in address_or_latlng:
                lat = float(address_or_latlng["lat"])
            if "lng" in address_or_latlng:
                lng = float(address_or_latlng["lng"])
            elif "lon" in address_or_latlng:
                lng = float(address_or_latlng["lon"])
        except (TypeError, ValueError):
            lat = lng = None

    if lat is not None and lng is not None:
        if _CN_LAT_RANGE[0] <= lat <= _CN_LAT_RANGE[1] and _CN_LNG_RANGE[0] <= lng <= _CN_LNG_RANGE[1]:
            return "cn"
        # 坐标在大陆外 → intl(海外覆盖更广,不需要二次判)
        return "intl"

    # 2. 字符串地址
    s = str(address_or_latlng).strip()
    if not s:
        return "intl"

    s_low = s.lower()

    # 海外关键字先判(避免 "USA 中国城" 这种被中文关键字抢答)
    for kw in _INTL_KEYWORDS:
        if kw.lower() in s_low:
            return "intl"

    # CN 关键字(中文大写不敏感 → 直接 substring)
    for kw in _CN_KEYWORDS:
        if kw in s:
            return "cn"

    # 中文字符兜底
    if re.search(r"[一-鿿]", s):
        return "cn"

    # 纯英文无关键字 → 海外
    return "intl"


# ============================================================
# Key 存储: ~/.prisIr/maps_keys.json
# ============================================================

@dataclass
class MapsKeys:
    """Maps vendor 的密钥集合(amap + google)。

    来源:
        1. 环境变量: PRISIR_AMAP_KEY / PRISIR_GOOGLE_KEY
        2. 文件: ~/.prisIr/maps_keys.json
    """
    amap: str = ""
    google: str = ""

    def has_amap(self) -> bool:
        return bool(self.amap and self.amap.strip())

    def has_google(self) -> bool:
        return bool(self.google and self.google.strip())


def _read_env_keys() -> MapsKeys:
    return MapsKeys(
        amap=os.environ.get("PRISIR_AMAP_KEY", "").strip(),
        google=os.environ.get("PRISIR_GOOGLE_KEY", "").strip(),
    )


def _read_file_keys(path: Optional[Path] = None) -> MapsKeys:
    """读 keys 文件;不传 path 时用模块级 KEYS_FILE(允许 monkeypatch)。

    默认参数陷阱:不能用 `path: Path = KEYS_FILE` — Python 在函数定义时求值,
    默认值已锁死,运行时 monkeypatch 无效。所以默认 None,运行时再取 KEYS_FILE。
    """
    actual = path if path is not None else KEYS_FILE
    if not actual.exists():
        return MapsKeys()
    try:
        data = json.loads(actual.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return MapsKeys()
        return MapsKeys(
            amap=str(data.get("amap", "")).strip(),
            google=str(data.get("google", "")).strip(),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[maps_vendor] read keys file %s fail: %s", actual, e)
        return MapsKeys()


def _write_file_keys(keys: MapsKeys, path: Optional[Path] = None) -> None:
    actual = path if path is not None else KEYS_FILE
    try:
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_text(
            json.dumps(
                {"amap": keys.amap, "google": keys.google},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # 设置权限位(仅 Unix 生效,Windows 跳过)— 对齐其它 keys 文件惯例
        try:
            os.chmod(actual, 0o600)
        except (OSError, NotImplementedError):
            pass
    except OSError as e:
        log.warning("[maps_vendor] write keys file %s fail: %s", actual, e)


def get_keys() -> MapsKeys:
    """读 keys:env 优先 → 文件兜底(env 覆盖文件值)。"""
    env_k = _read_env_keys()
    file_k = _read_file_keys()
    return MapsKeys(
        amap=env_k.amap or file_k.amap,
        google=env_k.google or file_k.google,
    )


def set_key(vendor: Literal["amap", "google"], api_key: str) -> None:
    """写 key(只写文件,env 由调用方自己设)。

    自动使用模块级 KEYS_FILE(允许测试 monkeypatch)。
    """
    cur = _read_file_keys()
    if vendor == "amap":
        cur.amap = api_key.strip()
    elif vendor == "google":
        cur.google = api_key.strip()
    else:
        raise ValueError(f"unknown vendor: {vendor!r}")
    _write_file_keys(cur)


# ============================================================
# 缓存层: LRU(OrderedDict)+ TTL
# ============================================================

class _LRUTTLCache:
    """线程安全 LRU + TTL 缓存(同步锁,够用于 key 缓存这种 IO-bound 前置)。"""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES, ttl_s: float = CACHE_TTL_S):
        self._max = max_entries
        self._ttl = ttl_s
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if (time.monotonic() - ts) > self._ttl:
                # 过期
                self._data.pop(key, None)
                return None
            # LRU bump
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            ts = time.monotonic()
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (ts, value)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"size": len(self._data), "max": self._max}


_CACHE = _LRUTTLCache()


def _cache_key(*parts: Any) -> str:
    """拼 cache key。None / 空字符串都归一化,避免 key 漂移。"""
    norm = [("" if p is None else str(p)).strip() for p in parts]
    return "|".join(norm)


def clear_cache() -> None:
    _CACHE.clear()


# ============================================================
# Vendor 协议 + 注册表
# ============================================================

@dataclass
class VendorResult:
    """Vendor 调用结果。"""
    ok: bool
    duration_seconds: Optional[int] = None
    distance_meters: Optional[int] = None
    origin_resolved: Optional[str] = None
    destination_resolved: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    raw: Optional[Dict[str, Any]] = None


# vendor 注册签名:async (origin, dest, mode, api_key) -> VendorResult
VendorEtaFn = Callable[[str, str, str, str, float], Awaitable[VendorResult]]
VendorGeocodeFn = Callable[[str, str, float], Awaitable[VendorResult]]


@dataclass
class VendorSpec:
    name: str                                  # "amap" / "google"
    display: str
    region: Literal["cn", "intl"]
    base_url: str                              # 可被 env 覆盖
    eta_fn: VendorEtaFn
    geocode_fn: VendorGeocodeFn
    # 端点路径(供注册表内自包含函数用)— 实际由 *_impl 内部拼
    notes: str = ""


# 模块级注册表(对齐 companion_llm_providers.LLM_PROVIDERS / _register 模式)
VENDORS: Dict[str, VendorSpec] = {}


def _register(spec: VendorSpec) -> None:
    """vendor 注册入口(模块级,不允许运行时覆盖已有同名)。"""
    if spec.name in VENDORS:
        log.warning("[maps_vendor] vendor %r already registered; overwriting", spec.name)
    VENDORS[spec.name] = spec


# ============================================================
# aiohttp session helper
# ============================================================

def _new_aiohttp_session_cls():
    """延迟 import aiohttp(避免硬依赖污染不需要 maps 的进程)。"""
    try:
        import aiohttp  # type: ignore
        return aiohttp
    except ImportError as e:
        raise RuntimeError(
            "maps_vendor 需要 aiohttp;请 pip install aiohttp"
        ) from e


async def _http_get_json(url: str, params: Dict[str, Any], timeout_s: float) -> Tuple[int, Dict[str, Any], str]:
    """GET → (status, json, text)。

    失败(status 非 200 / json 解析失败)统一返 (status, {}, text)。
    """
    aiohttp = _new_aiohttp_session_cls()
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, params=params) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {}
                return resp.status, data, text
    except asyncio.TimeoutError:
        return 0, {}, "timeout"
    except Exception as e:  # noqa: BLE001
        return 0, {}, f"{type(e).__name__}: {e}"


# ============================================================
# 高德(amap)实现
# ============================================================

AMAP_GEOCODE_PATH = "/v3/geocode/geo"
AMAP_DIRECTION_PATH_V3 = "/v3/direction"  # driving/walking/transit 都拼后缀


def _amap_eta_impl(origin: str, dest: str, mode: str, api_key: str,
                   timeout_s: float = DEFAULT_TIMEOUT_S) -> Awaitable[VendorResult]:
    """高德路径规划 v3 — 拼 origin/destination → driving/walking/transit/bicycling。"""
    async def _run() -> VendorResult:
        amap_mode = AMAP_MODE_MAP.get(mode)
        if not amap_mode:
            return VendorResult(ok=False, error=f"unsupported mode: {mode}")

        path = f"{AMAP_DIRECTION_PATH_V3}/{amap_mode}"
        params = {
            "key": api_key,
            "origin": origin,
            "destination": dest,
            "output": "json",
        }
        # 高德 transit 需要 city(简化:从 dest 提不出来时,用户自己保证)
        if mode == "transit":
            params["city"] = "全国"   # 高德要求;具体城市可后续从 geocode 反推

        url = f"{DEFAULT_AMAP_BASE}{path}"
        status, data, text = await _http_get_json(url, params, timeout_s)

        if status == 0:
            return VendorResult(ok=False, status_code=0, error=text or "network error")
        if status != 200:
            return VendorResult(ok=False, status_code=status,
                                error=f"HTTP {status}: {text[:200]}")

        # 高德 status: "0"=ok, "1"=err, "2"=参数错误, ...
        if str(data.get("status")) not in ("0", 0):
            errcode = data.get("infocode", "")
            err = data.get("info") or "amap error"
            return VendorResult(ok=False, status_code=status,
                                error=f"amap errcode={errcode}: {err}",
                                raw=data)

        route = data.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            return VendorResult(ok=False, status_code=status,
                                error="amap returned no paths", raw=data)
        p0 = paths[0]
        try:
            duration_s = int(p0.get("duration", 0))   # 高德已经是秒
            distance_m = int(p0.get("distance", 0))
        except (TypeError, ValueError):
            return VendorResult(ok=False, status_code=status,
                                error="amap duration/distance not int", raw=data)

        return VendorResult(
            ok=True,
            duration_seconds=duration_s,
            distance_meters=distance_m,
            origin_resolved=route.get("origin") or origin,
            destination_resolved=route.get("destination") or dest,
            raw=data,
        )
    return _run()


def _amap_geocode_impl(address: str, api_key: str,
                       timeout_s: float = DEFAULT_TIMEOUT_S) -> Awaitable[VendorResult]:
    """高德 geocode — /v3/geocode/geo?address=...&city=...&key=..."""
    async def _run() -> VendorResult:
        url = f"{DEFAULT_AMAP_BASE}{AMAP_GEOCODE_PATH}"
        params = {"key": api_key, "address": address, "output": "json"}
        status, data, text = await _http_get_json(url, params, timeout_s)
        if status == 0:
            return VendorResult(ok=False, status_code=0, error=text or "network error")
        if status != 200:
            return VendorResult(ok=False, status_code=status, error=f"HTTP {status}")
        if str(data.get("status")) not in ("0", 0):
            return VendorResult(ok=False, status_code=status,
                                error=f"amap geocode: {data.get('info')}", raw=data)
        geocodes = data.get("geocodes") or []
        if not geocodes:
            return VendorResult(ok=False, error="amap geocode empty")
        g = geocodes[0]
        loc = g.get("location", "")
        if not loc or "," not in loc:
            return VendorResult(ok=False, error=f"amap loc invalid: {loc!r}", raw=data)
        try:
            lng_s, lat_s = loc.split(",", 1)
            lng, lat = float(lng_s), float(lat_s)
        except (TypeError, ValueError):
            return VendorResult(ok=False, error=f"amap loc parse fail: {loc!r}", raw=data)
        return VendorResult(
            ok=True,
            origin_resolved=g.get("formatted_address") or address,
            raw={"lat": lat, "lng": lng, "formatted_address": g.get("formatted_address")},
        )
    return _run()


# ============================================================
# Google Maps 实现
# ============================================================

GOOGLE_GEOCODE_PATH = "/maps/api/geocode/json"
GOOGLE_DISTANCE_MATRIX_PATH = "/maps/api/distancematrix/json"


def _google_eta_impl(origin: str, dest: str, mode: str, api_key: str,
                     timeout_s: float = DEFAULT_TIMEOUT_S) -> Awaitable[VendorResult]:
    """Google Distance Matrix API。"""
    async def _run() -> VendorResult:
        g_mode = GOOGLE_MODE_MAP.get(mode)
        if not g_mode:
            return VendorResult(ok=False, error=f"unsupported mode: {mode}")

        url = f"{DEFAULT_GOOGLE_BASE}{GOOGLE_DISTANCE_MATRIX_PATH}"
        params = {
            "key": api_key,
            "origins": origin,
            "destinations": dest,
            "mode": g_mode,
            "language": "zh-CN",
        }
        status, data, text = await _http_get_json(url, params, timeout_s)
        if status == 0:
            return VendorResult(ok=False, status_code=0, error=text or "network error")
        if status != 200:
            return VendorResult(ok=False, status_code=status, error=f"HTTP {status}")

        # Google status: top-level "OK" / "REQUEST_DENIED" / "INVALID_REQUEST" / ...
        if data.get("status") != "OK":
            return VendorResult(ok=False, status_code=status,
                                error=f"google status={data.get('status')}: {data.get('error_message')}",
                                raw=data)

        rows = data.get("rows") or []
        if not rows:
            return VendorResult(ok=False, error="google rows empty", raw=data)
        elements = rows[0].get("elements") or []
        if not elements:
            return VendorResult(ok=False, error="google elements empty", raw=data)
        el = elements[0]
        if el.get("status") != "OK":
            return VendorResult(ok=False,
                                error=f"google element status={el.get('status')}", raw=data)

        try:
            duration_s = int(el.get("duration", {}).get("value", 0))
            distance_m = int(el.get("distance", {}).get("value", 0))
        except (TypeError, ValueError):
            return VendorResult(ok=False, error="google duration/distance not int", raw=data)

        # Google 返回的 origin/destination 是 address echo(原文)
        return VendorResult(
            ok=True,
            duration_seconds=duration_s,
            distance_meters=distance_m,
            origin_resolved=origin,
            destination_resolved=dest,
            raw=data,
        )
    return _run()


def _google_geocode_impl(address: str, api_key: str,
                         timeout_s: float = DEFAULT_TIMEOUT_S) -> Awaitable[VendorResult]:
    """Google Geocoding API。"""
    async def _run() -> VendorResult:
        url = f"{DEFAULT_GOOGLE_BASE}{GOOGLE_GEOCODE_PATH}"
        params = {"key": api_key, "address": address, "language": "zh-CN"}
        status, data, text = await _http_get_json(url, params, timeout_s)
        if status == 0:
            return VendorResult(ok=False, status_code=0, error=text or "network error")
        if status != 200:
            return VendorResult(ok=False, status_code=status, error=f"HTTP {status}")
        if data.get("status") != "OK":
            return VendorResult(ok=False,
                                error=f"google geocode status={data.get('status')}: {data.get('error_message')}",
                                raw=data)
        results = data.get("results") or []
        if not results:
            return VendorResult(ok=False, error="google geocode empty")
        r = results[0]
        loc = r.get("geometry", {}).get("location") or {}
        try:
            lat = float(loc.get("lat"))
            lng = float(loc.get("lng"))
        except (TypeError, ValueError):
            return VendorResult(ok=False, error="google loc invalid", raw=data)
        return VendorResult(
            ok=True,
            origin_resolved=r.get("formatted_address") or address,
            raw={"lat": lat, "lng": lng, "formatted_address": r.get("formatted_address")},
        )
    return _run()


# 注册两个 vendor
_register(VendorSpec(
    name="amap",
    display="高德地图(国内)",
    region="cn",
    base_url=DEFAULT_AMAP_BASE,
    eta_fn=_amap_eta_impl,
    geocode_fn=_amap_geocode_impl,
    notes="amap.cn restapi;v3 driving/walking/transit/bicycling",
))

_register(VendorSpec(
    name="google",
    display="Google Maps(海外)",
    region="intl",
    base_url=DEFAULT_GOOGLE_BASE,
    eta_fn=_google_eta_impl,
    geocode_fn=_google_geocode_impl,
    notes="Distance Matrix + Geocoding API;海外覆盖",
))


# ============================================================
# 公开 API
# ============================================================

def _normalize_mode(mode: str) -> str:
    m = (mode or "driving").strip().lower()
    if m not in SUPPORTED_MODES:
        return "driving"
    return m


async def eta(origin: str, dest: str, mode: str = "driving") -> Dict[str, Any]:
    """ETA 主入口(国内走 amap,海外走 google;5xx / 失败 fallback 对家)。

    Args:
        origin: 起点地址或 "lng,lat"(高德接受字符串坐标)
        dest: 终点地址或 "lng,lat"
        mode: driving / walking / transit / bicycling

    Returns:
        {
            "duration_seconds": int,
            "distance_meters": int,
            "source": "amap" | "google",
            "origin_resolved": str,
            "destination_resolved": str,
        }

    Raises:
        RuntimeError: 双 vendor 都失败 / 没 key
    """
    if not origin or not dest:
        return {
            "ok": False,
            "duration_seconds": 0,
            "distance_meters": 0,
            "source": "",
            "origin_resolved": origin or "",
            "destination_resolved": dest or "",
            "error": "origin and dest required",
        }

    m = _normalize_mode(mode)
    region = detect_region(origin)  # 优先按起点判
    cache_key = _cache_key("eta", region, m, origin, dest)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    keys = get_keys()
    primary, fallback = ("amap", "google") if region == "cn" else ("google", "amap")

    primary_spec = VENDORS.get(primary)
    fallback_spec = VENDORS.get(fallback)

    primary_key = keys.amap if primary == "amap" else keys.google
    fallback_key = keys.amap if fallback == "amap" else keys.google

    # 主 vendor
    if primary_spec and primary_key:
        try:
            r = await primary_spec.eta_fn(origin, dest, m, primary_key, DEFAULT_TIMEOUT_S)
            if r.ok:
                out = {
                    "ok": True,
                    "duration_seconds": int(r.duration_seconds or 0),
                    "distance_meters": int(r.distance_meters or 0),
                    "source": primary,
                    "origin_resolved": r.origin_resolved or origin,
                    "destination_resolved": r.destination_resolved or dest,
                }
                _CACHE.set(cache_key, out)
                return out
            log.info("[maps_vendor] %s eta fail: %s; try fallback %s", primary, r.error, fallback)
        except Exception as e:  # noqa: BLE001
            log.warning("[maps_vendor] %s eta exception: %s", primary, e)

    # fallback vendor
    if fallback_spec and fallback_key:
        try:
            r = await fallback_spec.eta_fn(origin, dest, m, fallback_key, DEFAULT_TIMEOUT_S)
            if r.ok:
                out = {
                    "ok": True,
                    "duration_seconds": int(r.duration_seconds or 0),
                    "distance_meters": int(r.distance_meters or 0),
                    "source": fallback,
                    "origin_resolved": r.origin_resolved or origin,
                    "destination_resolved": r.destination_resolved or dest,
                }
                _CACHE.set(cache_key, out)
                return out
            log.warning("[maps_vendor] fallback %s eta fail: %s", fallback, r.error)
        except Exception as e:  # noqa: BLE001
            log.warning("[maps_vendor] fallback %s exception: %s", fallback, e)

    # 双 vendor 都失败 → 不缓存(下次重试)
    missing = []
    if not keys.has_amap():
        missing.append("amap")
    if not keys.has_google():
        missing.append("google")
    msg = "no keys configured" if missing else "both vendors failed"
    return {
        "ok": False,
        "duration_seconds": 0,
        "distance_meters": 0,
        "source": "",
        "origin_resolved": origin,
        "destination_resolved": dest,
        "error": msg + (f" (missing: {','.join(missing)})" if missing else ""),
    }


async def geocode(address: str) -> Dict[str, Any]:
    """地理编码:地址 → (lat, lng, formatted_address)。

    Returns:
        {"ok": True, "lat": float, "lng": float, "formatted_address": str}
        或
        {"ok": False, "error": str}
    """
    if not address or not address.strip():
        return {"ok": False, "error": "address required"}

    region = detect_region(address)
    cache_key = _cache_key("geocode", region, address.strip())
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    keys = get_keys()
    primary, fallback = ("amap", "google") if region == "cn" else ("google", "amap")

    primary_spec = VENDORS.get(primary)
    fallback_spec = VENDORS.get(fallback)

    primary_key = keys.amap if primary == "amap" else keys.google
    fallback_key = keys.amap if fallback == "amap" else keys.google

    last_err = ""

    if primary_spec and primary_key:
        try:
            r = await primary_spec.geocode_fn(address, primary_key, DEFAULT_TIMEOUT_S)
            if r.ok:
                lat = float(r.raw.get("lat"))
                lng = float(r.raw.get("lng"))
                formatted = r.raw.get("formatted_address") or r.origin_resolved or address
                out = {"ok": True, "lat": lat, "lng": lng, "formatted_address": formatted}
                _CACHE.set(cache_key, out)
                return out
            last_err = r.error or "?"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

    if fallback_spec and fallback_key:
        try:
            r = await fallback_spec.geocode_fn(address, fallback_key, DEFAULT_TIMEOUT_S)
            if r.ok:
                lat = float(r.raw.get("lat"))
                lng = float(r.raw.get("lng"))
                formatted = r.raw.get("formatted_address") or r.origin_resolved or address
                out = {"ok": True, "lat": lat, "lng": lng, "formatted_address": formatted}
                _CACHE.set(cache_key, out)
                return out
            last_err = (last_err + " | " if last_err else "") + (r.error or "?")
        except Exception as e:  # noqa: BLE001
            last_err = (last_err + " | " if last_err else "") + f"{type(e).__name__}: {e}"

    return {"ok": False, "error": last_err or "no keys configured"}


# ============================================================
# 导出
# ============================================================

__all__ = [
    "VENDORS",
    "_register",
    "VendorSpec",
    "VendorResult",
    "MapsKeys",
    "detect_region",
    "get_keys",
    "set_key",
    "eta",
    "geocode",
    "clear_cache",
    "KEYS_FILE",
    "SUPPORTED_MODES",
]
