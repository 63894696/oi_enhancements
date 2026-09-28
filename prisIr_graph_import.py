"""prisIr_graph_import.py — 抓网页/微信公众号文章 → 落 vault/00-inbox/{web,wechat}/ → 入 wikilink 图谱

设计:
- 零外部依赖(标准库 + html2text)
- 微信走 MicroMessenger UA 绕过风控(memory/wechat-article-fetch-ua-bypass.md)
- 落盘后自动触发 prisIr_graph_build.build_vault() 增量更新,新节点入图
- 不做反向链接(留口子 C.1)
- 不做去重(留口子 C.2 — 用户后续手动整理)

可独立测试:
    python prisIr_graph_import.py     # 跑内置 smoke test
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# html2text 是可选依赖(失败时降级纯文本)
try:
    import html2text  # type: ignore
    _HAS_HTML2TEXT = True
except ImportError:
    _HAS_HTML2TEXT = False

# ── 配置 ──────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT = 15
_MAX_HTML_BYTES = 5 * 1024 * 1024  # 5MB

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 微信公众号专用 — 手机微信 UA 绕过风控(2026-08-14 已验证)
_WECHAT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Mobile/15E148 MicroMessenger/8.0.44"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# HTML 元素匹配
_META_TAG_RE = re.compile(
    r"""<meta\s+(?:[^>]*?\s+)?(?:property|name)=["']([^"']+)["']\s+(?:[^>]*?\s+)?content=["']([^"']*)["']""",
    re.IGNORECASE,
)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_WECHAT_CONTENT_RE = re.compile(
    r'<div\s+id=["\']js_content["\'][^>]*>(.*?)</div>\s*<script',
    re.IGNORECASE | re.DOTALL,
)
_WECHAT_AUTHOR_RE = re.compile(
    r'<strong[^>]*class=["\']profile_meta_value["\'][^>]*>([^<]+)</strong>',
    re.IGNORECASE,
)
_WECHAT_PUBLISHED_RE = re.compile(
    r'em\s+id=["\']publish_time["\'][^>]*>([^<]+)</em>',
    re.IGNORECASE,
)

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ── 工具 ──────────────────────────────────────────────────────────
def _resolve_default_vault() -> Path:
    try:
        from vault_tools import VAULT_DIR as VAULT
        return VAULT
    except Exception:
        return Path(r"C:\Users\Administrator\Documents\ObsidianVault")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_date_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _slugify(text: str, max_len: int = 60) -> str:
    """生成文件名安全 slug:保留 ASCII 字母/数字/中文/连字符,其余替换。"""
    s = text.strip().lower()
    # 保留 ASCII alphanumeric + Chinese + hyphen + space
    s = re.sub(r"[^\w一-鿿\- ]+", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s)
    s = s.strip("-")
    if not s:
        s = "untitled"
    return s[:max_len]


def _make_filename(date_str: str, slug: str, url: str) -> str:
    """YYYY-MM-DD-<slug>-<hash6>.md,同 title 不同 URL 用 hash 区分。"""
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:6]
    return f"{date_str}-{slug}-{h}.md"


# ── HTTP 抓取 ─────────────────────────────────────────────────────
def _fetch_html(url: str, headers: dict | None = None) -> str:
    """抓 HTML 文本;非 200/超时/过大 → 抛 ImportError。"""
    req = urllib.request.Request(url, headers=headers or _DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            status = getattr(resp, "status", 200) or 200
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            # 长度检查
            cl_header = resp.headers.get("Content-Length")
            if cl_header and cl_header.isdigit() and int(cl_header) > _MAX_HTML_BYTES:
                raise RuntimeError(f"Content-Length={cl_header} 超过 {_MAX_HTML_BYTES} 字节上限")
            raw = resp.read(_MAX_HTML_BYTES + 1)
            if len(raw) > _MAX_HTML_BYTES:
                raise RuntimeError(f"实际响应超过 {_MAX_HTML_BYTES} 字节上限")
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL 错误: {e}") from e
    except TimeoutError as e:
        raise RuntimeError(f"超时: {e}") from e
    except Exception as e:
        raise RuntimeError(f"抓取失败: {e}") from e

    # 解码
    charset = "utf-8"
    try:
        # 尝试从 HTML meta 检测
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:2048], re.IGNORECASE)
        if m:
            charset = m.group(1).decode("ascii", errors="replace").lower()
    except Exception:
        pass
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


# ── HTML 解析 ─────────────────────────────────────────────────────
def _strip_noise(html: str) -> str:
    """去 script/style/comment 标签。"""
    html = _SCRIPT_RE.sub("", html)
    html = _STYLE_RE.sub("", html)
    html = _HTML_COMMENT_RE.sub("", html)
    return html


def _extract_meta(html: str) -> dict[str, str]:
    """提取 og:title / og:description / og:image / og:site_name 等 meta。"""
    meta: dict[str, str] = {}
    for m in _META_TAG_RE.finditer(html):
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        if not value:
            continue
        # 只收 og:* 和 article:* 和 description 类
        if key.startswith("og:") or key.startswith("article:") or key == "description":
            meta[key] = value
    return meta


def _extract_title(html: str, meta: dict[str, str]) -> str:
    """优先级:og:title > <title> > meta title。"""
    title = meta.get("og:title") or meta.get("title")
    if title:
        return title.strip()
    m = _TITLE_TAG_RE.search(html)
    if m:
        return m.group(1).strip()
    return "Untitled"


def _html_to_markdown(html: str) -> str:
    """HTML → markdown(html2text);不可用则降级纯文本。"""
    cleaned = _strip_noise(html)
    if _HAS_HTML2TEXT:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.body_width = 0  # 不自动换行
        try:
            return h.handle(cleaned).strip()
        except Exception:
            pass
    # 降级:去所有标签
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _wechat_extract(html: str) -> dict[str, str]:
    """微信文章专用:正文 <div id="js_content">,author/published 特殊 meta。"""
    meta = _extract_meta(html)
    out: dict[str, str] = {
        "title": _extract_title(html, meta),
        "author": "",
        "published": meta.get("article:published_time", ""),
        "site_name": meta.get("og:site_name", ""),
    }
    # 公众号名兜底
    m = _WECHAT_AUTHOR_RE.search(html)
    if m:
        out["author"] = m.group(1).strip()
    if not out["published"]:
        m = _WECHAT_PUBLISHED_RE.search(html)
        if m:
            out["published"] = m.group(1).strip()
    # 正文
    m = _WECHAT_CONTENT_RE.search(html)
    if m:
        out["body_html"] = m.group(1)
    else:
        out["body_html"] = html  # 兜底走通用提取
    return out


# ── 落盘 ──────────────────────────────────────────────────────────
def _escape_yaml_string(s: str) -> str:
    """简化的 YAML 字符串转义:双引号包裹 + 转义内部双引号/反斜杠。"""
    if not s:
        return '""'
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    # 含特殊字符走引号包裹
    if any(c in s for c in [":", "#", "&", "*", "!", "|", ">", "<", "%", "@", "`", "\n", "\t"]):
        return f'"{s}"'
    return s if not any(c in s for c in ' "{}[]') else f'"{s}"'


def _render_frontmatter(meta: dict[str, Any]) -> str:
    """渲染 frontmatter 块(YAML 子集,够用即可)。"""
    lines = ["---"]
    lines.append(f"source: {_escape_yaml_string(meta['source'])}")
    lines.append(f"url: {_escape_yaml_string(meta['url'])}")
    lines.append(f"title: {_escape_yaml_string(meta['title'])}")
    if meta.get("author"):
        lines.append(f"author: {_escape_yaml_string(meta['author'])}")
    if meta.get("published"):
        lines.append(f"published: {_escape_yaml_string(meta['published'])}")
    if meta.get("site_name"):
        lines.append(f"site_name: {_escape_yaml_string(meta['site_name'])}")
    lines.append(f"fetched_at: {_escape_yaml_string(meta['fetched_at'])}")
    lines.append("status: inbox")
    lines.append(f"tags: [{meta['source']}, inbox]")
    lines.append("---")
    return "\n".join(lines)


def _render_md(source: str, meta: dict[str, Any], body_md: str, url: str) -> str:
    """渲染完整 markdown 文件:frontmatter + 标题 + 来源块 + 正文。"""
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
    except Exception:
        pass

    published = meta.get("published") or ""
    source_line = f"> 来源:[{domain}]({url})" + (f" · {published}" if published else "")

    parts = [
        _render_frontmatter(meta),
        "",
        f"# {meta['title']}",
        "",
        source_line,
        "",
        body_md.strip(),
        "",
    ]
    return "\n".join(parts)


def _write_to_inbox(source: str, slug: str, url: str, content: str, vault_dir: Path) -> Path:
    """写 md 到 vault/00-inbox/{source}/YYYY-MM-DD-<slug>-<hash6>.md,重名加 hash。"""
    inbox = vault_dir / "00-inbox" / source
    inbox.mkdir(parents=True, exist_ok=True)
    date_str = _now_date_str()
    filename = _make_filename(date_str, slug, url)
    target = inbox / filename

    # 重名追加 hash(虽然 _make_filename 已带 hash,二次防御)
    if target.exists():
        h = hashlib.md5((url + str(time.time())).encode("utf-8")).hexdigest()[:6]
        stem = f"{date_str}-{slug}-{h}.md"
        target = inbox / stem

    target.write_text(content, encoding="utf-8")
    return target


# ── 触发增量 build ─────────────────────────────────────────────────
def _trigger_build_incremental(vault_dir: Path) -> dict:
    """调 prisIr_graph_build.build_vault 让新节点入图。失败不阻塞。"""
    try:
        import prisIr_graph_build as _build
        r = _build.build_vault(vault_dir)
        return {
            "ok": r.get("ok", False),
            "scanned": r.get("scanned", 0),
            "added": r.get("added", 0),
            "updated": r.get("updated", 0),
            "elapsed_sec": r.get("elapsed_sec", 0),
        }
    except Exception as e:
        return {"ok": False, "error": f"build_vault 失败: {e}"}


# ── 公开 API ──────────────────────────────────────────────────────
def fetch_web(url: str, vault_dir: Path | None = None) -> dict:
    """抓网页 → 落 vault/00-inbox/web/ → 触发 build_vault 增量更新。

    Returns:
        {ok, source, path, title, url, body_chars, build_result}
    """
    if vault_dir is None:
        vault_dir = _resolve_default_vault()

    try:
        html = _fetch_html(url, headers=_DEFAULT_HEADERS)
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}", "url": url}

    meta_html = _extract_meta(html)
    title = _extract_title(html, meta_html)
    body_md = _html_to_markdown(html)
    if not body_md.strip():
        return {"ok": False, "error": "正文为空(可能被登录墙/验证码拦截)", "url": url}

    slug = _slugify(title)
    fm: dict[str, Any] = {
        "source": "web",
        "url": url,
        "title": title,
        "author": meta_html.get("article:author", ""),
        "published": meta_html.get("article:published_time", ""),
        "site_name": meta_html.get("og:site_name", ""),
        "fetched_at": _now_iso(),
    }
    md = _render_md("web", fm, body_md, url)

    try:
        target = _write_to_inbox("web", slug, url, md, vault_dir)
    except Exception as e:
        return {"ok": False, "error": f"落盘失败: {e}", "url": url}

    build_result = _trigger_build_incremental(vault_dir)

    return {
        "ok": True,
        "source": "web",
        "path": str(target.relative_to(vault_dir)).replace("\\", "/"),
        "title": title,
        "url": url,
        "body_chars": len(body_md),
        "build_result": build_result,
    }


def fetch_wechat(url: str, vault_dir: Path | None = None) -> dict:
    """抓微信公众号文章 → 落 vault/00-inbox/wechat/ → 触发 build_vault 增量更新。

    Returns:
        {ok, source, path, title, url, author, body_chars, build_result}
    """
    if vault_dir is None:
        vault_dir = _resolve_default_vault()

    if "mp.weixin.qq.com" not in url:
        # 非微信域,提示但仍走通用 fetch_wechat 路径(用户自定义公众号域可走)
        pass

    try:
        html = _fetch_html(url, headers=_WECHAT_HEADERS)
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}", "url": url}

    wechat_meta = _wechat_extract(html)
    body_md = _html_to_markdown(wechat_meta["body_html"])
    if not body_md.strip():
        return {"ok": False, "error": "正文为空(可能风控拦截,确认 UA 绕过是否失效)", "url": url}

    slug = _slugify(wechat_meta["title"])
    fm: dict[str, Any] = {
        "source": "wechat",
        "url": url,
        "title": wechat_meta["title"],
        "author": wechat_meta.get("author", ""),
        "published": wechat_meta.get("published", ""),
        "site_name": wechat_meta.get("site_name", ""),
        "fetched_at": _now_iso(),
    }
    md = _render_md("wechat", fm, body_md, url)

    try:
        target = _write_to_inbox("wechat", slug, url, md, vault_dir)
    except Exception as e:
        return {"ok": False, "error": f"落盘失败: {e}", "url": url}

    build_result = _trigger_build_incremental(vault_dir)

    return {
        "ok": True,
        "source": "wechat",
        "path": str(target.relative_to(vault_dir)).replace("\\", "/"),
        "title": wechat_meta["title"],
        "url": url,
        "author": fm["author"],
        "body_chars": len(body_md),
        "build_result": build_result,
    }


# ── Smoke Test ───────────────────────────────────────────────────
def _smoke() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="prisImport_smoke_") as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()

        # mock urllib,避免真抓
        sample_html = """
        <html><head>
        <meta property="og:title" content="Test Article">
        <meta property="og:description" content="A test article">
        <meta property="article:author" content="Alice">
        </head><body>
        <script>alert(1)</script>
        <h1>Test</h1>
        <p>Hello, <strong>world</strong>!</p>
        <a href="https://example.com">link</a>
        </body></html>
        """

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
                self.headers = {"Content-Length": str(len(data))}
            def read(self, n: int = -1) -> bytes:
                return self._data[:n] if n > 0 else self._data
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout=15):
            ua = req.get_header("User-agent") or ""
            print(f"  [mock] req URL={req.full_url} UA={ua[:30]}...")
            return FakeResp(sample_html.encode("utf-8"))

        import prisIr_graph_import as _imp
        import urllib.request as _ur

        # web 抓取(mock urlopen)
        real_open = _ur.urlopen
        _ur.urlopen = fake_urlopen
        try:
            r = _imp.fetch_web("https://example.com/article.html", vault_dir=vault)
            print(f"\n[smoke] fetch_web → {r}")
            assert r["ok"], f"expected ok, got {r}"
            assert r["source"] == "web"
            assert (vault / "00-inbox" / "web" / r["path"].split("/")[-1]).exists()
            content = (vault / "00-inbox" / "web" / r["path"].split("/")[-1]).read_text(encoding="utf-8")
            assert "source: web" in content
            # url 字段会被引号包裹(URL 含 : 和 /)
            assert ('url: "https://example.com/article.html"' in content
                    or "url: https://example.com/article.html" in content)
            assert "fetched_at:" in content
            assert "Test Article" in content
            assert "Hello" in content
            print(f"  [PASS] fetch_web wrote: {r['path']}")

            # wechat 抓取(同样 mock 数据,但走 wechat UA 路径)
            sample_wechat = """
            <html><head>
            <meta property="og:title" content="WeChat Article">
            <meta property="article:published_time" content="2026-09-20">
            </head><body>
            <strong class="profile_meta_value">TestPubAccount</strong>
            <em id="publish_time">2026-09-20 12:00</em>
            <div id="js_content"><p>WeChat body <strong>here</strong>.</p></div>
            <script>footer ad</script>
            </body></html>
            """
            def fake_urlopen_wechat(req, timeout=15):
                ua = req.get_header("User-agent") or ""
                assert "MicroMessenger" in ua, f"wechat should use MicroMessenger UA, got: {ua}"
                print(f"  [mock] wechat UA verified")
                return FakeResp(sample_wechat.encode("utf-8"))
            _ur.urlopen = fake_urlopen_wechat
            r2 = _imp.fetch_wechat("https://mp.weixin.qq.com/s/test123", vault_dir=vault)
            print(f"\n[smoke] fetch_wechat → {r2}")
            assert r2["ok"], f"expected ok, got {r2}"
            assert r2["source"] == "wechat"
            assert r2["author"] == "TestPubAccount"
            content2 = (vault / "00-inbox" / "wechat" / r2["path"].split("/")[-1]).read_text(encoding="utf-8")
            assert "source: wechat" in content2
            assert "author: TestPubAccount" in content2
            assert "WeChat body" in content2
            # 验证 js_content 截断生效(footer ad 不应出现)
            assert "footer ad" not in content2, "wechat 正文应只到 js_content,不含 footer"
            print(f"  [PASS] fetch_wechat wrote: {r2['path']}")

            # slug 不冲突(切回 web mock)
            _ur.urlopen = fake_urlopen
            r3 = _imp.fetch_web("https://example.com/different-url", vault_dir=vault)
            assert r3["ok"], f"r3 not ok: {r3}"
            assert r3["path"] != r["path"], f"同 title 不同 URL 应生成不同文件名"
            print(f"  [PASS] slug collision: {r['path'].split('/')[-1]} vs {r3['path'].split('/')[-1]}")

            # 失败路径
            def fake_fail(req, timeout=15):
                raise RuntimeError("mock network error")
            _ur.urlopen = fake_fail
            r4 = _imp.fetch_web("https://example.com/fail", vault_dir=vault)
            assert not r4["ok"]
            assert "抓取失败" in r4["error"]
            print(f"  [PASS] failure path: {r4['error'][:50]}")
        finally:
            _ur.urlopen = real_open

        print("\n[smoke] ALL PASS")


if __name__ == "__main__":
    _smoke()
    sys.exit(0)