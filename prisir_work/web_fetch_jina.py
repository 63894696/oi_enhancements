"""web_fetch_jina.py — Jina Reader / Search fetcher(2026-09-26 ship,P3j T20-I)。

复现 jina-ai/reader (https://github.com/jina-ai/reader) 的两个核心端点:
  · r.jina.ai/{url}    → URL 转 LLM-friendly Markdown(自动剥 nav/footer/ads)
  · s.jina.ai/{query}  → 关键词搜 + 拿回 top N 全文

支持两种部署方式:
  1) 官方 hosted(默认):https://r.jina.ai / https://s.jina.ai
     - 需 JINA_API_KEY env(免费层 100 万 token)
  2) 自部署(Docker):JINA_READER_URL=http://localhost:8081
     - ghcr.io/jina-ai/reader:oss 跑 8081(h1c)/8080(h2c)
     - 零 key、零外部依赖、隐私安全(数据不出本机)

设计:
  · 失败一律返 {ok: False, error: "jina_xxx"} 不 raise
  · markdown 截断到 max_chars(默认 50k)防止超大页面拖死 LLM
  · x-respond-with=markdown + x-engine=auto(官方推荐默认)
  · timeout 默认 30s(jina hosted 平均 5-15s,自部署更慢)

公开 API:
  · jina_fetch(url, options) → {content, meta}
    — 作为 fetcher 给 web_fetch.fetch() 用
  · jina_search(query, limit=5, options) → list[{url, title, content}]
    — 作为 provider 给 web_search.search() 用
  · jina_health() → {ok, mode: "hosted"|"self_hosted", version}
    — UI / verify 检查

为什么独立模块(并入 web_fetch.register_fetcher 也行):
  · 保持 web_fetch.py 零外部依赖(纯 stdlib urllib)
  · 后续若 jina API 大改,只动这一个文件
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# Reader 默认 hosted,自部署用 env 覆盖
JINA_READER_HOSTED = "https://r.jina.ai"
JINA_SEARCH_HOSTED = "https://s.jina.ai"
JINA_DEFAULT_TIMEOUT = 30.0
JINA_MAX_CHARS = 50_000  # 截断 markdown 防 LLM context 爆炸

# 自部署 URL(空 → 用 hosted)— env 同时控制 reader 和 search
JINA_READER_URL: str = os.environ.get("JINA_READER_URL", "").strip().rstrip("/")
JINA_SEARCH_URL: str = os.environ.get("JINA_SEARCH_URL", "").strip().rstrip("/")

JINA_API_KEY: str = os.environ.get("JINA_API_KEY", "").strip()

# 自部署检测:env 设了 → 用自部署,否则 hosted
def _is_self_hosted() -> bool:
    return bool(JINA_READER_URL or JINA_SEARCH_URL)


def _reader_base() -> str:
    return JINA_READER_URL if JINA_READER_URL else JINA_READER_HOSTED


def _search_base() -> str:
    return JINA_SEARCH_URL if JINA_SEARCH_URL else JINA_SEARCH_HOSTED


# ---------------------------------------------------------------------------
# 共享 HTTP 工具
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: float = JINA_DEFAULT_TIMEOUT,
              headers: dict[str, str] | None = None,
              params: dict[str, str] | None = None) -> dict[str, Any]:
    """调 jina 端点,统一异常处理。返 {ok, status, content, raw}。

    永不 raise。
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    hdrs = {
        "User-Agent": "PrisirAI/1.0 (+https://github.com/local/prisir)",
        "Accept": "text/markdown, text/plain;q=0.9, */*;q=0.8",
        "x-respond-with": "markdown",
        "x-engine": "auto",
        "x-timeout": str(int(timeout)),
    }
    if JINA_API_KEY:
        hdrs["Authorization"] = f"Bearer {JINA_API_KEY}"
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200) or 200
            resp_headers = {k: v for k, v in resp.headers.items()}
            try:
                content = raw.decode("utf-8", errors="replace")
            except Exception:
                content = ""
            return {"ok": 200 <= int(status) < 300,
                    "status": int(status),
                    "content": content,
                    "headers": resp_headers}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": int(getattr(e, "code", 0) or 0),
                "error": f"http_{e.code}", "content": ""}
    except urllib.error.URLError as e:
        return {"ok": False, "status": 0,
                "error": "url_error", "detail": str(getattr(e, "reason", e)),
                "content": ""}
    except Exception as e:
        return {"ok": False, "status": 0,
                "error": f"jina_{type(e).__name__}",
                "detail": str(e), "content": ""}


def _truncate(content: str, max_chars: int = JINA_MAX_CHARS) -> str:
    """截断 markdown 到 max_chars(防止超大页面拖死 LLM)。"""
    if not content:
        return ""
    if len(content) <= max_chars:
        return content
    # 保留头部(标题 + 开头部分),尾部写截断标记
    return content[:max_chars] + f"\n\n... [truncated at {max_chars} chars] ..."


# ---------------------------------------------------------------------------
# fetcher:URL → LLM-friendly markdown
# ---------------------------------------------------------------------------

def jina_fetch(url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """jina reader fetcher:URL → markdown。

    给 web_fetch.fetch() 调用,签名同 register_fetcher 注册的 fetcher。
    失败返 {content: "", meta: {ok: False, error: "jina_xxx"}},永不 raise。
    """
    options = options or {}
    timeout = float(options.get("timeout", JINA_DEFAULT_TIMEOUT))
    max_chars = int(options.get("max_chars", JINA_MAX_CHARS))

    if not url or not isinstance(url, str):
        return {"content": "", "meta": {"fetcher": "jina", "ok": False,
                                        "error": "bad_url"}}

    # jina reader 端点:GET {reader_base}/{url}
    # 注:url 里通常有 https://,直接拼接即可
    full_url = f"{_reader_base()}/{url}"
    t0 = time.monotonic()
    r = _http_get(full_url, timeout=timeout)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if not r.get("ok"):
        return {"content": "", "meta": {
            "fetcher": "jina", "ok": False,
            "error": r.get("error", "jina_failed"),
            "status": r.get("status", 0),
            "detail": r.get("detail", ""),
            "elapsed_ms": elapsed_ms,
            "mode": "self_hosted" if _is_self_hosted() else "hosted",
        }}

    content = _truncate(r.get("content", ""), max_chars)
    meta = {
        "fetcher": "jina",
        "ok": bool(content),
        "status": r.get("status", 200),
        "elapsed_ms": elapsed_ms,
        "format": "markdown",
        "mode": "self_hosted" if _is_self_hosted() else "hosted",
    }
    if not content:
        meta["error"] = "empty_content"
    return {"content": content, "meta": meta}


# ---------------------------------------------------------------------------
# provider:关键词 → top N 结果 + 全文
# ---------------------------------------------------------------------------

def jina_search(query: str, limit: int = 5,
                options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """jina search provider:query → top N URL + 全文 markdown。

    给 web_search.search() 调用,签名同 register_provider。
    失败 / 不可用 → 返 []。
    """
    if not query or not query.strip():
        return []
    options = options or {}
    timeout = float(options.get("timeout", JINA_DEFAULT_TIMEOUT))
    max_chars = int(options.get("max_chars", JINA_MAX_CHARS))
    cap = max(1, min(limit, 20))

    full_url = f"{_search_base()}/{urllib.parse.quote(query.strip())}"
    r = _http_get(full_url, timeout=timeout,
                  params={"num": str(cap)},
                  headers={"Accept": "application/json"})
    if not r.get("ok"):
        log.warning("jina_search failed: status=%s error=%s",
                    r.get("status"), r.get("error"))
        return []

    raw = r.get("content", "")
    # jina s.jina.ai 默认返 JSON(若 Accept: application/json)
    items: list[dict[str, Any]] = []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            items = data.get("data") or data.get("results") or []
        elif isinstance(data, list):
            items = data
    except json.JSONDecodeError:
        # 兜底:plain text 模式 → 整段当一个 result
        items = [{"url": "", "title": query, "content": raw}]

    out: list[dict[str, Any]] = []
    for item in items[:cap]:
        url = item.get("url") or item.get("link") or ""
        if not url:
            continue
        out.append({
            "url": url,
            "title": item.get("title") or url,
            "snippet": (item.get("description")
                        or item.get("snippet")
                        or (item.get("content", "")[:300] if item.get("content") else "")),
            "content": _truncate(item.get("content", "") or "", max_chars),
        })
    return out


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

def jina_health() -> dict[str, Any]:
    """检查 jina reader / search 部署状态。

    返 {ok, mode: "hosted"|"self_hosted", reader: {ok, ...}, search: {ok, ...}}。
    """
    reader_url = f"{_reader_base()}/https://example.com"
    search_url = f"{_search_base()}/test"

    t0 = time.monotonic()
    reader_r = _http_get(reader_url, timeout=5.0)
    reader_ms = int((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    search_r = _http_get(search_url, timeout=5.0,
                          headers={"Accept": "application/json"})
    search_ms = int((time.monotonic() - t0) * 1000)

    return {
        "ok": bool(reader_r.get("ok") or search_r.get("ok")),
        "mode": "self_hosted" if _is_self_hosted() else "hosted",
        "api_key_set": bool(JINA_API_KEY),
        "reader": {
            "url": _reader_base(),
            "ok": reader_r.get("ok", False),
            "status": reader_r.get("status", 0),
            "elapsed_ms": reader_ms,
            "error": reader_r.get("error", "") if not reader_r.get("ok") else "",
        },
        "search": {
            "url": _search_base(),
            "ok": search_r.get("ok", False),
            "status": search_r.get("status", 0),
            "elapsed_ms": search_ms,
            "error": search_r.get("error", "") if not search_r.get("ok") else "",
        },
    }


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys

    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "health"
    if cmd == "health":
        print(_json.dumps(jina_health(), ensure_ascii=False, indent=2))
    elif cmd == "fetch":
        url = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(_json.dumps(jina_fetch(url), ensure_ascii=False, indent=2))
    elif cmd == "search":
        q = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(_json.dumps(jina_search(q, limit=5), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd} (use health/fetch/search)")