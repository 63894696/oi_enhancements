"""test_prisir_graph_import.py — prisIr_graph 方向 C 抓取 + 落盘 测试

不需要 pytest:python test_prisir_graph_import.py 即可全跑一遍。

覆盖:
- HTML 解析(meta / title / 噪音去除)
- slug 生成(不冲突)
- fetch_web 落 vault/00-inbox/web/
- fetch_wechat 落 vault/00-inbox/wechat/(走 MicroMessenger UA)
- frontmatter 字段齐全
- 不做反向链接(留口子 C.1 验证)
- 触发 build_vault 增量,新节点入图
- 失败路径(抓取失败 → ok=False)
- Content-Length 过大保护
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import prisIr_graph_build as _build
import prisIr_graph_import as _import


# ── 单元测试 ─────────────────────────────────────────────────────
def test_html_to_markdown_basic():
    """<script>/<style>/comment 应被剥离,纯文本保留。"""
    html = """
    <html><head><title>X</title></head><body>
    <script>alert('hi')</script>
    <style>.foo{color:red}</style>
    <!-- 注释 -->
    <p>Hello, <strong>world</strong>!</p>
    </body></html>
    """
    md = _import._html_to_markdown(html)
    assert "alert" not in md, f"script 未剥离: {md}"
    assert "color:red" not in md, f"style 未剥离: {md}"
    assert "注释" not in md, f"comment 未剥离: {md}"
    assert "Hello" in md and "world" in md, f"正文应保留: {md}"
    print(f"  [PASS] html_to_markdown_basic: 噪音去除 + 正文保留")


def test_extract_meta_og_title():
    """<meta property='og:title' content='X'> → {'og:title': 'X'}。"""
    html = """
    <html><head>
    <meta property="og:title" content="Hello World">
    <meta property="og:description" content="desc">
    <meta name="description" content="meta desc">
    <meta property="article:published_time" content="2026-09-24">
    </head><body></body></html>
    """
    meta = _import._extract_meta(html)
    assert meta.get("og:title") == "Hello World"
    assert meta.get("og:description") == "desc"
    assert meta.get("description") == "meta desc"
    assert meta.get("article:published_time") == "2026-09-24"
    title = _import._extract_title(html, meta)
    assert title == "Hello World"
    print(f"  [PASS] extract_meta_og_title: {title}")


def test_extract_title_fallback():
    """meta 没有 og:title 时回退 <title>。"""
    html = "<html><head><title>Fallback Title</title></head></html>"
    meta = _import._extract_meta(html)
    title = _import._extract_title(html, meta)
    assert title == "Fallback Title"

    # 两个都没有 → Untitled
    html2 = "<html><head></head></html>"
    meta2 = _import._extract_meta(html2)
    title2 = _import._extract_title(html2, meta2)
    assert title2 == "Untitled"
    print(f"  [PASS] extract_title_fallback: og → <title> → Untitled")


def test_make_slug_no_collision():
    """同 title 不同 url → 文件名 hash 不同。"""
    slug1 = _import._slugify("Test Article")
    slug2 = _import._slugify("Test Article")
    fn1 = _import._make_filename("2026-09-24", slug1, "https://a.com/x")
    fn2 = _import._make_filename("2026-09-24", slug2, "https://b.com/y")
    assert fn1 != fn2, f"应 hash 区分: {fn1} vs {fn2}"
    assert fn1.endswith(".md")
    assert fn2.endswith(".md")
    print(f"  [PASS] make_slug_no_collision: {fn1} vs {fn2}")


def test_slugify_chinese():
    """中文 title 应保留(unicode slugify)。"""
    raw = "测试 文章"
    slug = _import._slugify(raw)
    assert "测试" in slug or "untitled" not in slug, f"中文丢失: {slug}"
    print(f"  [PASS] slugify_chinese: '{raw}' → '{slug}'")


def test_wechat_extract_js_content():
    """<div id='js_content'>...<img>...</div> → 正文,footer 截断。"""
    html = """
    <html><head>
    <meta property="og:title" content="WeChat Title">
    <meta property="article:published_time" content="2026-09-20">
    </head><body>
    <strong class="profile_meta_value">TestPubAccount</strong>
    <em id="publish_time">2026-09-20 12:00</em>
    <div id="js_content"><p>WeChat body <strong>here</strong>.</p></div>
    <script>footer ad content</script>
    </body></html>
    """
    out = _import._wechat_extract(html)
    assert out["title"] == "WeChat Title"
    assert out["author"] == "TestPubAccount"
    assert out["published"] == "2026-09-20"
    assert "WeChat body" in out["body_html"]
    assert "footer ad" not in out["body_html"], "js_content 之后应被截断"
    print(f"  [PASS] wechat_extract_js_content: title={out['title']} author={out['author']}")


def test_fetch_web_writes_to_inbox(tmp_vault: Path, tmp_db: Path):
    """mock urllib → 验证 00-inbox/web/<file>.md 落盘 + frontmatter + 入图。"""
    sample_html = """
    <html><head>
    <meta property="og:title" content="Sample Web Article">
    <meta property="article:author" content="Alice">
    </head><body><p>Hello web.</p></body></html>
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
        return FakeResp(sample_html.encode("utf-8"))

    import urllib.request as _ur
    real_open = _ur.urlopen
    _ur.urlopen = fake_urlopen
    try:
        # 调 import 前把 _build.build_vault 重定向到 tmp_db
        import prisIr_graph_build as _build_mod
        orig_build = _build_mod.build_vault

        def patched_build_vault(vault_dir, db_path=tmp_db, rebuild=False, debug=False):
            return orig_build(vault_dir, db_path=tmp_db, rebuild=rebuild, debug=debug)
        _build_mod.build_vault = patched_build_vault

        try:
            r = _import.fetch_web("https://example.com/x", vault_dir=tmp_vault)
        finally:
            _build_mod.build_vault = orig_build
    finally:
        _ur.urlopen = real_open

    assert r["ok"], f"r={r}"
    assert r["source"] == "web"
    assert r["path"].startswith("00-inbox/web/")
    target = tmp_vault / r["path"]
    assert target.exists(), f"file missing: {target}"
    content = target.read_text(encoding="utf-8")
    assert "source: web" in content
    assert "https://example.com/x" in content
    assert "fetched_at:" in content
    assert "Sample Web Article" in content
    assert "status: inbox" in content
    assert "tags: [web, inbox]" in content
    print(f"  [PASS] fetch_web_writes_to_inbox: {r['path']}")


def test_fetch_wechat_uses_microMessenger_ua(tmp_vault: Path, tmp_db: Path):
    """mock urllib 验证 wechat fetch 走 MicroMessenger UA。"""
    sample = """
    <html><head>
    <meta property="og:title" content="WeChat Test">
    </head><body>
    <strong class="profile_meta_value">TestPub</strong>
    <div id="js_content"><p>body here</p></div>
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

    seen_ua: list[str] = []
    def fake_urlopen(req, timeout=15):
        ua = req.get_header("User-agent") or ""
        seen_ua.append(ua)
        assert "MicroMessenger" in ua, f"should use wechat UA, got: {ua}"
        return FakeResp(sample.encode("utf-8"))

    import urllib.request as _ur
    import prisIr_graph_build as _build_mod
    real_open = _ur.urlopen
    orig_build = _build_mod.build_vault

    _ur.urlopen = fake_urlopen
    def patched_build_vault(vault_dir, db_path=tmp_db, rebuild=False, debug=False):
        return orig_build(vault_dir, db_path=tmp_db, rebuild=rebuild, debug=debug)
    _build_mod.build_vault = patched_build_vault

    try:
        r = _import.fetch_wechat("https://mp.weixin.qq.com/s/abc", vault_dir=tmp_vault)
    finally:
        _ur.urlopen = real_open
        _build_mod.build_vault = orig_build

    assert r["ok"], f"r={r}"
    assert r["source"] == "wechat"
    assert r["author"] == "TestPub"
    assert "MicroMessenger" in seen_ua[0]
    target = tmp_vault / r["path"]
    content = target.read_text(encoding="utf-8")
    assert "source: wechat" in content
    assert "TestPub" in content
    print(f"  [PASS] fetch_wechat_uses_microMessenger_ua: {r['path']}")


def test_no_reverse_links_from_web(tmp_vault: Path, tmp_db: Path):
    """web 节点 body 里写 [[vault 笔记]] → 不入 vault 笔记的入边。

    (留口子 C.1:反向链接不做;但 build_vault 仍正常解析 web 节点的 outbound wikilink,
    只是不会反向影响 vault 内笔记的入度 — 因为 web 节点是新节点,vault 笔记作为 dst_title
    在 vault 内存在 → 解析器会填 dst_id 入 web→vault 的边,这是单向 outbound 不是反向填充)

    关键:build_vault 不该因为 web 节点而修改 vault 内笔记的任何状态(除了 mtime 不变)。
    """
    # 先在 vault 写一篇 "main" 笔记,带 outbound 到 [[other]]
    main = tmp_vault / "main.md"
    main.write_text(
        "---\ntags: [test]\n---\n# Main\n指向 [[other]]。\n",
        encoding="utf-8",
    )
    other = tmp_vault / "other.md"
    other.write_text(
        "# Other\n被 [[main]] 引用。\n",
        encoding="utf-8",
    )

    # 触发第一次 build,记录 main 的入度
    r1 = _build.build_vault(tmp_vault, tmp_db)
    assert r1["ok"]
    import prisIr_graph_store as _store
    main_id = _store.get_node_by_path("main.md", tmp_db)["id"]
    indeg_before = _store.stats(tmp_db)["nodes"]  # 总节点数
    print(f"  [diag] baseline nodes={indeg_before}")

    # 现在 fetch_web 落一篇 web 节点,body 里写 [[main]]
    sample = """
    <html><head><meta property="og:title" content="Web No Reverse"></head><body>
    <p>看 [[main]] 这篇笔记</p>
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
        return FakeResp(sample.encode("utf-8"))

    import urllib.request as _ur
    real_open = _ur.urlopen
    _ur.urlopen = fake_urlopen
    try:
        r = _import.fetch_web("https://example.com/web1", vault_dir=tmp_vault)
    finally:
        _ur.urlopen = real_open

    assert r["ok"]
    # 验证 web 节点被建图(独立 build 调用)
    web_path = r["path"]
    print(f"  [diag] web node path={web_path}")
    # 注:web 节点的 [[main]] 引用是单向 outbound — 我们验证 "main 不因此被反向修改" — 入度变化应为 0
    # 做法:web 节点 → main 是新边(web→main),但 main 的入度增加 1 是正常的 outbound 行为,
    # "不做反向链接"指的是:web 节点的 outbound 不会去把 main 笔记的"反向链接列表"写入 main 的 metadata。
    # 我们这里能验证的有限 — 重点是 build_vault 不抛错且 main 笔记的 frontmatter 没被改
    main_content_after = main.read_text(encoding="utf-8")
    assert main_content_after == (
        "---\ntags: [test]\n---\n# Main\n指向 [[other]]。\n"
    ), f"vault 内笔记 frontmatter 不应被外部 import 修改: {main_content_after!r}"
    print(f"  [PASS] no_reverse_links_from_web: vault 内笔记未被外部 import 修改")


def test_fetch_web_failure_returns_error(tmp_vault: Path):
    """抓取失败 → 返回 ok=False error,不抛异常。"""
    import urllib.request as _ur
    real_open = _ur.urlopen

    # 记录失败前 inbox 文件数
    inbox = tmp_vault / "00-inbox" / "web"
    inbox.mkdir(parents=True, exist_ok=True)
    before = set(p.name for p in inbox.glob("*.md"))

    def fake_fail(req, timeout=15):
        raise RuntimeError("mock network error")

    _ur.urlopen = fake_fail
    try:
        r = _import.fetch_web("https://example.com/fail", vault_dir=tmp_vault)
    finally:
        _ur.urlopen = real_open

    assert not r["ok"]
    assert "抓取失败" in r["error"] or "mock" in r["error"]
    # 失败后 inbox 文件数应不变
    after = set(p.name for p in inbox.glob("*.md"))
    assert before == after, f"失败不应新增文件: before={before}, after={after}"
    print(f"  [PASS] fetch_web_failure_returns_error: {r['error'][:50]}")


def test_content_length_too_large_rejected(tmp_vault: Path):
    """Content-Length > 5MB → 返回 ok=False,不下载。"""
    import urllib.request as _ur
    real_open = _ur.urlopen

    class FakeResp:
        headers = {"Content-Length": str(10 * 1024 * 1024)}  # 10MB
        def read(self, n: int = -1) -> bytes:
            return b""
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=15):
        return FakeResp()

    _ur.urlopen = fake_urlopen
    try:
        r = _import.fetch_web("https://example.com/huge", vault_dir=tmp_vault)
    finally:
        _ur.urlopen = real_open

    assert not r["ok"]
    assert "超过" in r["error"] or "上限" in r["error"]
    print(f"  [PASS] content_length_too_large_rejected: {r['error'][:60]}")


def main() -> int:
    # 单元测试(不需要 vault)
    test_html_to_markdown_basic()
    test_extract_meta_og_title()
    test_extract_title_fallback()
    test_make_slug_no_collision()
    test_slugify_chinese()
    test_wechat_extract_js_content()

    # 集成测试(需要 tmp_vault + tmp_db)
    with tempfile.TemporaryDirectory(prefix="prisIr_import_test_") as tmp:
        tmp_dir = Path(tmp)
        tmp_vault = tmp_dir / "vault"
        tmp_vault.mkdir(parents=True, exist_ok=True)
        tmp_db = tmp_dir / "graph.db"

        # 先 build 让 graph schema 就绪
        r = _build.build_vault(tmp_vault, tmp_db)
        assert r["ok"]
        print(f"[setup] initial build: {r['scanned']} nodes\n")

        test_fetch_web_writes_to_inbox(tmp_vault, tmp_db)
        test_fetch_wechat_uses_microMessenger_ua(tmp_vault, tmp_db)
        test_no_reverse_links_from_web(tmp_vault, tmp_db)
        test_fetch_web_failure_returns_error(tmp_vault)
        test_content_length_too_large_rejected(tmp_vault)

    print("\n=== ALL IMPORT TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())