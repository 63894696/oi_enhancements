# -*- coding: utf-8 -*-
"""cache 查询门面 v0.1.0 (P2.5+17a, 2026-09-23)

对 wigolo cache 工具的 Prisir 对应:列已缓存 URL、按 host 聚合、强制失效、命中统计。
基于 prisir_work.cache(7d 磁盘 JSON 存储层)的查询接口,不改写存储层。

任何 IO/解析异常 → 吞掉,返空 list / 零计数(失败降级,绝不抛)。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from prisir_work import cache as _cache_mod

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, **extra) -> dict:
    out = {"step": name, "ok": True, "duration_ms": 0}
    out.update(extra)
    return out


def _read_record(path) -> dict | None:
    """读 cache 单条记录,异常返 None。"""
    try:
        if not path.exists() or not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _parse_iso_safe(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        return datetime.fromisoformat(s2)
    except (ValueError, TypeError):
        return None


def cache_list(host: str | None = None, limit: int = 20, include_expired: bool = False) -> dict:
    """列出已缓存条目。

    Args:
        host: 限定 host(模糊匹配,如 'github.com');None = 全部
        limit: 最多返几条
        include_expired: 是否包含过期条目(默认 False,只返存活)

    Returns:
    {
        'ok': True,
        'entries': [{'url', 'host', 'fetched_at', 'expires_at', 'size_bytes', 'expired'}],
        'total': int,                   # 扫到的总数(已应用 host/limit 之前)
        'limit': int,
        'host_filter': str | None,
        'steps': [...]
    }
    """
    t0 = _now_ms()
    entries: list[dict] = []
    total_scanned = 0
    host_norm = host.lower() if host else None

    try:
        d = _cache_mod.cache_dir()
        if not d.exists():
            return {"ok": True, "entries": [], "total": 0, "limit": limit,
                    "host_filter": host_norm, "steps": [_step("scan", found=0,
                                                              duration_ms=_now_ms() - t0)]}

        # 用 scandir 比 iterdir 快一点,且能拿 stat
        with os.scandir(d) as it:
            for entry in it:
                if not entry.name.endswith(".json"):
                    continue
                total_scanned += 1
                p = d / entry.name
                rec = _read_record(p)
                if rec is None:
                    continue
                url = rec.get("url", "")
                if not url:
                    continue
                # host 过滤
                try:
                    h = (urlparse(url).hostname or "").lower()
                except Exception:
                    h = ""
                if host_norm and host_norm not in h:
                    continue
                exp_dt = _parse_iso_safe(str(rec.get("expires_at", "")))
                now = datetime.now(timezone.utc)
                expired = exp_dt is None or exp_dt <= now
                if expired and not include_expired:
                    continue
                # 文件大小
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                entries.append({
                    "url": url,
                    "host": h,
                    "fetched_at": rec.get("fetched_at", ""),
                    "expires_at": rec.get("expires_at", ""),
                    "size_bytes": size,
                    "expired": bool(expired),
                })
                if len(entries) >= limit:
                    break
    except Exception as e:  # noqa: BLE001
        log.warning("cache_list scan error: %s", e)

    # 最新优先
    entries.sort(key=lambda x: x.get("fetched_at", ""), reverse=True)
    return {
        "ok": True,
        "entries": entries,
        "total": len(entries),
        "limit": limit,
        "host_filter": host_norm,
        "steps": [_step("scan", scanned=total_scanned, kept=len(entries),
                        duration_ms=_now_ms() - t0)],
    }


def cache_invalidate(url: str | None = None, host: str | None = None,
                     all_expired: bool = False) -> dict:
    """强制删除缓存条目。

    至少需要 url / host / all_expired 之一,否则什么都不做。

    Returns:
    {
        'ok': True,
        'deleted': int,        # 实际删的条目数
        'mode': 'url' | 'host' | 'all_expired' | 'noop',
        'steps': [...]
    }
    """
    t0 = _now_ms()
    deleted = 0
    mode = "noop"

    if not url and not host and not all_expired:
        return {"ok": True, "deleted": 0, "mode": "noop",
                "steps": [_step("invalidate", deleted=0, duration_ms=_now_ms() - t0)]}

    try:
        d = _cache_mod.cache_dir()
        if not d.exists():
            return {"ok": True, "deleted": 0, "mode": mode,
                    "steps": [_step("invalidate", deleted=0, duration_ms=_now_ms() - t0)]}

        with os.scandir(d) as it:
            for entry in list(it):
                if not entry.name.endswith(".json"):
                    continue
                p = d / entry.name
                rec = _read_record(p)
                if rec is None:
                    continue
                url_v = rec.get("url", "")
                if not url_v:
                    continue
                # 决策删谁
                hit = False
                if url and url_v == url:
                    hit = True
                    mode = "url"
                elif host:
                    try:
                        h = (urlparse(url_v).hostname or "").lower()
                        if host.lower() in h:
                            hit = True
                            mode = "host"
                    except Exception:
                        pass
                elif all_expired:
                    exp_dt = _parse_iso_safe(str(rec.get("expires_at", "")))
                    now = datetime.now(timezone.utc)
                    if exp_dt is None or exp_dt <= now:
                        hit = True
                        mode = "all_expired"
                if hit:
                    try:
                        p.unlink()
                        deleted += 1
                    except OSError as e:
                        log.warning("cache_invalidate unlink failed: %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("cache_invalidate scan error: %s", e)

    return {"ok": True, "deleted": deleted, "mode": mode,
            "steps": [_step("invalidate", deleted=deleted, mode=mode,
                            duration_ms=_now_ms() - t0)]}


def cache_stats() -> dict:
    """返回缓存统计:总数/大小/按 host 聚合 Top/过期条目数。

    Returns:
    {
        'ok': True,
        'total_entries': int,
        'expired_entries': int,
        'total_bytes': int,
        'cache_dir': str,
        'by_host': [{'host', 'count', 'bytes'}, ...],   # Top 10
        'steps': [...]
    }
    """
    t0 = _now_ms()
    total = 0
    expired = 0
    total_bytes = 0
    host_agg: dict[str, dict[str, int]] = {}
    cache_dir_str = ""

    try:
        d = _cache_mod.cache_dir()
        cache_dir_str = str(d)
        if not d.exists():
            return {"ok": True, "total_entries": 0, "expired_entries": 0,
                    "total_bytes": 0, "cache_dir": cache_dir_str, "by_host": [],
                    "steps": [_step("stats", duration_ms=_now_ms() - t0)]}
        now = datetime.now(timezone.utc)
        with os.scandir(d) as it:
            for entry in it:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                rec = _read_record(d / entry.name)
                if rec is None:
                    continue
                url_v = rec.get("url", "")
                if not url_v:
                    continue
                total += 1
                total_bytes += size
                exp_dt = _parse_iso_safe(str(rec.get("expires_at", "")))
                if exp_dt is None or exp_dt <= now:
                    expired += 1
                try:
                    h = (urlparse(url_v).hostname or "").lower() or "unknown"
                except Exception:
                    h = "unknown"
                agg = host_agg.setdefault(h, {"count": 0, "bytes": 0})
                agg["count"] += 1
                agg["bytes"] += size
    except Exception as e:  # noqa: BLE001
        log.warning("cache_stats scan error: %s", e)

    by_host = sorted(
        [{"host": h, "count": v["count"], "bytes": v["bytes"]} for h, v in host_agg.items()],
        key=lambda x: (-x["count"], x["host"]),
    )[:10]

    return {
        "ok": True,
        "total_entries": total,
        "expired_entries": expired,
        "total_bytes": total_bytes,
        "cache_dir": cache_dir_str,
        "by_host": by_host,
        "steps": [_step("stats", total=total, expired=expired,
                        duration_ms=_now_ms() - t0)],
    }


if __name__ == "__main__":
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "stats"
    if cmd == "list":
        print(json.dumps(cache_list(host=_sys.argv[2] if len(_sys.argv) > 2 else None),
                         ensure_ascii=False, indent=2))
    elif cmd == "invalidate":
        target = _sys.argv[2] if len(_sys.argv) > 2 else None
        print(json.dumps(cache_invalidate(url=target), ensure_ascii=False, indent=2))
    elif cmd == "stats":
        print(json.dumps(cache_stats(), ensure_ascii=False, indent=2))
    else:
        print('usage: python -m prisir_work.cache_query {list|invalidate|stats} [args]')
        raise SystemExit(2)