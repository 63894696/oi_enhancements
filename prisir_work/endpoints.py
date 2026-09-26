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
# P3j T21-A: web.feedparser.{fetch,health} 直接调 feedparser 库(不绕 agent-reach)
# ---------------------------------------------------------------------------

@register("/web/feedparser/fetch", method="POST", risk="L0", auth=True)
def _web_feedparser_fetch(body: dict) -> tuple[dict, int]:
    """feedparser 直接抓 RSS / Atom / JSON Feed。"""
    body = body or {}
    url = (body.get("url") or "").strip()
    if not url:
        return ({"ok": False, "error": "empty_url",
                 "required": ["url"]}, 200)
    try:
        from prisir_work import web_fetch_feedparser as _fp
        r = _fp.feedparser_fetch(url, options=body)
        return ({"ok": True, "url": url, **r}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "url": url,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/feedparser/health", method="POST", risk="L0", auth=True)
def _web_feedparser_health(_body: dict) -> tuple[dict, int]:
    """feedparser 版本 + 能力探活(无需网络)。"""
    try:
        from prisir_work import web_fetch_feedparser as _fp
        return ({"ok": True, **_fp.feedparser_health()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "error": type(e).__name__}, 200)


# ---------------------------------------------------------------------------
# P3j T21-B: web.ytdlp.{meta,health} 把 yt-dlp 抽成通用 fetcher
# ---------------------------------------------------------------------------

@register("/web/ytdlp/meta", method="POST", risk="L0", auth=True)
def _web_ytdlp_meta(body: dict) -> tuple[dict, int]:
    """yt-dlp 通用元数据 + 字幕探测(200+ 网站:B站/微博/Twitter/Reddit/Vimeo/Niconico/TikTok)。

    不下载任何视频流,只抽 metadata + 字幕语言列表。
    """
    body = body or {}
    url = (body.get("url") or "").strip()
    if not url:
        return ({"ok": False, "error": "empty_url",
                 "required": ["url"]}, 200)
    try:
        from prisir_work import web_fetch_ytdlp as _yt
        r = _yt.ytdlp_meta(url, options=body)
        return ({"ok": True, "url": url, **r}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "url": url,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/ytdlp/health", method="POST", risk="L0", auth=True)
def _web_ytdlp_health(_body: dict) -> tuple[dict, int]:
    """yt-dlp 版本 + 支持网站数探活(无需网络)。"""
    try:
        from prisir_work import web_fetch_ytdlp as _yt
        return ({"ok": True, **_yt.ytdlp_health()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "error": type(e).__name__}, 200)


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


# ---------------------------------------------------------------------------
# P2.5+18b: web.tune per-domain fetcher 学习
# ---------------------------------------------------------------------------

@register("/web/tune/recommend", method="POST", risk="L0", auth=True)
def _web_tune_recommend(body: dict) -> tuple[dict, int]:
    """查 host 的 recommended fetcher 列表。空 / 无记录 → 返 null + hint。

    请求:{"url": "https://github.com/x"}  → host = github.com。
    """
    body = body if isinstance(body, dict) else {}
    try:
        from prisir_work import tune as _tune
        from urllib.parse import urlparse
        url = body.get("url") or body.get("host") or ""
        host = (urlparse(url).hostname or url).lower() if url else ""
        rec = _tune.recommend(host) if host else None
        return ({
            "ok": True,
            "host": host,
            "recommended": rec,
            "hint": None if rec else "no learned data yet",
        }, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)


@register("/web/tune/stats", method="POST", risk="L0", auth=True)
def _web_tune_stats(_body: dict) -> tuple[dict, int]:
    """调试:进程内累加器快照 + tune.json 落盘内容。"""
    try:
        from prisir_work import tune as _tune
        snap = _tune.tune_stats_snapshot()
        # 落盘内容
        loaded = {}
        try:
            p = _tune.tune_path()
            if p.exists():
                import json as _json
                loaded = _json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    loaded = {}
        except Exception:
            loaded = {}
        return ({
            "ok": True,
            "accumulator": snap,
            "tune_json": loaded,
            "hosts_learned": len(loaded),
        }, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)


@register("/web/tune/flush", method="POST", risk="L1", auth=True)
def _web_tune_flush(_body: dict) -> tuple[dict, int]:
    """手动触发 flush(累加器已稳定 → 写 tune.json)。返回落盘前后 best。"""
    try:
        from prisir_work import tune as _tune
        result = _tune.flush_if_ready()
        return ({"ok": True, "evaluated": result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# Easel 桥接(公众号扫码发布 + 数据回收)— 2026-09-24 ship
# 失败/未就绪 → 200 + ok=False + 明确 reason,绝不抛栈。
# 风险:发布类默认 L2(全回显,扩展侧弹确认卡);数据回收 L0(只读)。
# ---------------------------------------------------------------------------

@register("/publish/list", method="POST", risk="L0", auth=True)
def _publish_list(_body: dict) -> tuple[dict, int]:
    """列出所有注册的平台发布器 + ready 状态。"""
    try:
        from prisir_work import publisher as _pub
        return ({"ok": True, "publishers": _pub.list_publishers()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "publishers": [], "warnings": [type(e).__name__]}, 200)


@register("/publish/status", method="POST", risk="L0", auth=True)
def _publish_status(body: dict) -> tuple[dict, int]:
    """某平台的登录态 / 桥接就绪状态。"""
    platform = (body or {}).get("platform", "").strip()
    if not platform:
        return ({"ok": False, "error": "empty_platform"}, 200)
    try:
        from prisir_work import publisher as _pub
        p = _pub.get(platform)
        if p is None:
            return ({"ok": False, "platform": platform, "error": "unknown_platform"}, 200)
        return ({"ok": True, "platform": platform, **p.status()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platform": platform, "warnings": [type(e).__name__]}, 200)


@register("/publish/html", method="POST", risk="L2", auth=True)
def _publish_html(body: dict) -> tuple[dict, int]:
    """发 HTML 到指定平台(目前只有 wechat-oa 真接 Easel)。

    body: {platform, html_path, title, cover, [digest], [author]}
    失败/未登录/平台未实现 → 200 + ok=False + error,绝不抛栈。
    """
    body = body or {}
    platform = body.get("platform", "").strip()
    html_path = body.get("html_path", "").strip()
    title = body.get("title", "").strip()
    cover = body.get("cover", "").strip()
    digest = body.get("digest", "")
    author = body.get("author", "")
    if not platform or not html_path or not title or not cover:
        return ({"ok": False,
                 "error": "missing_fields",
                 "required": ["platform", "html_path", "title", "cover"]}, 200)
    try:
        from prisir_work import publisher as _pub
        r = _pub.publish(platform, html_path,
                         title=title, cover=cover,
                         digest=digest, author=author)
        return ({"ok": True, **r.to_dict()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platform": platform,
                 "title": title, "error": f"{type(e).__name__}: {e}"}, 200)


@register("/publish/stats", method="POST", risk="L0", auth=True)
def _publish_stats(body: dict) -> tuple[dict, int]:
    """公众号近 N 天数据回收(发表记录 / 阅读 / 分享 / 粉丝)。

    body: {platform='wechat-oa', count=30}
    Easel stats 返的 dict 字段已含 metrics/notes/growth(自带 last/day/week/month/year)。
    """
    body = body or {}
    platform = body.get("platform", "wechat-oa").strip()
    count = int(body.get("count", 30))
    if platform != "wechat-oa":
        return ({"ok": False, "platform": platform,
                 "error": "stats 目前只支持 wechat-oa"}, 200)
    try:
        from prisir_work import easel_bridge as _eb
        br = _eb.easel()
        if not br.ready:
            return ({"ok": False, "platform": platform,
                     "error": "Easel 桥接未就绪"}, 200)
        r = br.stats(count=count)
        # Easel 内部已返 {platform,loggedIn,followers,posts,metrics,notes,growth}
        # 我们把整个 r.parsed 透传,前端按字段取
        return ({"ok": True, "platform": platform, "count": count,
                 "data": r.parsed,
                 "stderr_tail": r.stderr[-300:] if r.stderr else ""}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platform": platform,
                 "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P3j T14-B: 视频 + YouTube 自然语言端点(透明代理 companion wechat-publisher)
# 全部走 port_registry.read_port("wechat_publisher_port") 找到子服务后 HTTP 调用。
# 失败/未注册 → 200 + ok=False + reason,绝不抛栈。
# ---------------------------------------------------------------------------

# 短帮助代理:把路径前缀转给子服务
def _proxy(name: str, method: str, sub_path: str,
           body: dict, timeout: float) -> tuple[dict, int]:
    """通用代理:把 body POST/GET 到 wechat-publisher 子服务。"""
    from . import port_registry
    if method == "GET":
        return port_registry.proxy_get(name, sub_path, timeout=timeout), 200
    return port_registry.proxy_post(name, sub_path, body or {}, timeout=timeout), 200


@register("/video/list", method="POST", risk="L0", auth=True)
def _video_list(_body: dict) -> tuple[dict, int]:
    """列 9 个 video creator + ready — 代理 /api/video/creators。"""
    r = _proxy("wechat_publisher_port", "GET", "/api/video/creators", {}, timeout=10)
    return (r, 200)


@register("/video/orchestrate", method="POST", risk="L2", auth=True)
def _video_orchestrate(body: dict) -> tuple[dict, int]:
    """一键出片 — 代理 /api/video/orchestrate。

    body: {topic, script, output_dir?, aspect_ratio?, duration?, voice?,
           with_subtitle?, with_images?}
    """
    body = body or {}
    required = ["topic", "script"]
    missing = [k for k in required if not (body.get(k) or "").strip()]
    if missing:
        return ({"ok": False, "error": "missing_fields",
                 "required": required, "missing": missing}, 200)
    # 默认 9:16 60s
    body.setdefault("aspect_ratio", "9:16")
    body.setdefault("duration", 60)
    body.setdefault("with_subtitle", True)
    body.setdefault("with_images", False)
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/orchestrate", body, timeout=900)  # 合成最长 15min


@register("/video/tts", method="POST", risk="L1", auth=True)
def _video_tts(body: dict) -> tuple[dict, int]:
    """文字转语音 — 代理 /api/video/create creator=tts。"""
    body = body or {}
    text = (body.get("text") or "").strip()
    file_ = (body.get("file") or "").strip()
    output = (body.get("output") or "").strip()
    if not text and not file_:
        return ({"ok": False,
                 "error": "missing_input",
                 "hint": "传 text(直接文本)或 file(文本文件路径)"}, 200)
    if not output:
        return ({"ok": False, "error": "missing_output",
                 "hint": "传 output(产物 mp3 路径)"}, 200)
    payload = {"creator": "tts",
               "text": text, "file": file_,
               "output": output,
               "voice": body.get("voice", ""),
               "rate": body.get("rate", ""),
               "subtitle": body.get("subtitle", "")}
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/create", payload, timeout=180)


@register("/video/asr", method="POST", risk="L1", auth=True)
def _video_asr(body: dict) -> tuple[dict, int]:
    """音视频转字幕 — 代理 /api/video/create creator=asr。"""
    body = body or {}
    input_ = (body.get("input") or "").strip()
    if not input_:
        return ({"ok": False, "error": "missing_input",
                 "hint": "传 input(视频/音频文件路径)"}, 200)
    payload = {"creator": "asr",
               "input": input_,
               "output": body.get("output", ""),
               "model": body.get("model", "base"),
               "format": body.get("format", "srt"),
               "language": body.get("language", "")}
    model = body.get("model", "base")
    to = 1800 if model in ("medium", "large", "large-v3") else 300
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/create", payload, timeout=to)


@register("/video/cut", method="POST", risk="L1", auth=True)
def _video_cut(body: dict) -> tuple[dict, int]:
    """裁剪视频 — 代理 video-ops op=cut。"""
    body = body or {}
    input_ = (body.get("input") or "").strip()
    output = (body.get("output") or "").strip()
    if not input_ or not output:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["input", "output"],
                 "hint": "自然语言:'裁剪 C:/v.mp4 从 00:10 到 00:30 输出 C:/out.mp4'"}, 200)
    payload = {"creator": "video-ops", "op": "cut",
               "input": input_, "output": output}
    # 自然语言友好别名
    if "start" in body:
        payload["start"] = body["start"]
    if "end" in body:
        payload["end"] = body["end"]
    if "duration" in body:
        payload["duration"] = body["duration"]
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/create", payload, timeout=600)


@register("/video/bgm", method="POST", risk="L1", auth=True)
def _video_bgm(body: dict) -> tuple[dict, int]:
    """加背景音乐 — 代理 video-ops op=bgm。"""
    body = body or {}
    input_ = (body.get("input") or "").strip()
    output = (body.get("output") or "").strip()
    music = (body.get("music") or body.get("bgm") or "").strip()
    if not input_ or not output or not music:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["input", "output", "music"]}, 200)
    payload = {"creator": "video-ops", "op": "bgm",
               "input": input_, "output": output,
               "music": music}
    if "volume" in body:
        payload["volume"] = body["volume"]
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/create", payload, timeout=600)


@register("/video/burn", method="POST", risk="L1", auth=True)
def _video_burn(body: dict) -> tuple[dict, int]:
    """字幕烧录 — 代理 /api/video/subtitle/burn。"""
    body = body or {}
    input_ = (body.get("input") or body.get("video") or "").strip()
    sub = (body.get("sub") or body.get("subtitle") or "").strip()
    output = (body.get("output") or "").strip()
    if not input_ or not sub or not output:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["input", "sub", "output"],
                 "hint": "自然语言:'把字幕 C:/a.srt 烧到 C:/v.mp4 输出 C:/v_burned.mp4'"}, 200)
    payload = {"input": input_, "sub": sub, "output": output,
               "soft": bool(body.get("soft", False)),
               "lang": body.get("lang", ""),
               "force_style": body.get("force_style", ""),
               "font_dir": body.get("font_dir", "")}
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/subtitle/burn", payload, timeout=600)


@register("/video/info", method="POST", risk="L0", auth=True)
def _video_info(body: dict) -> tuple[dict, int]:
    """查视频元数据 — 代理 /api/video/info。"""
    body = body or {}
    path = (body.get("path") or body.get("input") or "").strip()
    if not path:
        return ({"ok": False, "error": "missing_path",
                 "hint": "传 path(视频文件路径)"}, 200)
    from . import port_registry
    r = port_registry.proxy_get("wechat_publisher_port",
                                f"/api/video/info?path={path}", timeout=10)
    return (r, 200)


@register("/video/analyze", method="POST", risk="L0", auth=True)
def _video_analyze(body: dict) -> tuple[dict, int]:
    """发布数据分析 — 代理 /api/analytics。"""
    body = body or {}
    mode = (body.get("mode") or "selftest").strip()
    from . import port_registry
    qs = f"?mode={mode}"
    if body.get("data"):
        qs += f"&data={body['data']}"
    if body.get("follower_log"):
        qs += f"&follower_log={body['follower_log']}"
    if body.get("profile"):
        qs += f"&profile={body['profile']}"
    r = port_registry.proxy_get("wechat_publisher_port",
                                f"/api/analytics{qs}", timeout=30)
    return (r, 200)


@register("/youtube/status", method="POST", risk="L0", auth=True)
def _youtube_status(_body: dict) -> tuple[dict, int]:
    """YouTube 桥接状态 — 代理 /api/youtube/status。"""
    r = _proxy("wechat_publisher_port", "GET", "/api/youtube/status", {}, timeout=10)
    return (r, 200)


@register("/youtube/upload", method="POST", risk="L3", auth=True)
def _youtube_upload(body: dict) -> tuple[dict, int]:
    """YouTube 上传 — 代理 /api/youtube/upload。

    body: {video, title, description?, tags?, category_id?, privacy?, exec_real?}
    """
    body = body or {}
    video = (body.get("video") or body.get("input") or "").strip()
    title = (body.get("title") or "").strip()
    if not video or not title:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["video", "title"]}, 200)
    payload = {"video": video, "title": title,
               "description": body.get("description", ""),
               "tags": body.get("tags") or [],
               "category_id": str(body.get("category_id", "22")),
               "privacy": body.get("privacy", "private"),
               "exec_real": bool(body.get("exec_real", False))}
    to = 600 if payload["exec_real"] else 30
    return _proxy("wechat_publisher_port", "POST",
                  "/api/youtube/upload", payload, timeout=to)


@register("/youtube/list", method="POST", risk="L0", auth=True)
def _youtube_list(body: dict) -> tuple[dict, int]:
    """列我的 YouTube 视频 — 代理 /api/youtube/list。"""
    body = body or {}
    max_results = int(body.get("max_results", 10))
    exec_real = bool(body.get("exec_real", False))
    from . import port_registry
    qs = f"?max_results={max_results}&exec_real={exec_real}"
    r = port_registry.proxy_get("wechat_publisher_port",
                                f"/api/youtube/list{qs}", timeout=30)
    return (r, 200)


# ---------------------------------------------------------------------------
# P3j T20-B: Agent-Reach 集成(14 平台读+搜:小红书/B站字幕/GitHub/V2EX/RSS…)
# 全部 L0 只读,失败/未安装 agent-reach → 200 + ok=False + hint,绝不抛栈。
# ---------------------------------------------------------------------------

@register("/web/reach/doctor", method="POST", risk="L0", auth=True)
def _web_reach_doctor(_body: dict) -> tuple[dict, int]:
    """查 agent-reach 安装 + 14 平台健康状态。"""
    try:
        from prisir_work import agent_reach_bridge as _arb
        return ({"ok": True, **_arb.doctor()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)


@register("/web/reach/read", method="POST", risk="L0", auth=True)
def _web_reach_read(body: dict) -> tuple[dict, int]:
    """读某平台 URL。body: {platform, url, timeout?}"""
    body = body or {}
    platform = (body.get("platform") or "").strip()
    url = (body.get("url") or "").strip()
    timeout = float(body.get("timeout", 30.0))
    if not platform or not url:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["platform", "url"],
                 "hint": "platform + url 都必填"}, 200)
    try:
        from prisir_work import agent_reach_bridge as _arb
        result = _arb.read(platform, url, timeout=timeout)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platform": platform, "url": url,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/reach/search", method="POST", risk="L0", auth=True)
def _web_reach_search(body: dict) -> tuple[dict, int]:
    """搜某平台关键词。body: {platform, query, limit?, timeout?}

    注意:rss/feed 类 channel 的 query 是 feed URL,可空;
    其他 channel query 必填,缺则返 reach_missing_query。
    """
    body = body or {}
    platform = (body.get("platform") or "").strip()
    query = (body.get("query") or "").strip()
    limit = int(body.get("limit", 10))
    timeout = float(body.get("timeout", 30.0))
    if not platform:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["platform"],
                 "hint": "platform 必填(query 可空,如 rss)"}, 200)
    try:
        from prisir_work import agent_reach_bridge as _arb
        result = _arb.search(platform, query, limit=limit, timeout=timeout)
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platform": platform, "query": query,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/reach/platforms", method="POST", risk="L0", auth=True)
def _web_reach_platforms(_body: dict) -> tuple[dict, int]:
    """列 14 平台目录(静态,与安装状态无关)。"""
    try:
        from prisir_work import agent_reach_bridge as _arb
        return ({"ok": True, "platforms": _arb.platforms()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "platforms": [], "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P3j T20-I: Jina reader/search 端点
# ---------------------------------------------------------------------------

@register("/web/jina/health", method="POST", risk="L0", auth=True)
def _web_jina_health(_body: dict) -> tuple[dict, int]:
    """查 jina reader/search 部署状态(hosted / 自部署)。"""
    try:
        from prisir_work import web_fetch_jina as _jina
        return ({"ok": True, **_jina.jina_health()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "warnings": [type(e).__name__]}, 200)


@register("/web/jina/fetch", method="POST", risk="L0", auth=True)
def _web_jina_fetch(body: dict) -> tuple[dict, int]:
    """URL → markdown(显式调 jina reader,不走 web_fetch 路由)。

    body: {url, timeout?, max_chars?}
    """
    body = body or {}
    url = (body.get("url") or "").strip()
    if not url:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["url"]}, 200)
    timeout = float(body.get("timeout", 30.0))
    max_chars = int(body.get("max_chars", 50000))
    try:
        from prisir_work import web_fetch_jina as _jina
        result = _jina.jina_fetch(url, {"timeout": timeout, "max_chars": max_chars})
        return ({"ok": True, **result}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "url": url, "warnings": [type(e).__name__]}, 200)


@register("/web/jina/search", method="POST", risk="L0", auth=True)
def _web_jina_search(body: dict) -> tuple[dict, int]:
    """关键词 → top N 结果 + 全文 markdown。

    body: {query, limit?, timeout?, max_chars?}
    """
    body = body or {}
    query = (body.get("query") or "").strip()
    if not query:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["query"]}, 200)
    limit = int(body.get("limit", 5))
    timeout = float(body.get("timeout", 30.0))
    max_chars = int(body.get("max_chars", 50000))
    try:
        from prisir_work import web_fetch_jina as _jina
        results = _jina.jina_search(query, limit=limit,
                                     options={"timeout": timeout,
                                              "max_chars": max_chars})
        return ({"ok": True, "query": query, "results": results,
                 "sources": ["jina_search"] * len(results)}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "query": query, "warnings": [type(e).__name__]}, 200)


# ---------------------------------------------------------------------------
# P3j T21-C: web.gh.{health,repo,issue,search} 直接调 gh CLI(不绕 Playwright)
# ---------------------------------------------------------------------------

@register("/web/gh/health", method="POST", risk="L0", auth=True)
def _web_gh_health(_body: dict) -> tuple[dict, int]:
    """gh CLI 安装 + 版本 + auth 状态(无需 token)。"""
    try:
        from prisir_work import gh_bridge as _gh
        return ({"ok": True, **_gh.gh_health()}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "error": type(e).__name__}, 200)


@register("/web/gh/repo", method="POST", risk="L0", auth=True)
def _web_gh_repo(body: dict) -> tuple[dict, int]:
    """读 GitHub 仓库元数据(stars/forks/desc)。无需 token(public)。

    body: {owner, name, timeout?}
    """
    body = body or {}
    owner = (body.get("owner") or "").strip()
    name = (body.get("name") or "").strip()
    if not owner or not name:
        return ({"ok": False, "error": "missing_owner_or_name",
                 "required": ["owner", "name"]}, 200)
    timeout = float(body.get("timeout", 30.0))
    try:
        from prisir_work import gh_bridge as _gh
        r = _gh.repo_info(owner, name, timeout=timeout)
        return ({"ok": True, **r}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "owner": owner, "name": name,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/gh/issue", method="POST", risk="L0", auth=True)
def _web_gh_issue(body: dict) -> tuple[dict, int]:
    """读 GitHub issue/PR。无需 token(public)。

    body: {owner, name, number, timeout?}
    """
    body = body or {}
    owner = (body.get("owner") or "").strip()
    name = (body.get("name") or "").strip()
    number = int(body.get("number", 0))
    if not owner or not name or not number:
        return ({"ok": False, "error": "missing_fields",
                 "required": ["owner", "name", "number"]}, 200)
    timeout = float(body.get("timeout", 30.0))
    try:
        from prisir_work import gh_bridge as _gh
        # issue 端点同时也支持 PR(gh api 不区分,统一 issues 端点返 PR)
        r = _gh.issue_get(owner, name, number, timeout=timeout)
        return ({"ok": True, **r}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "owner": owner, "name": name,
                 "number": number,
                 "warnings": [type(e).__name__]}, 200)


@register("/web/gh/search", method="POST", risk="L0", auth=True)
def _web_gh_search(body: dict) -> tuple[dict, int]:
    """GitHub 搜索(repos/issues/prs/code)。无需 token(public)。

    body: {query, kind?, limit?, timeout?}
    """
    body = body or {}
    query = (body.get("query") or "").strip()
    if not query:
        return ({"ok": False, "error": "empty_query",
                 "required": ["query"]}, 200)
    kind = (body.get("kind") or "repos").strip()
    limit = int(body.get("limit", 10))
    timeout = float(body.get("timeout", 30.0))
    try:
        from prisir_work import gh_bridge as _gh
        results = _gh.search(query, kind=kind, limit=limit,
                             timeout=timeout)
        return ({"ok": True, "query": query, "kind": kind,
                 "results": results,
                 "sources": ["gh_search"] * len(results)}, 200)
    except Exception as e:  # noqa: BLE001
        return ({"ok": False, "query": query, "kind": kind,
                 "warnings": [type(e).__name__]}, 200)
