"""web_search.py — P2.5+16 多 provider 搜索引擎门面,内置 RRF rank fusion。

定位(见 prisirwork-foundation-integration-design §3 / F1 能力门面扩展):
- 让 agent / 扩展 / shell 在不知道后端 provider 的前提下发起网络搜索。
- 通过 register_provider() 注入多个数据源(内置:ddg_html / baidu / bing_public /
  可选 tavily / serper),search() 并发跑全部数据源 + RRF 融合去重排序。
- 「降级而非崩溃」:任何 provider 失败/超时/网络断都被吞,只把成功的合入结果。
  全失败 → 返空 list(绝不 raise)。空 query → []。无 provider → []。

RRF(Reciprocal Rank Fusion):
    score(doc) = Σ  1 / (k + rank_in_provider)
    k = 60(经典默认)
- URL 去重:url_normalize(u) = strip fragment + lowercase host + strip trailing slash
- 排序:score 降序,取 top limit。

并发:
    每个 provider 一个 daemon 线程,主线程 join(timeout) 等结果;超时未完成的
    provider 视为空结果(线程变孤儿,后台自然结束 / GC,资源泄漏可控)。

缓存:
    模块级 LRU dict(max 32, ttl 300s),key = md5(query + str(limit) + ','.join(sorted(providers)))。
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

__all__ = [
    "register_provider",
    "search",
    "url_normalize",
    "clear_cache",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# provider 注册表
# ---------------------------------------------------------------------------

# 每个 provider: name -> Callable[[query: str, limit: int], list[dict]]
# 返回 list[{"url","title","snippet"}]
_PROVIDERS: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {}

# 默认 UA(避免被 baidu / bing 当作 bot 直接挡)
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


def register_provider(name: str, fn: Callable[[str, int], list[dict[str, Any]]]) -> None:
    """登记一个 provider 函数。"""
    _PROVIDERS[name] = fn


def _registered_providers() -> dict[str, Callable[[str, int], list[dict[str, Any]]]]:
    return dict(_PROVIDERS)


# ---------------------------------------------------------------------------
# URL 规范化
# ---------------------------------------------------------------------------


def url_normalize(u: str) -> str:
    """strip fragment + lowercase host + strip trailing slash。

    用于 LRU 缓存键 + RRF 去重键。注意:仅 strip 尾部 1 个 slash,保留 path。
    """
    if not u:
        return ""
    try:
        parsed = urllib.parse.urlparse(u)
    except Exception:
        return u.strip()
    # 丢掉 fragment
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # 端口显式标准化(若为默认 80/443 则清掉,否则保留)
    if netloc:
        host, sep, port = netloc.partition(":")
        if port in ("80", "443") and scheme in ("http", "https"):
            netloc = host
    path = parsed.path or ""
    # strip 尾部 slash(保留根目录的 /)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = parsed.query
    out = urllib.parse.urlunparse((scheme, netloc, path, parsed.params, query, ""))
    return out


# ---------------------------------------------------------------------------
# LRU 缓存
# ---------------------------------------------------------------------------

_CACHE_MAX = 32
_CACHE_TTL = 300.0
_LRU: "collections.OrderedDict[str, tuple[float, list[dict[str, Any]]]]" = collections.OrderedDict()


def _cache_key(query: str, limit: int, providers: list[str]) -> str:
    h = hashlib.md5()
    h.update(query.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(limit).encode("ascii"))
    h.update(b"\x00")
    h.update(",".join(sorted(providers)).encode("utf-8"))
    return h.hexdigest()


def _lru_get(key: str) -> list[dict[str, Any]] | None:
    now = time.monotonic()
    entry = _LRU.get(key)
    if entry is None:
        return None
    ts, value = entry
    if (now - ts) > _CACHE_TTL:
        # 过期
        try:
            del _LRU[key]
        except KeyError:
            pass
        return None
    # LRU bump
    _LRU.move_to_end(key)
    return value


def _lru_put(key: str, value: list[dict[str, Any]]) -> None:
    _LRU[key] = (time.monotonic(), value)
    _LRU.move_to_end(key)
    while len(_LRU) > _CACHE_MAX:
        _LRU.popitem(last=False)


def clear_cache() -> None:
    """清空 LRU(测试 / 调试用)。"""
    _LRU.clear()


# ---------------------------------------------------------------------------
# rank fusion (RRF)
# ---------------------------------------------------------------------------

_RRF_K = 60


def _rrf_fuse(provider_results: dict[str, list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """按 RRF 融合多 provider 结果。返回 score 降序,top limit。

    URL 去重基于 url_normalize;输出 url 字段统一为规范化后的版本。
    """
    # norm_url -> {norm_url, title, snippet, score, sources:set, ranks:dict}
    bucket: dict[str, dict[str, Any]] = {}
    for pname, items in provider_results.items():
        for rank, item in enumerate(items, start=1):
            url = item.get("url") or ""
            if not url:
                continue
            norm = url_normalize(url)
            if not norm:
                continue
            entry = bucket.get(norm)
            if entry is None:
                entry = {
                    "url": norm,
                    "title": item.get("title", "") or "",
                    "snippet": item.get("snippet", "") or "",
                    "score": 0.0,
                    "sources": set(),
                    "_ranks": {},
                }
                bucket[norm] = entry
            else:
                # 标题/snippet 取首个非空
                if not entry["title"] and item.get("title"):
                    entry["title"] = item["title"]
                if not entry["snippet"] and item.get("snippet"):
                    entry["snippet"] = item["snippet"]
            entry["score"] += 1.0 / (_RRF_K + rank)
            entry["sources"].add(pname)
            entry["_ranks"][pname] = rank

    out: list[dict[str, Any]] = []
    for entry in bucket.values():
        # 输出 sources 为 list,稳定排序
        sources = sorted(entry["sources"])
        out.append({
            "url": entry["url"],
            "title": entry["title"],
            "snippet": entry["snippet"],
            "sources": sources,
            "score": round(entry["score"], 6),
        })
    out.sort(key=lambda x: (-x["score"], x["url"]))
    return out[:limit]


# ---------------------------------------------------------------------------
# 并发调度 + 异常隔离
# ---------------------------------------------------------------------------


def _search_uncached(query: str, limit: int, providers: list[str], timeout: float) -> list[dict[str, Any]]:
    """实际并发跑 providers,内部不再查缓存。

    设计:
    - 每个 provider 一个 daemon 线程,主线程 join(timeout) 等结果。
    - 超时未完成的线程放弃等待(线程变孤儿,后台跑完即 GC);
      生产场景下,time.sleep 类阻塞无法在 Python 层强行中断,
      但网络请求多半因服务端超时 / TCP RST 自然结束,资源泄漏可控。
    - 任何 provider 抛异常 / 超时都不影响其余 provider。
    """
    if not query.strip() or not providers:
        return []

    reg = _registered_providers()
    selected: list[tuple[str, Callable[[str, int], list[dict[str, Any]]]]] = []
    for pname in providers:
        fn = reg.get(pname)
        if fn is None:
            _LOG.warning("web_search provider %s not registered, skip", pname)
            continue
        selected.append((pname, fn))

    if not selected:
        return []

    results: dict[str, list[dict[str, Any]]] = {}

    # 每个 provider 一份「结果容器」,线程写入,主线程 join 后读
    bag: dict[str, dict[str, Any]] = {pname: {"done": False, "value": []} for pname, _ in selected}
    # 用一组 Event 而不是 dict 锁,简单且线程安全
    done_events: dict[str, threading.Event] = {}

    threads: list[threading.Thread] = []

    def worker(pname: str, fn: Callable[[str, int], list[dict[str, Any]]]) -> None:
        evt = done_events[pname]
        t0 = time.monotonic()
        ok = True
        err_str = ""
        try:
            bag[pname]["value"] = fn(query, limit) or []
        except Exception as e:  # noqa: BLE001 - 全部吞
            ok = False
            err_str = f"{type(e).__name__}: {e}"
            _LOG.warning("web_search provider %s raised %s: %s", pname, type(e).__name__, e)
            bag[pname]["value"] = []
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            _Stats.record(pname, ok=ok, error=err_str, ms=elapsed_ms)
            bag[pname]["done"] = True
            evt.set()

    for pname, fn in selected:
        evt = threading.Event()
        done_events[pname] = evt
        t = threading.Thread(target=worker, args=(pname, fn), name=f"web_search-{pname}", daemon=True)
        t.start()
        threads.append(t)

    # 等待所有线程,总等待 = timeout(每个 provider 共享一个 timeout 预算)
    # 用 join(timeout * len(selected) / max_workers) 算一个保守上限,
    # 但为简单起见:用 timeout 直接 join 全部线程(超时未完成 → 视为 [] 返回)。
    deadline = time.monotonic() + timeout
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)
        if time.monotonic() >= deadline:
            break

    for pname, _ in selected:
        if not bag[pname]["done"]:
            _LOG.warning("web_search provider %s timed out, drop", pname)
            results[pname] = []
        else:
            results[pname] = bag[pname]["value"]

    return _rrf_fuse(results, limit)


def search(query: str, limit: int = 10,
           providers: list[str] | None = None,
           timeout: float = 8.0) -> list[dict[str, Any]]:
    """多 provider 并发 + RRF 融合 + LRU 缓存。

    返回 list[{"url","title","snippet","sources":[provider_name...],"score":float}]。
    空 query / 无 provider / 全部失败 → []。
    """
    if not query or not query.strip():
        return []
    reg = _registered_providers()
    if not reg:
        return []
    selected_providers = list(providers) if providers is not None else list(reg.keys())
    if not selected_providers:
        return []

    cache_key = _cache_key(query, limit, selected_providers)
    cached = _lru_get(cache_key)
    if cached is not None:
        return cached

    out = _search_uncached(query, limit, selected_providers, timeout)
    _lru_put(cache_key, out)
    return out


# ---------------------------------------------------------------------------
# 内置 provider
# ---------------------------------------------------------------------------


def _http_get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> str:
    """简单 GET,自动套 UA。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # 尝试 utf-8 → gb18030 兜底
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_DDG_RE_LINK = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_DDG_RE_SNIPPET = re.compile(
    r'class="result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|span)',
    re.DOTALL | re.IGNORECASE,
)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def ddg_html(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """DuckDuckGo HTML 版抓取(result__a / result__snippet)。"""
    if not query:
        return []
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        html = _http_get(url, timeout=8.0)
    except Exception as e:
        _LOG.warning("ddg_html fetch error: %s", e)
        return []

    # 1) 把所有链接抓出来,按出现顺序 rank
    links: list[tuple[str, str]] = []  # (href, title_html)
    for m in _DDG_RE_LINK.finditer(html):
        href = m.group(1)
        # DDG 链接常带 uddg= 二次编码
        if "uddg=" in href:
            try:
                parsed = urllib.parse.urlparse(href)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs:
                    href = urllib.parse.unquote(qs["uddg"][0])
            except Exception:
                pass
        title_html = m.group(2)
        title = _strip_html(re.sub(r"<[^>]+>", " ", title_html))
        if href and title:
            links.append((href, title))

    # 2) snippet 抓同样数量的 a-tag 之后紧跟的 snippet
    snippets: list[str] = []
    for m in _DDG_RE_SNIPPET.finditer(html):
        snippets.append(_strip_html(m.group(1)))

    out: list[dict[str, Any]] = []
    for i, (href, title) in enumerate(links[:limit]):
        snippet = snippets[i] if i < len(snippets) else ""
        out.append({"url": href, "title": title, "snippet": snippet})
    return out


_BAIDU_RE_LINK = re.compile(
    r'<a[^>]+class="result-title[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_BAIDU_RE_SNIPPET = re.compile(
    r'class="result-content[^"]*"[^>]*>(.*?)</(?:div|span)',
    re.DOTALL | re.IGNORECASE,
)


def baidu(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """百度网页搜索(简易抓取,可能被风控;失败/空都吞)。"""
    if not query:
        return []
    url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    try:
        html = _http_get(url, timeout=8.0, headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    except Exception as e:
        _LOG.warning("baidu fetch error: %s", e)
        return []

    out: list[dict[str, Any]] = []
    links = list(_BAIDU_RE_LINK.finditer(html))
    snippets = list(_BAIDU_RE_SNIPPET.finditer(html))
    for i, m in enumerate(links[:limit]):
        href = m.group(1)
        title = _strip_html(m.group(2))
        if not href or not title:
            continue
        snippet = _strip_html(snippets[i].group(1)) if i < len(snippets) else ""
        out.append({"url": href, "title": title, "snippet": snippet})
    return out


_BING_RE_LINK = re.compile(
    r'<li[^>]+class="b_algo"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_BING_RE_SNIPPET = re.compile(
    r'<p[^>]*>(.*?)</p>',
    re.DOTALL | re.IGNORECASE,
)


def bing_public(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Bing 国际版搜索(简易抓取)。"""
    if not query:
        return []
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    try:
        html = _http_get(url, timeout=8.0)
    except Exception as e:
        _LOG.warning("bing_public fetch error: %s", e)
        return []

    # 1) 收集 b_algo 容器
    out: list[dict[str, Any]] = []
    for m in _BING_RE_LINK.finditer(html):
        href = m.group(1)
        title = _strip_html(m.group(2))
        if not href or not title:
            continue
        # 紧跟的 <p> 段(简化:从 href 后面剩余找最近一个)
        rest = html[m.end():m.end() + 4000]
        snip_m = _BING_RE_SNIPPET.search(rest)
        snippet = _strip_html(snip_m.group(1)) if snip_m else ""
        out.append({"url": href, "title": title, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def tavily(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Tavily API(需 TAVILY_API_KEY)。失败返 []。"""
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    body = json.dumps({
        "api_key": api_key,
        "query": query,
        "max_results": max(1, min(limit, 20)),
        "include_answer": False,
        "search_depth": "basic",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _DEFAULT_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        _LOG.warning("tavily fetch error: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for r in data.get("results", []) or []:
        url = r.get("url") or ""
        if not url:
            continue
        out.append({
            "url": url,
            "title": r.get("title") or "",
            "snippet": r.get("content") or "",
        })
        if len(out) >= limit:
            break
    return out


def serper(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Serper.dev Google 搜索 API(需 SERPER_API_KEY)。失败返 []。"""
    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not api_key:
        return []
    body = json.dumps({"q": query, "num": max(1, min(limit, 20))}).encode("utf-8")
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
            "User-Agent": _DEFAULT_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        _LOG.warning("serper fetch error: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for r in data.get("organic", []) or []:
        url = r.get("link") or ""
        if not url:
            continue
        out.append({
            "url": url,
            "title": r.get("title") or "",
            "snippet": r.get("snippet") or "",
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# 启动时注册内置 provider
# ---------------------------------------------------------------------------

# 三个无外部依赖的 provider 总是注册
register_provider("ddg_html", ddg_html)
register_provider("baidu", baidu)
register_provider("bing_public", bing_public)

# 有 API key 才注册(env 缺失则不出现在默认列表)
if os.environ.get("TAVILY_API_KEY", "").strip():
    register_provider("tavily", tavily)
if os.environ.get("SERPER_API_KEY", "").strip():
    register_provider("serper", serper)

# P3j T20-I: jina search — URL→markdown 的姐妹端点 s.jina.ai
# 当 JINA_API_KEY 或 JINA_SEARCH_URL 存在时挂上(高质全文,query→top N)
if os.environ.get("JINA_API_KEY", "").strip() or \
   os.environ.get("JINA_SEARCH_URL", "").strip():
    def jina_search_provider(query: str, limit: int = 10) -> list[dict[str, Any]]:
        from prisir_work import web_fetch_jina as _jina
        items = _jina.jina_search(query, limit=limit)
        return [{"url": it["url"], "title": it["title"],
                 "snippet": it.get("snippet", "")[:300]}
                for it in items if it.get("url")]
    register_provider("jina_search", jina_search_provider)

# P3j T21-C: gh_search provider — 只在 gh CLI 已装时注册
# 走 gh search repos(kind 默认),结果转 {url, title, snippet} 给 web_search.merge
try:
    import shutil as _shutil_gh
    if _shutil_gh.which("gh"):
        def gh_search_provider(query: str, limit: int = 10) -> list[dict[str, Any]]:
            """GitHub 仓库搜索(gh search repos)。失败返 []。"""
            try:
                from prisir_work import gh_bridge as _gh
                return _gh.search(query, kind="repos", limit=min(max(limit, 1), 30))
            except Exception:  # noqa: BLE001
                return []
        register_provider("gh_search", gh_search_provider)
except Exception:  # noqa: BLE001
    pass  # gh CLI 不在,跳过注册

# P3j T22-A: exa_search provider — 只在 EXA_API_KEY env 在时注册
# Exa 语义搜索,embedding + LLM 重排序,$0.005/次 适合研究
try:
    if os.environ.get("EXA_API_KEY", "").strip():
        def exa_search_provider(query: str, limit: int = 10) -> list[dict[str, Any]]:
            """Exa 语义搜索。失败返 []。"""
            try:
                from prisir_work import exa_bridge as _ex
                r = _ex.exa_search(query, num_results=min(max(limit, 1), 30),
                                   max_chars=2000)
                if not r.get("ok"):
                    return []
                return [{"url": it["url"], "title": it["title"],
                         "snippet": it.get("snippet", "")[:300]}
                        for it in r.get("results", []) if it.get("url")]
            except Exception:  # noqa: BLE001
                return []
        register_provider("exa_search", exa_search_provider)
except Exception:  # noqa: BLE001
    pass  # Exa key 不在,跳过注册


# ---------------------------------------------------------------------------
# P3j T22-B: HackerNews(Algolia API 免 key,始终注册)
# ---------------------------------------------------------------------------

def hn_search_provider(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """HN search_by_date → 标准 search result list。"""
    try:
        from prisir_work import hn_bridge as _hn
        r = _hn.hn_search(query, sort="by_date",
                         limit=min(max(limit, 1), 30),
                         min_points=5,           # 过滤低分帖子
                         timeout=10.0)
        if not r.get("ok"):
            return []
        return [{"url": it["url"], "title": it["title"],
                 "snippet": (it.get("snippet", "")
                             or f"{it.get('points', 0)}pt · "
                                f"{it.get('num_comments', 0)}cmt")}
                for it in r.get("results", []) if it.get("url")]
    except Exception:  # noqa: BLE001
        return []

register_provider("hn_search", hn_search_provider)


# ---------------------------------------------------------------------------
# P3j T23: 引擎健康度观测 + 借鉴 SearXNG 的 76 个无 key 引擎批量注册
# ---------------------------------------------------------------------------


class _Stats:
    """每 provider 最近一次调用状态(用于 ship 治理 + 降权决策)。

    - record() 在 _search_uncached.worker 内每 provider 调一次
    - snapshot() 返 dict 给外部观测(stats())
    - reset() 仅测试
    """
    _DATA: dict[str, dict[str, Any]] = {}

    @classmethod
    def record(cls, name: str, *, ok: bool, error: str = "",
               ms: float = 0.0) -> None:
        d = cls._DATA.setdefault(name, {
            "call_count": 0, "fail_count": 0,
            "last_status": "unknown", "last_error": "",
            "last_called_at": 0.0, "last_ms": 0.0,
        })
        d["call_count"] += 1
        d["last_called_at"] = time.monotonic()
        d["last_ms"] = ms
        if ok:
            d["last_status"] = "ok"
            d["fail_count"] = max(0, d["fail_count"] - 1)  # 成功减半累积
        else:
            d["fail_count"] += 1
            d["last_status"] = "fail"
            d["last_error"] = error[:200]

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        return {
            "total": len(cls._DATA),
            "providers": {k: dict(v) for k, v in cls._DATA.items()},
        }

    @classmethod
    def reset(cls) -> None:
        cls._DATA.clear()


def stats() -> dict[str, Any]:
    """返回每 provider 最近一次调用的状态(用于后续 ship 降权 / 退役决策)。"""
    return _Stats.snapshot()


def reset_stats() -> None:
    """清空观测(测试用)。"""
    _Stats.reset()


def _register_searxng_engines() -> None:
    """import 全部 search_engines 子模块并触发 register_all()。

    失败静默:子模块 import 异常不会影响现有内置 9 provider。
    """
    try:
        from prisir_work.search_engines import (
            general, academic, code, wikipedia,
            media, images, news, maps, specialty,
        )
        for mod in (general, academic, code, wikipedia,
                    media, images, news, maps, specialty):
            mod.register_all()
    except Exception as e:  # noqa: BLE001 - 静默,主对话不依赖
        _LOG.warning("search_engines auto-register failed: %s", e)


_register_searxng_engines()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else ""
    print(json.dumps(search(q, limit=10), ensure_ascii=False, indent=2))
