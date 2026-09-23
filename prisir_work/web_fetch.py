"""统一抓取门面 — 多 fetcher 并发竞速 + 7d 本地缓存 + 内存 LRU。

设计原则(用户拍板):「信息来源途径越多越好,先有东西再处理」 —
任何 provider / fetcher / IO 失败/超时,绝对不 raise;降级返空或缓存穿透。

公开 API:
  · register_fetcher(name, fn)  — 注册抓取器
  · fetch(url, options, timeout)  — 统一入口:缓存 → 并发竞速 → 缓存落盘

默认 fetcher:
  · http_urllib              — urllib.request(带 gzip + UA + 状态码校验)
  · a11y_provider            — 本地 file:// / Windows 路径 走 a11y 提取
  · browser_use_cli_provider  — http/https 走浏览器自动化兜底
"""
from __future__ import annotations

import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import Any, Callable
from urllib.parse import urlparse

from . import cache

# P2.5+18b:per-domain fetcher 优先级学习(可选依赖,缺则跳过)
try:
    from . import tune as _tune  # noqa: F401
except Exception:
    _tune = None  # type: ignore

FetcherFn = Callable[[str, dict], dict[str, Any]]
# 内部 fetcher 注册表
_FETCHERS: dict[str, FetcherFn] = {}
# 内存 LRU 兜底(避免每次都打磁盘)。max 32,key = sha1(url)
_MEM_CACHE: dict[str, dict[str, Any]] = {}
_MEM_CACHE_MAX = 32
# 缓存目录名(便于单测 monkeypatch)
_CACHE_DIR_NAME = "web"


# ---------------------------------------------------------------------------
# fetcher 注册
# ---------------------------------------------------------------------------

def register_fetcher(name: str, fn: FetcherFn) -> None:
    """注册一个 fetcher。fn(url, options) -> {content: str, meta: dict}。

    失败时 fn 自己负责返 meta.error / 空 content,不要 raise(框架层也会兜底再吞)。
    """
    _FETCHERS[name] = fn


def _safe_call(name: str, fn: FetcherFn, url: str, options: dict, timeout: float) -> dict | None:
    """调一次 fetcher,任何异常 → 返 None(让上层继续等其它 fetcher)。"""
    try:
        # fetcher 自己负责吃 timeout,这里只设一个上限
        result = fn(url, dict(options or {}))
        if not isinstance(result, dict):
            return None
        if "content" not in result:
            result.setdefault("content", "")
        if "meta" not in result or not isinstance(result.get("meta"), dict):
            result["meta"] = {}
        # 强制标 fetcher 名(以注册名为准,防 fetcher 内部标错)
        result["meta"].setdefault("fetcher", name)
        # 失败判定:content 非空 + meta 无 error 视为成功
        if not result["content"]:
            # 空内容也算 fail(除非显式 ok=True)
            if not result["meta"].get("ok"):
                return None
        return result
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 默认 fetcher 实现
# ---------------------------------------------------------------------------

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/127.0 Safari/537.36")


def http_urllib(url: str, options: dict) -> dict[str, Any]:
    """urllib 抓取:自动 gzip + UA + 2xx 校验。

    非 2xx / 网络异常 → meta.error="http_xxx"。
    """
    import gzip
    import io
    import urllib.error
    import urllib.request

    timeout = float(options.get("timeout", 10.0))
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200) or 200
            headers = {k: v for k, v in resp.headers.items()}
            ce = (headers.get("Content-Encoding") or "").lower().strip()
            if "gzip" in ce:
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except Exception:
                    pass
            elif "deflate" in ce:
                try:
                    import zlib
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    try:
                        import zlib
                        raw = zlib.decompress(raw)
                    except Exception:
                        pass
            try:
                content = raw.decode("utf-8", errors="replace")
            except Exception:
                content = ""
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            meta: dict[str, Any] = {
                "status": int(status),
                "headers": headers,
                "elapsed_ms": elapsed_ms,
                "fetcher": "http_urllib",
            }
            if 200 <= int(status) < 300 and content:
                meta["ok"] = True
                return {"content": content, "meta": meta}
            meta["error"] = f"http_{status}"
            return {"content": "", "meta": meta}
    except urllib.error.HTTPError as e:
        return {"content": "", "meta": {
            "fetcher": "http_urllib",
            "error": f"http_{e.code}",
            "status": int(getattr(e, "code", 0) or 0),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }}
    except urllib.error.URLError as e:
        return {"content": "", "meta": {
            "fetcher": "http_urllib",
            "error": "http_url_error",
            "detail": str(getattr(e, "reason", e)),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }}
    except Exception as e:
        return {"content": "", "meta": {
            "fetcher": "http_urllib",
            "error": f"http_{type(e).__name__}",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }}


def a11y_provider(url: str, options: dict) -> dict[str, Any]:
    """本地 file:// / Windows 路径 → a11y 提取可访问性文本。

    其它 scheme(http/https)直接返 a11y_unavailable。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    path = parsed.path or ""
    # 只处理本地
    is_local = scheme in ("file", "") or (sys.platform.startswith("win") and path.startswith("\\"))
    if not is_local:
        return {"content": "", "meta": {"fetcher": "a11y", "error": "a11y_unavailable",
                                        "reason": "non_local_url"}}
    try:
        import a11y_extract  # type: ignore
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "a11y", "error": "a11y_unavailable",
                                        "detail": type(e).__name__}}
    if not hasattr(a11y_extract, "extract"):
        return {"content": "", "meta": {"fetcher": "a11y", "error": "a11y_unavailable",
                                        "reason": "no_extract_fn"}}
    try:
        result = a11y_extract.extract(url)
        if isinstance(result, dict):
            content = str(result.get("text") or result.get("content") or "")
            return {"content": content, "meta": {"fetcher": "a11y", "ok": True}}
        return {"content": str(result or ""), "meta": {"fetcher": "a11y", "ok": True}}
    except NotImplementedError:
        return {"content": "", "meta": {"fetcher": "a11y", "error": "a11y_unavailable",
                                        "reason": "not_implemented"}}
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "a11y", "error": "a11y_failed",
                                        "detail": type(e).__name__}}


def browser_use_cli_provider(url: str, options: dict) -> dict[str, Any]:
    """http/https 走浏览器自动化(借 browser_use_cli 模块)。

    非 http(s) → browser_use_unavailable。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return {"content": "", "meta": {"fetcher": "browser_use_cli",
                                        "error": "browser_use_unavailable",
                                        "reason": "non_http_url"}}
    try:
        import browser_use_cli  # type: ignore
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "browser_use_cli",
                                        "error": "browser_use_unavailable",
                                        "detail": type(e).__name__}}
    fn = getattr(browser_use_cli, "fetch", None)
    if not callable(fn):
        return {"content": "", "meta": {"fetcher": "browser_use_cli",
                                        "error": "browser_use_unavailable",
                                        "reason": "no_fetch_fn"}}
    try:
        result = fn(url, dict(options or {}))
        if isinstance(result, dict):
            content = str(result.get("content") or "")
            return {"content": content, "meta": dict(result.get("meta") or {"fetcher": "browser_use_cli"})}
        return {"content": str(result or ""), "meta": {"fetcher": "browser_use_cli", "ok": True}}
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "browser_use_cli",
                                        "error": "browser_use_failed",
                                        "detail": type(e).__name__}}


# ---------------------------------------------------------------------------
# 内存 LRU
# ---------------------------------------------------------------------------

def _mem_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _mem_get(url: str) -> dict | None:
    return _MEM_CACHE.get(_mem_key(url))


def _mem_put(url: str, record: dict) -> None:
    key = _mem_key(url)
    if key in _MEM_CACHE:
        return
    _MEM_CACHE[key] = record
    if len(_MEM_CACHE) > _MEM_CACHE_MAX:
        # FIFO 淘汰(dict 有序,Python3.7+)
        try:
            first = next(iter(_MEM_CACHE))
            # 跳过刚加的
            keys = list(_MEM_CACHE.keys())
            for k in keys:
                if k != key:
                    _MEM_CACHE.pop(k, None)
                    break
        except Exception:
            pass


def _mem_clear() -> None:
    _MEM_CACHE.clear()


# ---------------------------------------------------------------------------
# URL 规范化(去 fragment,缓存命中更稳)
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    if not url:
        return url
    try:
        p = urlparse(url)
        # 去 fragment,query 保留
        if not p.fragment:
            return url
        # 重组
        from urllib.parse import urlunparse
        return urlunparse(p._replace(fragment=""))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# 公开主入口
# ---------------------------------------------------------------------------

def fetch(url: str, options: dict | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """统一抓取:缓存 → 并发竞速 → 落盘缓存。

    options 支持:
        no_cache: bool — 跳过 mem + disk cache,直接走 fetcher(默认 False)

    返 {url, content, meta, fetcher, cached, fetched_at}。
    任何异常不抛(用户拍板)。
    """
    options = dict(options or {})
    no_cache = bool(options.get("no_cache", False))
    base_meta: dict[str, Any] = {
        "fetcher": "",
        "cached": False,
    }
    if not isinstance(url, str) or not url.strip():
        return {"url": url or "", "content": "", "meta": {"error": "bad_url"},
                "fetcher": "", "cached": False, "fetched_at": "", "ok": False}

    norm_url = _normalize_url(url)
    now_iso = ""

    # 1. 内存 LRU(no_cache 跳过)
    if not no_cache:
        try:
            mem_hit = _mem_get(norm_url)
            if mem_hit:
                mem_hit["cached"] = True
                return mem_hit
        except Exception:
            pass

    # 2. 磁盘缓存(no_cache 跳过)
    cached = None
    if not no_cache:
        try:
            cached = cache.cache_get(norm_url)
        except Exception:
            cached = None
    if cached and isinstance(cached, dict):
        payload = cached.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        content = str(payload.get("content") or "")
        meta = dict(payload.get("meta") or {})
        meta.setdefault("fetcher", meta.get("fetcher", "cache"))
        record = {
            "url": norm_url,
            "content": content,
            "meta": meta,
            "fetcher": meta.get("fetcher", "cache"),
            "cached": True,
            "fetched_at": cached.get("fetched_at", ""),
            "ok": bool(content),
        }
        try:
            _mem_put(norm_url, record)
        except Exception:
            pass
        return record

    # 3. 并发跑所有 fetcher
    if not _FETCHERS:
        # 没人注册 → 自动装默认三个
        register_fetcher("http_urllib", http_urllib)
        register_fetcher("a11y", a11y_provider)
        register_fetcher("browser_use_cli", browser_use_cli_provider)

    # P2.5+18b:查 host 的 learned 优先级(tune.json 命中 → 只跑 learned 列表)
    learned: list[str] | None = None
    learned_source = None  # None=全并发; "tune"=走 learned
    try:
        if _tune is not None:
            parsed = urlparse(norm_url)
            host = (parsed.hostname or "").lower()
            if host:
                learned = _tune.recommend(host)
                if learned:
                    learned_source = "tune"
    except Exception:
        learned = None
        learned_source = None

    if learned_source == "tune" and learned:
        fetchers = [(n, _FETCHERS[n]) for n in learned if n in _FETCHERS]
        if not fetchers:  # learned 列表里的 fetcher 全没了 → 回退全并发
            fetchers = list(_FETCHERS.items())
            learned_source = None
    else:
        fetchers = list(_FETCHERS.items())
    results: dict[str, dict | None] = {}
    have_success = False

    def _run_one(name_fn: tuple[str, FetcherFn]) -> tuple[str, dict | None]:
        name, fn = name_fn
        return name, _safe_call(name, fn, norm_url, options, timeout)

    pool = None
    try:
        workers = max(1, min(4, len(fetchers)))
        deadline = max(0.1, float(timeout))
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {pool.submit(_run_one, nf): nf[0] for nf in fetchers}
        try:
            for fut in as_completed(futures, timeout=deadline):
                name = futures[fut]
                try:
                    n, r = fut.result(timeout=0.05)
                    results[n] = r
                    if r and r.get("content"):
                        have_success = True
                        # 拿到第一个成功结果立刻跳出,不让其它 fetcher 拖时间
                        break
                except FutureTimeout:
                    results[name] = None
                except Exception:
                    results[name] = None
        except FutureTimeout:
            # 全 timeout 或卡死,继续
            pass
        # 仍未填的 future(因 break 提前退出)→ None,后台线程跑完就丢
        for n in futures.values():
            if n not in results:
                results[n] = None
    except Exception:
        # 兜底:并发跑不起来就串行(每个 fetcher 单独给 timeout 上限)
        for nf in fetchers:
            try:
                n, r = _run_one(nf)
                results[n] = r
                if r and r.get("content") and not have_success:
                    have_success = True
            except Exception:
                results[n] = None
    finally:
        if pool is not None:
            try:
                # 不等后台慢 fetcher(shutdown(wait=False) 让 Python 进程退出时丢)
                pool.shutdown(wait=False)
            except Exception:
                pass

    # 选最快成功
    chosen_name, chosen_result = None, None
    for n, r in results.items():
        if r and isinstance(r, dict) and r.get("content"):
            if chosen_result is None:
                chosen_name, chosen_result = n, r

    if not chosen_result:
        # 全失败降级
        return {
            "url": norm_url,
            "content": "",
            "meta": {"error": "all_failed", "attempts": {
                n: (r.get("meta", {}) if isinstance(r, dict) else {"error": "raised"})
                for n, r in results.items()
            }},
            "fetcher": "",
            "cached": False,
            "fetched_at": "",
            "ok": False,
        }

    # 4. 落盘缓存
    meta = chosen_result.get("meta", {})
    payload = {"content": chosen_result.get("content", ""), "meta": meta}
    try:
        cache.cache_put(norm_url, payload)
    except Exception:
        pass

    # P2.5+18b:把每个 fetcher 的尝试结果累加到 tune 累加器 + 触发落盘
    # 只 record 真跑完的 fetcher(已经返 result 的);None = 没跑完/在途,不 record,
    # 否则 break 后未完成的 fetcher 会误记成 fail,污染学习。
    if _tune is not None:
        try:
            for n, r in results.items():
                if r is None or not isinstance(r, dict):
                    continue  # 未跑完/抛了 → 跳过,不污染学习
                rm = r.get("meta") if isinstance(r.get("meta"), dict) else {}
                ok = bool(r.get("content")) and not rm.get("error")
                ms = int(rm.get("elapsed_ms") or 0)
                _tune.record(norm_url, n, ms, ok=ok)
            # 顺手触发落盘(累加器已就绪就写)
            _tune.flush_if_ready()
        except Exception:
            pass
    now_iso = ""
    try:
        # 复用 cache 的时间戳生成(避免再 import)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    record = {
        "url": norm_url,
        "content": chosen_result.get("content", ""),
        "meta": meta,
        "fetcher": chosen_name or meta.get("fetcher", ""),
        "cached": False,
        "fetched_at": now_iso,
        "ok": bool(chosen_result.get("content")),
    }
    try:
        _mem_put(norm_url, record)
    except Exception:
        pass
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys
    _url = _sys.argv[1] if len(_sys.argv) > 1 else ""
    print(_json.dumps(fetch(_url), ensure_ascii=False, indent=2))