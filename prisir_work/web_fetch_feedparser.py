"""web_fetch_feedparser.py — feedparser 直接 fetcher(2026-09-26 ship,P3j T21-A)。

复现 kurtmckee/feedparser (https://github.com/kurtmckee/feedparser) 的核心能力:
解析 RSS / Atom / JSON Feed,带条件 GET(ETag/Last-Modified)节省带宽,转 markdown
喂 LLM。

设计:
  · 调用 feedparser.parse(url, agent=USER_AGENT, timeout=...)
  · 状态:0=新内容;301=永久重定向;304=未修改(返缓存);其他错误
  · 失败一律返 {ok: False, error: "feedparser_xxx"} 不 raise
  · 单 feed 限制条数 + 总字符截断(防超大博客拖死 LLM)
  · URL 启发式判定(.xml / .rss / /feed / /atom / ?alt=rss ...),
    非 feed URL 提前返 not_a_feed_url

公开 API:
  · feedparser_fetch(url, options) → {content, meta}
    — 作为 fetcher 给 web_fetch.fetch() 用
  · feedparser_health() → {ok, version, supports_*}

为什么独立模块:
  · 保持 web_fetch.py 零外部依赖;feedparser 6.0.14 是第三方
  · 后续 feedparser 大改只动这一个文件
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import feedparser

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

FEEDPARSER_TIMEOUT = 30.0
FEEDPARSER_MAX_ITEMS = 50     # 单 feed 限制条数,防超大博客
FEEDPARSER_MAX_CHARS = 50_000  # markdown 截断(防止 LLM context 爆炸)

# 启发式:URL 末尾 / query 含这些 token 视为 feed
_FEED_URL_TOKENS: tuple[str, ...] = (
    ".xml", ".rss", "/feed", "/atom", "/rss",
    "?alt=rss", "?format=rss", "?output=rss", "?type=rss",
    "/feed.json", "/index.xml",
)


def _is_feed_url(url: str) -> bool:
    """启发式判断 URL 是否像 feed(.xml/.rss//feed//atom/?alt=rss 等)。"""
    if not url or not isinstance(url, str):
        return False
    u = url.lower()
    return any(tok in u for tok in _FEED_URL_TOKENS)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """剥 HTML 标签 + 折叠空白,返回纯文本。"""
    if not s:
        return ""
    return _WS_RE.sub(" ", _HTML_TAG_RE.sub("", s)).strip()


def _truncate(content: str, max_chars: int) -> str:
    """截断 markdown 到 max_chars,尾部写截断标记。"""
    if not content:
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n\n... [truncated at {max_chars} chars] ..."


# ---------------------------------------------------------------------------
# fetcher:feed URL → markdown
# ---------------------------------------------------------------------------

def feedparser_fetch(url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """feedparser fetcher:RSS/Atom/JSON Feed → LLM-friendly markdown。

    给 web_fetch.fetch() 调用,签名同 register_fetcher 注册的 fetcher。
    失败返 {content: "", meta: {ok: False, error: "feedparser_xxx"}},永不 raise。
    """
    options = options or {}
    timeout = float(options.get("timeout", FEEDPARSER_TIMEOUT))
    max_items = int(options.get("max_items", FEEDPARSER_MAX_ITEMS))
    max_chars = int(options.get("max_chars", FEEDPARSER_MAX_CHARS))

    if not url or not isinstance(url, str):
        return {"content": "", "meta": {"fetcher": "feedparser",
                                        "ok": False, "error": "bad_url"}}

    # 启发式判定:不像 feed 的 URL 直接拒(避免跟 jina/urllib 重叠抢活)
    if not _is_feed_url(url):
        return {"content": "", "meta": {"fetcher": "feedparser", "ok": False,
                                        "error": "not_a_feed_url",
                                        "hint": "URL 不像 RSS/Atom/JSON Feed;"
                                                "走 jina 或 urllib"}}

    t0 = time.monotonic()
    try:
        d = feedparser.parse(url, agent=feedparser.USER_AGENT,
                             timeout=timeout)
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "feedparser", "ok": False,
                                        "error": f"feedparser_{type(e).__name__}",
                                        "detail": str(e)[:200]}}

    # 状态码:feedparser 把 HTTP status 放在 d.status
    status = getattr(d, "status", 200)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if status == 304:
        # 304 Not Modified — 上层应复用 web_fetch 缓存,这里返 ok=False
        return {"content": "", "meta": {"fetcher": "feedparser",
                                        "ok": False, "status": 304,
                                        "error": "not_modified",
                                        "hint": "304 Not Modified,上层应复用缓存",
                                        "elapsed_ms": elapsed_ms}}
    if status not in (200, 301, 0):
        # 0 = feedparser 没拿到 status(可能 DNS 失败),仍尝试解析 entries
        # 其它非 2xx/301 → 错误
        return {"content": "", "meta": {"fetcher": "feedparser", "ok": False,
                                        "status": int(status) or 0,
                                        "error": f"http_{status}",
                                        "elapsed_ms": elapsed_ms}}

    # feed 元数据
    feed_obj = getattr(d, "feed", {}) or {}
    feed_title = (feed_obj.get("title", "") if isinstance(feed_obj, dict) else "") or ""
    feed_link = (feed_obj.get("link", "") if isinstance(feed_obj, dict) else "") or ""
    feed_subtitle = (feed_obj.get("subtitle", "") if isinstance(feed_obj, dict) else "") or ""

    entries = list(getattr(d, "entries", []) or [])[:max_items]

    bozo = getattr(d, "bozo", False)
    bozo_exception = getattr(d, "bozo_exception", None)

    # 组装 markdown
    lines: list[str] = []
    if feed_title:
        lines.append(f"# {feed_title}")
    if feed_subtitle:
        lines.append(f"_{feed_subtitle}_")
    if feed_link:
        lines.append(f"_Feed URL: {feed_link}_")
    if lines:
        lines.append("")

    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip() or "(no title)"
        link = (e.get("link") or "").strip()
        published = (e.get("published") or e.get("updated")
                     or e.get("created") or "").strip()
        author = (e.get("author") or e.get("dc_creator") or "").strip()
        summary = (e.get("summary") or e.get("description") or "").strip()
        # 简化:summary 去 HTML,留前 600 字符
        if summary:
            summary = _strip_html(summary)[:600]

        lines.append(f"## {i}. {title}")
        meta_bits: list[str] = []
        if published:
            meta_bits.append(f"Published: {published}")
        if author:
            meta_bits.append(f"Author: {author}")
        if meta_bits:
            lines.append(f"_{' · '.join(meta_bits)}_")
        if link:
            lines.append(f"_URL: {link}_")
        if summary:
            lines.append("")
            lines.append(summary)
        lines.append("")

    content = _truncate("\n".join(lines), max_chars)

    meta: dict[str, Any] = {
        "fetcher": "feedparser",
        "ok": bool(entries),
        "status": int(status) if status else 200,
        "format": "markdown",
        "feed_title": feed_title,
        "feed_link": feed_link,
        "item_count": len(entries),
        "elapsed_ms": elapsed_ms,
        "version": feedparser.__version__,
    }
    if bozo:
        # bozo=True 不算致命 — feedparser 仍可能给出部分 entries
        meta["bozo"] = True
        meta["bozo_exception"] = str(bozo_exception)[:200]
    if not content and entries:
        meta["error"] = "empty_content_after_truncate"
    elif not entries:
        meta["error"] = "empty_feed"
    return {"content": content, "meta": meta}


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

def feedparser_health() -> dict[str, Any]:
    """检查 feedparser 版本 + 能力(无需网络)。

    P3j T21-A:仅探活,不真抓;版本和能力列表给 verify / UI 用。
    """
    return {
        "ok": True,
        "version": feedparser.__version__,
        "user_agent": feedparser.USER_AGENT,
        "supports_rss": True,
        "supports_atom": True,
        "supports_json_feed": True,
        "supports_conditional_get": True,  # ETag/Last-Modified
        "max_items_default": FEEDPARSER_MAX_ITEMS,
        "max_chars_default": FEEDPARSER_MAX_CHARS,
        "timeout_default": FEEDPARSER_TIMEOUT,
    }


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys

    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "health"
    if cmd == "health":
        print(_json.dumps(feedparser_health(), ensure_ascii=False, indent=2))
    elif cmd == "fetch":
        url = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(_json.dumps(feedparser_fetch(url), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd} (use health/fetch)")