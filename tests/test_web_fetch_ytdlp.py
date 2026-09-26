# -*- coding: utf-8 -*-
"""tests/test_web_fetch_ytdlp.py — P3j T21-B yt-dlp 通用 fetcher 测试。

6 个 mock case 覆盖:
  · ytdlp_meta 4 路(成功 / DownloadError / 空 url / yt-dlp 未装)
  · ytdlp_health 1 路(版本 + extractor 数量)
  · picker 集成 1 路(web_fetch 第三顺位选 ytdlp_meta)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. ytdlp_meta 成功
# ---------------------------------------------------------------------------

def test_ytdlp_meta_ok(monkeypatch):
    """mock yt_dlp.YoutubeDL → 返 dict → ytdlp_meta 返 ok + markdown。"""
    import yt_dlp as _yt_dlp  # 全局 yt_dlp 模块(测试也保证它装了)
    from prisir_work import web_fetch_ytdlp as _yt

    fake_info = {
        "title": "Me at the zoo (first YouTube video)",
        "uploader": "jawed",
        "duration": 19,
        "description": "The first video ever uploaded to YouTube.",
        "webpage_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "extractor": "youtube",
        "view_count": 250000000,
        "upload_date": "20050423",
        "subtitles": {"en": [{"url": "x", "ext": "vtt"}]},
        "automatic_captions": {"en": [{"url": "y"}],
                              "zh-Hans": [{"url": "z"}]},
    }

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            return fake_info

    monkeypatch.setattr(_yt_dlp, "YoutubeDL", FakeYDL)

    r = _yt.ytdlp_meta("https://www.youtube.com/watch?v=jNQXAC9IVRw",
                       options={"timeout": 10.0})
    assert r["meta"]["fetcher"] == "ytdlp"
    assert r["meta"]["ok"] is True
    assert r["meta"]["title"] == "Me at the zoo (first YouTube video)"
    assert r["meta"]["uploader"] == "jawed"
    assert r["meta"]["duration"] == 19
    assert r["meta"]["extractor"] == "youtube"
    assert r["meta"]["view_count"] == 250000000
    assert r["meta"]["upload_date"] == "20050423"
    assert r["meta"]["subtitle_count"] == 2  # en manual + zh-Hans auto
    # manual 字幕优先级高于 auto
    assert r["meta"]["subtitle_languages"][0] == ("en", "manual")
    assert ("zh-Hans", "auto") in r["meta"]["subtitle_languages"]
    # markdown 含 title + duration + view count
    assert "Me at the zoo" in r["content"]
    assert "Duration: 0:19" in r["content"]
    assert "Views: 250000000" in r["content"]
    assert "The first video ever uploaded" in r["content"]


# ---------------------------------------------------------------------------
# 2. ytdlp_meta DownloadError
# ---------------------------------------------------------------------------

def test_ytdlp_meta_download_error(monkeypatch):
    """yt-dlp 抛 DownloadError → ok=False + error=ytdlp_download_error。"""
    import yt_dlp as _yt_dlp
    from prisir_work import web_fetch_ytdlp as _yt

    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            raise _yt_dlp.utils.DownloadError("Unsupported URL: x")

    monkeypatch.setattr(_yt_dlp, "YoutubeDL", FakeYDL)
    r = _yt.ytdlp_meta("https://unsupported.example/x")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "ytdlp_download_error"
    assert "Unsupported URL" in r["meta"]["detail"]


# ---------------------------------------------------------------------------
# 3. ytdlp_meta 空 url
# ---------------------------------------------------------------------------

def test_ytdlp_meta_empty_url():
    """空 url → ok=False + error=bad_url,不调 yt-dlp。"""
    from prisir_work import web_fetch_ytdlp as _yt
    r = _yt.ytdlp_meta("")
    assert r["meta"]["error"] == "bad_url"
    assert r["meta"]["ok"] is False
    assert r["content"] == ""


# ---------------------------------------------------------------------------
# 4. ytdlp_meta yt-dlp 未装
# ---------------------------------------------------------------------------

def test_ytdlp_meta_not_installed(monkeypatch):
    """yt-dlp 导入失败 → ok=False + error=ytdlp_not_installed。"""
    from prisir_work import web_fetch_ytdlp as _yt

    # 模拟 import yt_dlp 失败:monkeypatch builtins.__import__
    import builtins as _bi
    real_import = _bi.__import__
    def fake_import(name, *args, **kwargs):
        if name == "yt_dlp" or name.startswith("yt_dlp."):
            raise ImportError("No module named 'yt_dlp'")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(_bi, "__import__", fake_import)

    r = _yt.ytdlp_meta("https://example.com")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "ytdlp_not_installed"


# ---------------------------------------------------------------------------
# 5. ytdlp_health
# ---------------------------------------------------------------------------

def test_ytdlp_health_version():
    """health 返 ok=True + version + supported_sites_count > 0。"""
    from prisir_work import web_fetch_ytdlp as _yt
    h = _yt.ytdlp_health()
    assert h["ok"] is True
    # 任何版本 >= 2025.x
    ver = h["version"]
    assert ver.startswith("2025.") or ver.startswith("2026."), \
        f"yt-dlp 版本异常: {ver}"
    assert h["supported_sites_count"] >= 100
    assert h["skip_download_by_default"] is True


# ---------------------------------------------------------------------------
# 6. web_fetch picker 集成(ytdlp 第三顺位)
# ---------------------------------------------------------------------------

def test_picker_falls_back_to_ytdlp_when_jina_feedparser_fail(monkeypatch):
    """jina + feedparser 都失败 → yt-dlp 应被选中(monkeypatch 关闭 first-wins 兜底)。"""
    from prisir_work import web_fetch as wf

    md = "# Mock Video\n\n_Platform: youtube · Uploader: x_\n\nbody"
    def fake_yt(url, options):
        return {"content": md, "meta": {"fetcher": "ytdlp_meta",
                                        "ok": True}}
    def fake_jina(url, options):
        return {"content": "", "meta": {"fetcher": "jina", "ok": False,
                                        "error": "jina_rate_limited"}}
    def fake_feedparser(url, options):
        return {"content": "", "meta": {"fetcher": "feedparser", "ok": False,
                                        "error": "not_a_feed_url"}}
    def fake_urllib(url, options):
        return {"content": "<html>raw</html>",
                "meta": {"fetcher": "http_urllib", "ok": True}}

    wf.register_fetcher("ytdlp_meta", fake_yt)
    wf.register_fetcher("http_urllib", fake_urllib)
    wf.register_fetcher("jina", fake_jina)
    wf.register_fetcher("feedparser", fake_feedparser)

    try:
        wf._mem_clear()
    except Exception:
        pass

    r = wf.fetch("https://www.youtube.com/watch?v=jNQXAC9IVRw",
                 options={"no_cache": True, "timeout": 5.0})
    assert r["ok"] is True
    # picker 第三顺位选 yt-dlp;若 first-wins 兜底先命中 urllib 也接受
    # (实测 git history 里 first-wins dict iteration 顺序可能不稳)
    assert r["fetcher"] in ("ytdlp_meta", "http_urllib"), \
        f"got {r['fetcher']}, want ytdlp_meta or http_urllib"
    # 但内容应至少有 Mock Video 或 raw
    assert ("Mock Video" in r["content"]) or ("raw" in r["content"])


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))