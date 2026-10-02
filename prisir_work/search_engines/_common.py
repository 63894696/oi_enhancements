"""_common.py — 76 引擎共享工具:HTTP / HTML 清洗 / XML 提子串。

被 prisIr_work/search_engines/*.py 全部 provider 函数复用。
Provider 失败统一在自身 try/except 内捕获,本模块只抛异常。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


def _http_get(url: str, *, timeout: float = 8.0,
              headers: dict[str, str] | None = None) -> str:
    """简单 GET,自动套 UA。失败抛异常。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _json_get(url: str, *, timeout: float = 8.0,
              headers: dict[str, str] | None = None):
    """GET JSON。失败抛异常。"""
    text = _http_get(url, timeout=timeout,
                     headers={**(headers or {}), "Accept": "application/json"})
    return json.loads(text)


_HTML_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_RE.sub("", s or "").strip()


def _between(s: str, start: str, end: str) -> str:
    """截 start/end 之间,失败返 ''。"""
    try:
        a = s.index(start)
        b = s.index(end, a + len(start))
        return s[a + len(start):b]
    except ValueError:
        return ""


def _strip_xml(s: str) -> str:
    """Atom/RSS XML 内 title/summary 可能有内嵌标签。"""
    return _HTML_RE.sub(" ", s or "").strip()


def _truncate(s: str, n: int) -> str:
    """截字符串到 n 字符。"""
    if not s:
        return ""
    s = s.strip()
    return s[:n].rsplit(" ", 1)[0] + ("…" if len(s) > n else "")


def _url_quoted(s: str) -> str:
    return urllib.parse.quote(s or "", safe="")


# 避免循环 import,放底层 url 工具
import urllib.parse  # noqa: E402