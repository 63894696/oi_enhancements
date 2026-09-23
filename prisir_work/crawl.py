# -*- coding: utf-8 -*-
"""全站爬虫 v0.1.0 (P2.5+17c, 2026-09-23)

对 wigolo crawl 工具的 Prisir 对应:
  - BFS 同 host 内链抓取,robots.txt 尊重,每 host 限速
  - 抓取内容自动落 web_fetch cache(下次直接命中)

任何 fetch 失败 / robots 拒绝 / 深度超限 → 跳过,warnings 透出。
零外部依赖(robots.txt 用 urllib + 自写简单 parser)。
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, **extra) -> dict:
    out = {"step": name, "ok": True, "duration_ms": 0}
    out.update(extra)
    return out


_LINK_RE = re.compile(r'<a[^>]+href=["\\\']([^"\\\']+)["\\\']', re.IGNORECASE)


def _extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 提所有 href,绝对化 + 过滤空/锚/javascript:"""
    out = []
    seen = set()
    for m in _LINK_RE.finditer(html or ""):
        href = (m.group(1) or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") \
                or href.startswith("mailto:"):
            continue
        # 绝对化
        full = urljoin(base_url, href)
        # 去掉 fragment
        try:
            parsed = urlparse(full)
            full = parsed._replace(fragment="").geturl()
        except Exception:
            continue
        # 去重
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def _normalize_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _robots_allowed(robots_url: str, user_agent: str, path: str,
                    timeout: float = 4.0) -> bool:
    """读 robots.txt,无 robots → 允许;有 → 用 RobotFileParser 判。

    优先走 web_fetch(可被 mock 拦截,E2E 用);fallback 到 raw urllib。

    任何 IO 异常 → 视为允许(失败降级)。
    """
    content = None
    # 1) 走 web_fetch(走 mock 注册表,E2E / 测试友好)
    try:
        from prisir_work import web_fetch as _wf
        r = _wf.fetch(robots_url, options={"timeout": timeout})
        if r.get("ok") and r.get("content"):
            content = r["content"]
    except Exception:
        pass
    # 2) fallback raw urllib(无 web_fetch 或 fetch 失败时)
    if content is None:
        try:
            import urllib.request
            req = urllib.request.Request(robots_url, headers={
                "User-Agent": user_agent,
                "Accept": "text/plain,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return True  # 拿不到 robots → 放行

    try:
        rp = RobotFileParser()
        # 不调用 read(),直接喂文本
        import io
        rp.parse(io.StringIO(content).readlines())
        return rp.can_fetch(user_agent, path)
    except Exception:
        return True


def crawl(start_url: str, *, max_pages: int = 30, max_depth: int = 2,
          same_host: bool = True, respect_robots: bool = True,
          rate_per_host: float = 1.0, timeout: float = 10.0,
          user_agent: str = "PrisirCrawler/0.1") -> dict:
    """BFS 抓取同 host 页面。

    Args:
        start_url: 起点 URL
        max_pages: 最多抓多少页(默认 30)
        max_depth: BFS 最大深度(start=0,start 里的链接=1,...)
        same_host: 是否只抓同 host(默认 True)
        respect_robots: 是否读 robots.txt 判 Allow(默认 True)
        rate_per_host: 每 host 两次抓之间的最少间隔秒数(默认 1.0)
        timeout: 单页 fetch 超时
        user_agent: robots.txt 判 Allow 时用的 UA

    Returns:
    {
        'ok': True,
        'start_url': str,
        'pages': [{'url', 'title', 'host', 'depth', 'size_chars', 'fetched_at'}],
        'skipped': [{'url', 'reason'}],
        'links_seen': int,
        'warnings': list[str],
        'steps': [...]
    }
    """
    warnings: list[str] = []
    steps: list[dict] = []
    pages: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    last_fetch_per_host: dict[str, float] = {}

    if not start_url or not isinstance(start_url, str) or not start_url.strip():
        return {"ok": False, "start_url": start_url, "pages": [],
                "skipped": [], "links_seen": 0, "warnings": ["empty_url"], "steps": []}

    try:
        from prisir_work import web_fetch as _wf
    except Exception as e:
        return {"ok": False, "start_url": start_url, "pages": [],
                "skipped": [], "links_seen": 0,
                "warnings": [f"import_error:{type(e).__name__}"], "steps": []}

    start_host = _normalize_host(start_url)
    if not start_host:
        return {"ok": False, "start_url": start_url, "pages": [],
                "skipped": [], "links_seen": 0, "warnings": ["bad_start_url"], "steps": []}

    # robots.txt 缓存:每 host 一份 RobotFileParser 实例
    robots_cache: dict[str, RobotFileParser | None] = {}

    # 简单标题提取
    title_re = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)

    # BFS 队列:[(url, depth)]
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    seen.add(start_url)

    t_total = _now_ms()
    pages_fetched = 0
    total_links = 0

    while queue and pages_fetched < max_pages:
        url, depth = queue.popleft()
        host = _normalize_host(url)

        if same_host and host != start_host:
            skipped.append({"url": url, "reason": "off_host"})
            continue
        if depth > max_depth:
            skipped.append({"url": url, "reason": "depth_exceeded"})
            continue

        # robots.txt 判 Allow
        if respect_robots:
            # 用 host 路径 /robots.txt
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = robots_cache.get(host)
            if host not in robots_cache:
                rp = _load_robots_parser(robots_url, timeout=min(4.0, timeout))
                robots_cache[host] = rp
            allowed = True
            if rp is not None:
                try:
                    allowed = rp.can_fetch(user_agent, parsed.path)
                except Exception:
                    allowed = True
            if not allowed:
                skipped.append({"url": url, "reason": "robots_disallowed"})
                continue

        # 限速:每个 host 间隔
        if rate_per_host > 0:
            last = last_fetch_per_host.get(host, 0.0)
            wait = rate_per_host - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)

        # 抓
        t0 = _now_ms()
        try:
            r = _wf.fetch(url, options={"timeout": timeout})
        except Exception as e:  # noqa: BLE001
            skipped.append({"url": url, "reason": f"fetch_error:{type(e).__name__}"})
            continue
        last_fetch_per_host[host] = time.monotonic()
        fetch_ms = _now_ms() - t0

        if not r.get("ok") or not r.get("content"):
            skipped.append({"url": url, "reason": "fetch_failed"})
            continue

        content = r["content"]
        pages_fetched += 1
        tm = title_re.search(content)
        title = _strip_tags(tm.group(1)).strip() if tm else ""
        pages.append({
            "url": url,
            "title": title,
            "host": host,
            "depth": depth,
            "size_chars": len(content),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

        # BFS 下一层
        if depth < max_depth:
            new_links = _extract_links(content, url)
            total_links += len(new_links)
            for link in new_links:
                if link in seen:
                    continue
                seen.add(link)
                queue.append((link, depth + 1))

    steps.append(_step("crawl",
                        pages=len(pages), skipped=len(skipped),
                        links_seen=total_links,
                        duration_ms=_now_ms() - t_total))

    return {
        "ok": True,
        "start_url": start_url,
        "pages": pages,
        "skipped": skipped,
        "links_seen": total_links,
        "warnings": warnings,
        "steps": steps,
    }


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _load_robots_parser(robots_url: str, timeout: float = 4.0) -> RobotFileParser | None:
    """拉 robots.txt 解析成 RobotFileParser。

    走 web_fetch(mockable)→ fallback raw urllib → 失败返 None(调用方按 allow 处理)。
    """
    content = None
    try:
        from prisir_work import web_fetch as _wf
        r = _wf.fetch(robots_url, options={"timeout": timeout})
        if r.get("ok") and r.get("content"):
            content = r["content"]
    except Exception:
        pass
    if content is None:
        try:
            import urllib.request
            req = urllib.request.Request(robots_url, headers={
                "User-Agent": "PrisirCrawler/0.1",
                "Accept": "text/plain,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None
    try:
        rp = RobotFileParser()
        import io
        rp.parse(io.StringIO(content).readlines())
        return rp
    except Exception:
        return None


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print('usage: python -m prisir_work.crawl <start_url> [--max-pages N] [--max-depth N]')
        raise SystemExit(2)
    _url = _sys.argv[1]
    _max_p = int(_sys.argv[_sys.argv.index("--max-pages") + 1]) if "--max-pages" in _sys.argv else 30
    _max_d = int(_sys.argv[_sys.argv.index("--max-depth") + 1]) if "--max-depth" in _sys.argv else 2
    print(json.dumps(crawl(_url, max_pages=_max_p, max_depth=_max_d),
                     ensure_ascii=False, indent=2))