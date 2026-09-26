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
# P3j T20-I.2:零配置免 key 自动走 hosted + 令牌桶限流
# ---------------------------------------------------------------------------

# jina 官方文档(https://api.jina.ai/scalar):
#   · r.jina.ai (fetch) — 无 key 20 RPM;free key 500 RPM
#   · s.jina.ai (search)— 无 key 阻塞;free key 100 RPM
# fetch 端点零配置即可用(返 cached snapshot);search 必须有 key 或自部署。
# 策略:按当前模式动态查 RPM 上限,单进程内滑动窗口令牌桶保护。
JINA_RPM_BY_MODE: dict[str, int] = {
    "hosted_no_key":   20,     # env 全空
    "hosted_with_key": 500,    # env 有 JINA_API_KEY
    "self_hosted":     10_000, # 自部署基本无限,占位
}
# 令牌桶滑动窗口:最近 60 秒内的请求时间戳 list
_QUOTA_TIMES: list[float] = []
_RPM_WINDOW_SEC = 60.0
# 错误码常量(便于上层 / 错误翻译 / verify 引用)
RATE_LIMITED_ERROR = "jina_rate_limited"


def _current_mode_str() -> str:
    """返 hosted_no_key / hosted_with_key / self_hosted。"""
    if _is_self_hosted():
        return "self_hosted"
    if JINA_API_KEY:
        return "hosted_with_key"
    return "hosted_no_key"


def _current_rpm_limit() -> int:
    """根据 env / 模式返当前 jina 端点 RPM 上限。"""
    return JINA_RPM_BY_MODE.get(_current_mode_str(),
                                JINA_RPM_BY_MODE["hosted_no_key"])


def _prune_quota(now: float) -> None:
    """清掉 60s 之外的过期时间戳。"""
    while _QUOTA_TIMES and (now - _QUOTA_TIMES[0]) > _RPM_WINDOW_SEC:
        _QUOTA_TIMES.pop(0)


def _try_consume_quota() -> bool:
    """滑动窗口令牌桶:返 True=有配额可发,False=被限流。

    单进程内做;跨进程由 jina 服务端做(撞墙会被 429)。
    自部署模式直接放过(quota=10000 实际不会触顶)。
    """
    now = time.monotonic()
    _prune_quota(now)
    limit = _current_rpm_limit()
    if len(_QUOTA_TIMES) >= limit:
        return False
    _QUOTA_TIMES.append(now)
    return True


def _refund_quota() -> None:
    """服务端正向 429 时归还令牌(不算成功请求)。"""
    try:
        if _QUOTA_TIMES:
            _QUOTA_TIMES.pop()
    except Exception:
        pass


def quota_status() -> dict[str, Any]:
    """暴露给 /web/jina/health:rpm_limit / used_last_60s / remaining。"""
    now = time.monotonic()
    _prune_quota(now)
    limit = _current_rpm_limit()
    used = len(_QUOTA_TIMES)
    return {
        "rpm_limit": limit,
        "used_last_60s": used,
        "remaining": max(0, limit - used),
        "window_seconds": _RPM_WINDOW_SEC,
    }


# ---------------------------------------------------------------------------
# 共享 HTTP 工具
# ---------------------------------------------------------------------------

def _http_get(url: str, *, timeout: float = JINA_DEFAULT_TIMEOUT,
              headers: dict[str, str] | None = None,
              params: dict[str, str] | None = None,
              no_cache: bool = False) -> dict[str, Any]:
    """调 jina 端点,统一异常处理。返 {ok, status, content, headers}。

    no_cache=True 时设 X-No-Cache: true 头(jina 5 分钟同 URL 缓存跳过)。
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
    if no_cache:
        # P3j T20-I.2:用户传 no_cache=True → 跳过 jina hosted 5 分钟缓存
        hdrs["X-No-Cache"] = "true"
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
        code = int(getattr(e, "code", 0) or 0)
        # P3j T20-I.2:撞 20 RPM hosted 限流 → 显式 jina_rate_limited
        if code == 429:
            _refund_quota()
            return {"ok": False, "status": 429,
                    "error": RATE_LIMITED_ERROR,
                    "detail": "jina hosted 20 RPM 限流;env 配 JINA_API_KEY 可升 500 RPM",
                    "content": ""}
        return {"ok": False, "status": code,
                "error": f"http_{code}", "content": ""}
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

    P3j T20-I.2:hosted 模式做令牌桶前置检查;options.no_cache=True 时
    设 X-No-Cache: true 头强制 fresh(跳过 jina 5min 同 URL 缓存)。
    """
    options = options or {}
    timeout = float(options.get("timeout", JINA_DEFAULT_TIMEOUT))
    max_chars = int(options.get("max_chars", JINA_MAX_CHARS))
    no_cache = bool(options.get("no_cache", False))

    if not url or not isinstance(url, str):
        return {"content": "", "meta": {"fetcher": "jina", "ok": False,
                                        "error": "bad_url"}}

    mode = _current_mode_str()

    # 令牌桶前置检查:仅 hosted 模式触发;自部署 quota 10000 实际不触顶
    if not _is_self_hosted():
        if not _try_consume_quota():
            return {"content": "", "meta": {
                "fetcher": "jina", "ok": False,
                "error": RATE_LIMITED_ERROR,
                "rate_limit_per_min": _current_rpm_limit(),
                "mode": mode,
                "hint": ("jina hosted 无 key 20 RPM 限流;web_fetch 会自动落 urllib。"
                         "env 配 JINA_API_KEY 可升 500 RPM"),
            }}

    # jina reader 端点:GET {reader_base}/{url}
    # 注:url 里通常有 https://,直接拼接即可
    full_url = f"{_reader_base()}/{url}"
    t0 = time.monotonic()
    r = _http_get(full_url, timeout=timeout, no_cache=no_cache)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if not r.get("ok"):
        return {"content": "", "meta": {
            "fetcher": "jina", "ok": False,
            "error": r.get("error", "jina_failed"),
            "status": r.get("status", 0),
            "detail": r.get("detail", ""),
            "elapsed_ms": elapsed_ms,
            "mode": mode,
        }}

    content = _truncate(r.get("content", ""), max_chars)
    quota = quota_status()
    meta = {
        "fetcher": "jina",
        "ok": bool(content),
        "status": r.get("status", 200),
        "elapsed_ms": elapsed_ms,
        "format": "markdown",
        "mode": mode,
        "rpm_used_last_60s": quota["used_last_60s"],
        "rpm_limit": quota["rpm_limit"],
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

    P3j T20-I.2 扩字段:
      · mode: hosted_no_key / hosted_with_key / self_hosted
      · no_key_supported: True(fetch 永远无 key 也行)
      · search_requires_key: True(search 必须 key 或自部署)
      · search.skipped: True(无 key 时不打 s.jina.ai,避免 403 污染日志)
      · quota: {rpm_limit, used_last_60s, remaining, window_seconds}

    返 {ok, mode, ...}。
    """
    mode = _current_mode_str()
    reader_url = f"{_reader_base()}/https://example.com"
    search_url = f"{_search_base()}/test"

    # reader 真探:fetch 端点始终试(hosted_no_key 也行)
    t0 = time.monotonic()
    reader_r = _http_get(reader_url, timeout=5.0)
    reader_ms = int((time.monotonic() - t0) * 1000)

    # search 探活:无 key 时直接跳过,避免 s.jina.ai 返 403 污染错误日志
    if JINA_API_KEY or JINA_SEARCH_URL:
        t0 = time.monotonic()
        search_r = _http_get(search_url, timeout=5.0,
                             headers={"Accept": "application/json"})
        search_ms = int((time.monotonic() - t0) * 1000)
        search_skipped = False
        search_error = (search_r.get("error", "")
                        if not search_r.get("ok") else "")
    else:
        # 无 key 时不调,免得返 403 污染日志
        search_r = {"ok": False, "error": "no_key_required"}
        search_ms = 0
        search_skipped = True
        search_error = "no_key_required"

    quota = quota_status()
    return {
        "ok": bool(reader_r.get("ok") or search_r.get("ok")),
        "mode": mode,
        "api_key_set": bool(JINA_API_KEY),
        "self_hosted": _is_self_hosted(),
        "no_key_supported": True,        # fetch 永远无 key 也行
        "search_requires_key": True,    # search 必须 key / 自部署
        "quota": quota,
        "reader": {
            "url": _reader_base(),
            "ok": reader_r.get("ok", False),
            "status": reader_r.get("status", 0),
            "elapsed_ms": reader_ms,
            "error": (reader_r.get("error", "")
                      if not reader_r.get("ok") else ""),
        },
        "search": {
            "url": _search_base(),
            "ok": search_r.get("ok", False),
            "status": search_r.get("status", 0),
            "elapsed_ms": search_ms,
            "error": search_error,
            "skipped": search_skipped,
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