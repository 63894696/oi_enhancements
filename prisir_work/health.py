"""web health 诊断 v0.1.0 (P2.5+18a, 2026-09-23)

返回 web 子系统状态:
  - fetcher 注册名单(名字 + 是否 mock)
  - cache 目录可写性(写一个临时文件测试)
  - 当前 endpoint 数 + capability 数
  - 默认 web_search provider 名单
  - learned fetcher 优先级(tune 用,见 P2.5+18b)

任何异常 → 字段标 'unavailable',绝不抛。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_dir_writable() -> dict:
    """检查 cache 目录可写性 + 现有条目数。"""
    try:
        from prisir_work import cache as _cache
        d = _cache.cache_dir()
        # 写一个临时文件测可写
        writable = False
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=".health_probe_", dir=str(d))
            try:
                with os.fdopen(fd, "w") as f:
                    f.write("ok")
                writable = True
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception:
            writable = False
        # 数 cache 文件
        try:
            count = sum(1 for _ in d.glob("*.json"))
        except Exception:
            count = -1
        return {"ok": writable, "path": str(d), "entries": count}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _fetcher_health() -> dict:
    """列出已注册 fetcher,标 mock/real。"""
    try:
        from prisir_work import web_fetch as _wf
        fetchers = list(_wf._FETCHERS.keys())
        return {
            "ok": True,
            "count": len(fetchers),
            "names": fetchers,
            "has_mocks": any("mock" in n.lower() for n in fetchers),
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _search_provider_health() -> dict:
    """列出 web_search 已注册 provider。"""
    try:
        from prisir_work import web_search as _ws
        providers = list(_ws._PROVIDERS.keys())
        return {
            "ok": True,
            "count": len(providers),
            "names": providers,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _endpoint_health() -> dict:
    """列出 endpoint 白名单 + capability 数量。"""
    try:
        from prisir_work import endpoints as _ep
        from prisir_work import capability as _cap
        paths = [e["path"] for e in _ep.catalog()]
        caps = _cap.list_capabilities()
        web_paths = [p for p in paths if p.startswith("/web/")]
        web_caps = [c["id"] for c in caps if c["id"].startswith("web.")]
        return {
            "ok": True,
            "total_endpoints": len(paths),
            "total_capabilities": len(caps),
            "web_endpoints": web_paths,
            "web_capabilities": web_caps,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _tune_health() -> dict:
    """per-domain learned fetcher 优先级(P2.5+18b 用,无文件时返 unavailable)。"""
    try:
        from prisir_work import cache as _cache
        d = _cache.cache_dir()
        tune_path = d.parent / "tune.json"
        if not tune_path.exists():
            return {"ok": True, "tuned_domains": 0, "path": str(tune_path),
                    "note": "no learned data yet"}
        try:
            data = json.loads(tune_path.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "corrupted_file", "path": str(tune_path)}
        if not isinstance(data, dict):
            return {"ok": False, "error": "bad_format"}
        domains = list(data.keys())
        return {
            "ok": True,
            "tuned_domains": len(domains),
            "domains_sample": domains[:5],
            "path": str(tune_path),
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def web_health() -> dict:
    """聚合 web 子系统健康状态。

    Returns:
    {
        'ok': bool,                    # 整体 OK(所有子项 ok=True)
        'checked_at': str,             # UTC ISO
        'cache': {...},
        'fetchers': {...},
        'search_providers': {...},
        'endpoints_capabilities': {...},
        'tune': {...},
        'warnings': list[str]
    }
    """
    warnings: list[str] = []

    def _safe(fn):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": type(e).__name__}, type(e).__name__)

    cache, cache_err = _safe(_cache_dir_writable)
    if cache_err:
        warnings.append(f"cache_error:{cache_err}")
    fetchers, _ = _safe(_fetcher_health)
    providers, _ = _safe(_search_provider_health)
    ep_cap, _ = _safe(_endpoint_health)
    tune, _ = _safe(_tune_health)

    if not cache.get("ok"):
        warnings.append("cache_unwritable")
    if not fetchers.get("ok"):
        warnings.append("fetcher_list_error")
    if fetchers.get("count", 0) == 0:
        warnings.append("no_fetchers_registered")

    all_ok = (cache.get("ok") and fetchers.get("ok") and
              providers.get("ok") and ep_cap.get("ok") and tune.get("ok"))

    return {
        "ok": bool(all_ok),
        "checked_at": _now_iso(),
        "cache": cache,
        "fetchers": fetchers,
        "search_providers": providers,
        "endpoints_capabilities": ep_cap,
        "tune": tune,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import sys as _sys
    import json as _json
    print(_json.dumps(web_health(), ensure_ascii=False, indent=2))