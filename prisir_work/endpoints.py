"""endpoint 白名单注册表(红线③)。

PrisirWork 只暴露这里列名的端点;其余一律 404。
不开放任意 shell、不开放 Electrum 全量 RPC——只有经这里登记、
且标了风险级别的方法才可达。

每个 entry:
  method  : HTTP 方法("GET" / "POST")
  risk    : "L0" 只读免确认 / "L1" 内嵌卡 / "L2" 全回显 / "L3" 安全对话框(口令)
            —— 确认门槛在扩展侧;此处标注供扩展渲染对应确认卡,PrisirWork 不替用户决定。
  auth    : 是否需要 X-OI-Token(默认全部需要;只有 /health 探活可免)
  handler : 处理函数,签名 handler(body: dict) -> (payload: dict, http_status: int)
"""
from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[dict], tuple[dict, int]]

_REGISTRY: dict[str, dict[str, Any]] = {}


def register(path: str, *, method: str = "GET", risk: str = "L0",
             auth: bool = True) -> Callable[[Handler], Handler]:
    """装饰器:把一个处理函数登记进白名单。"""
    def deco(fn: Handler) -> Handler:
        _REGISTRY[path] = {"method": method.upper(), "risk": risk, "auth": auth, "handler": fn}
        return fn
    return deco


def lookup(path: str) -> dict[str, Any] | None:
    return _REGISTRY.get(path)


def is_whitelisted(path: str, method: str) -> bool:
    e = _REGISTRY.get(path)
    return bool(e and e["method"] == method.upper())


def catalog() -> list[dict[str, str]]:
    """列出白名单(供 /health 与调试;不含 handler 本体)。"""
    return [
        {"path": p, "method": e["method"], "risk": e["risk"], "auth": e["auth"]}
        for p, e in sorted(_REGISTRY.items())
    ]


# ---------------------------------------------------------------------------
# P2.5+16: web.search / web.fetch 端点 handler(真调底层函数,不 stub)
# 任何异常都吞,200 + warning 字段透出,绝不 raise 给上层。
# ---------------------------------------------------------------------------

@register("/web/search", method="POST", risk="L0", auth=True)
def _web_search(body: dict) -> tuple[dict, int]:
    """多源 rank fusion web 搜索门面。空查询 / 全失败 → 200 + ok=True + 空 list。"""
    query = (body or {}).get("query", "").strip()
    limit = int((body or {}).get("limit", 10))
    if not query:
        return ({"ok": True, "query": query, "results": [], "warning": "empty_query"}, 200)
    try:
        from prisir_work import web_search as _ws  # noqa: PLC0415
        results = _ws.search(query, limit=limit)
        return ({"ok": True, "query": query, "results": results}, 200)
    except Exception as e:  # 兜底:任何异常都不抛给上层
        return ({"ok": True, "query": query, "results": [], "warning": type(e).__name__}, 200)


@register("/web/fetch", method="POST", risk="L0", auth=True)
def _web_fetch(body: dict) -> tuple[dict, int]:
    """多 fetcher 并发竞速 + 7d 缓存抓取。失败 → 200 + ok=True + 空 content。"""
    url = (body or {}).get("url", "").strip()
    if not url:
        return ({"ok": True, "url": url, "content": "", "warning": "empty_url"}, 200)
    try:
        from prisir_work import web_fetch as _wf  # noqa: PLC0415
        result = _wf.fetch(url, options={"timeout": 10.0})
        return ({"ok": True, **result}, 200)
    except Exception as e:
        return ({"ok": True, "url": url, "content": "", "warning": type(e).__name__}, 200)


# ---------------------------------------------------------------------------
# P2.5+16d: web.research 多步研究端点(plan→search×N→fetch→LLM 合成 + [n] 引用)
# ---------------------------------------------------------------------------

@register("/web/research", method="POST", risk="L0", auth=True)
def _web_research(body: dict) -> tuple[dict, int]:
    """多步研究:plan→search×N→fetch→LLM 合成 + [n] 引用。"""
    query = (body or {}).get("query", "").strip()
    if not query:
        return ({"ok": True, "query": query, "answer": "",
                 "sources": [], "citations": [],
                 "warnings": ["empty_query"], "steps": [], "plan": []}, 200)
    try:
        from prisir_work import research as _r
        result = _r.research(query)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "query": query, "answer": "",
                 "sources": [], "citations": [],
                 "warnings": [type(e).__name__], "steps": [], "plan": []}, 200)


# ---------------------------------------------------------------------------
# P2.5+16e: web.extract JSON Schema 结构化抽取(LLM 可选,失败降级 regex 启发式)
# ---------------------------------------------------------------------------

@register("/web/extract", method="POST", risk="L0", auth=True)
def _web_extract(body: dict) -> tuple[dict, int]:
    """JSON Schema 结构化抽取(LLM 可选,失败降级 regex)。"""
    url = (body or {}).get("url", "").strip()
    schema = (body or {}).get("schema") or {}
    if not url:
        return ({"ok": False, "url": "", "data": {}, "warnings": ["empty_url"]}, 200)
    try:
        from prisir_work import extract as _e
        result = _e.extract(url, schema)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "url": url, "schema": {}, "data": {},
                 "warnings": [type(e).__name__], "mode": "regex", "steps": []}, 200)


# ---------------------------------------------------------------------------
# P2.5+16f: web.find_similar 相似 URL 发现(多源:web_search + 可选 Serper related)
# ---------------------------------------------------------------------------

@register("/web/find_similar", method="POST", risk="L0", auth=True)
def _web_find_similar(body: dict) -> tuple[dict, int]:
    """相似 URL 发现(多源:web_search + 可选 Serper related)。"""
    body = body or {}
    url = body.get("url", "").strip()
    if not url:
        return ({"ok": False, "input_url": url, "similar": [],
                "warnings": ["empty_url"]}, 200)
    try:
        from prisir_work import find_similar as _fs
        max_r = int(body.get("max_results", 10))
        # 透传 providers(供 e2e/调试强制 mock)+ timeout(单次 HTTP 上限)
        providers = body.get("providers")
        timeout = float(body.get("timeout", 10.0))
        result = _fs.find_similar(url, max_results=max_r,
                                  providers=providers, timeout=timeout)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "input_url": url, "similar": [],
                 "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P2.5+17a: web.cache 查询门面(列/失效/统计)
# ---------------------------------------------------------------------------

@register("/web/cache/list", method="POST", risk="L0", auth=True)
def _web_cache_list(body: dict) -> tuple[dict, int]:
    """列出已缓存 URL 条目,可选 host 过滤。"""
    body = body or {}
    try:
        from prisir_work import cache_query as _cq
        host = body.get("host")
        limit = int(body.get("limit", 20))
        include_expired = bool(body.get("include_expired", False))
        result = _cq.cache_list(host=host, limit=limit, include_expired=include_expired)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "entries": [], "total": 0,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/cache/invalidate", method="POST", risk="L1", auth=True)
def _web_cache_invalidate(body: dict) -> tuple[dict, int]:
    """强制失效缓存条目(url 精确 / host 模糊 / all_expired 兜底)。"""
    body = body or {}
    try:
        from prisir_work import cache_query as _cq
        result = _cq.cache_invalidate(
            url=body.get("url"),
            host=body.get("host"),
            all_expired=bool(body.get("all_expired", False)),
        )
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "deleted": 0, "mode": "error",
                 "warnings": [type(e).__name__]}, 200)


@register("/web/cache/stats", method="POST", risk="L0", auth=True)
def _web_cache_stats(body: dict) -> tuple[dict, int]:
    """缓存统计:总数/大小/按 host 聚合 Top10/过期数。"""
    try:
        from prisir_work import cache_query as _cq
        result = _cq.cache_stats()
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "total_entries": 0, "expired_entries": 0,
                 "total_bytes": 0, "by_host": [],
                 "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P2.5+17b: web.diff 页面版本对比(url vs url 或 url vs 历史快照)
# ---------------------------------------------------------------------------

@register("/web/diff", method="POST", risk="L0", auth=True)
def _web_diff(body: dict) -> tuple[dict, int]:
    """两 URL 对比(mode=url)或同 URL 两时间快照对比(mode=time)。"""
    body = body or {}
    url_a = (body.get("url_a") or body.get("url") or "").strip()
    url_b = (body.get("url_b") or "").strip()
    mode = body.get("mode", "url")
    if not url_a:
        return ({"ok": False, "diff": "", "summary": {},
                 "warnings": ["empty_url_a"]}, 200)
    if mode == "url" and not url_b:
        return ({"ok": False, "diff": "", "summary": {},
                 "warnings": ["empty_url_b"]}, 200)
    try:
        from prisir_work import diff as _diff_mod
        raw = bool(body.get("raw", False))
        snapshot_b = bool(body.get("snapshot_b", True))
        timeout = float(body.get("timeout", 12.0))
        result = _diff_mod.diff(url_a, url_b=url_b, mode=mode, raw=raw,
                                timeout=timeout, snapshot_b=snapshot_b)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "diff": "", "summary": {},
                 "unchanged": True, "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P2.5+17c: web.crawl 全站爬虫(BFS + robots + 同 host 限速)
# ---------------------------------------------------------------------------

@register("/web/crawl", method="POST", risk="L1", auth=True)
def _web_crawl(body: dict) -> tuple[dict, int]:
    """BFS 同 host 抓取。L1 因会产生外部流量 + 写入 cache。"""
    body = body or {}
    url = body.get("url", "").strip()
    if not url:
        return ({"ok": False, "pages": [], "skipped": [],
                 "warnings": ["empty_url"]}, 200)
    try:
        from prisir_work import crawl as _crawl_mod
        result = _crawl_mod.crawl(
            url,
            max_pages=int(body.get("max_pages", 30)),
            max_depth=int(body.get("max_depth", 2)),
            same_host=bool(body.get("same_host", True)),
            respect_robots=bool(body.get("respect_robots", True)),
            rate_per_host=float(body.get("rate_per_host", 1.0)),
            timeout=float(body.get("timeout", 10.0)),
            user_agent=body.get("user_agent", "PrisirCrawler/0.1"),
        )
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "pages": [], "skipped": [],
                 "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P2.5+17d: web.agent LLM 驱动自主采集
# ---------------------------------------------------------------------------

@register("/web/agent", method="POST", risk="L1", auth=True)
def _web_agent(body: dict) -> tuple[dict, int]:
    """LLM 驱动多步自主采集,fallback 模板决策。"""
    body = body or {}
    query = body.get("query", "").strip()
    if not query:
        return ({"ok": False, "findings": [], "answer": "",
                 "warnings": ["empty_query"]}, 200)
    try:
        from prisir_work import agent as _agent_mod
        # llm_call 不通过 HTTP 注入(无现成渠道);走模板决策路径。
        # 上层 CLI / 扩展可包装自己的 llm_call 走直接调用。
        result = _agent_mod.agent(
            query,
            max_steps=int(body.get("max_steps", 6)),
            llm_call=None,  # 模板决策,符合零外部依赖
            timeout=float(body.get("timeout", 10.0)),
        )
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": True, "findings": [], "answer": "",
                 "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P2.5+18a: web.health 运维诊断
# ---------------------------------------------------------------------------

@register("/web/health", method="GET", risk="L0", auth=False)
def _web_health(_body: dict) -> tuple[dict, int]:
    """web 子系统健康检查(免 token):fetcher / cache / endpoint / tune。

    对齐 wigolo `npx wigolo doctor`。
    """
    try:
        from prisir_work import health as _h
        result = _h.web_health()
        return (result, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)
