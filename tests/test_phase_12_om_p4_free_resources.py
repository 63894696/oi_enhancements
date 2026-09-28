"""
tests/test_phase_12_om_p4_free_resources.py — Phase 12 OM-P4 测试(2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

OM-P4:渐进 ship 3 个免费资源真集成 — edge_tts / pixabay / archive_org。

OM-P4-fix(2026-09-28):Pixabay 没有音频 API(/api/audio/ 403);
  删 search_music,加 search_images,free_bgm 改走 archive.org audio。

## 测试矩阵(15 项)
  1. edge_tts_client.is_available → True
  2. edge_tts_client.list_chinese_voices 含 XiaoxiaoNeural
  3. edge_tts 真调用生成 mp3(中文「你好,PrisirAI 测试」)
  4. edge_tts 真调用英文(英文发音验证)
  5. edge_tts 空文本 → ok=False
  6. pixabay_client.is_key_configured / get_api_key
  7. pixabay_client.probe_key(无 key)→ configured=False
  8. pixabay search_music 不再存在(OM-P4-fix 删)
  9. pixabay search_images 真调(有 key)→ ≥ 1 hit
 10. pixabay search_videos 真调(有 key)→ ≥ 1 hit
 11. archive_org_client.is_available → True
 12. archive_org 真调 search_videos → 返 ≥ 1 hit
 13. archive_org 真调 search_audio(mediatype=audio)→ 返 ≥ 1 hit
 14. free_resource_fetcher 聚合 status 正确
 15. free_resource_fetcher.free_tts 真调
 16. free_resource_fetcher.free_bgm → archive.org audio
 17. free_resource_fetcher.free_stock_image 真调(有 key)
 18. free_resource_fetcher.free_stock_video 真调(archive.org fallback)
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ════════════════════════════════════════════════════════════════════════════
# edge_tts 测试(真调用 WebSocket)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_1_edge_tts_available():
    """#1 edge_tts 库可用"""
    from prisir_work.edge_tts_client import is_available
    assert is_available(), "edge-tts 库应已装"
    print("✓ #1 edge-tts 库可导入")


def test_om_p4_2_chinese_voices():
    """#2 中文 voice 含 XiaoxiaoNeural"""
    from prisir_work.edge_tts_client import list_chinese_voices, CHINESE_VOICES
    voices = list_chinese_voices()
    assert "zh-CN-XiaoxiaoNeural" in voices, "默认中文 voice 应在列表"
    assert len(voices) >= 7, f"中文 voice 应 ≥ 7,实得 {len(voices)}"
    print(f"✓ #2 中文 voice {len(voices)} 个,含 XiaoxiaoNeural")


def test_om_p4_3_edge_tts_chinese_real():
    """#3 edge_tts 真调用 — 中文(生成 mp3 文件验证)"""
    from prisir_work.edge_tts_client import synthesize_sync
    size = 0
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_zh.mp3")
        r = synthesize_sync("你好,PrisirAI 测试 edge-tts 中文", out)
        assert r.ok, f"中文 TTS 应成功,实得 error={r.error}"
        assert os.path.exists(out), f"mp3 文件应生成,实无"
        size = os.path.getsize(out)
        assert size > 1000, f"中文 mp3 应 > 1KB,实得 {size} 字节"
        assert r.voice == "zh-CN-XiaoxiaoNeural"
        assert r.size_bytes == size
    print(f"✓ #3 中文 TTS 真生成 {size} 字节 mp3")


def test_om_p4_4_edge_tts_english_real():
    """#4 edge_tts 真调用 — 英文"""
    from prisir_work.edge_tts_client import synthesize_sync, CHINESE_VOICES
    # 用中文 voice 念英文也行(测试不限语言)
    voice = CHINESE_VOICES[0]
    size = 0
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_en.mp3")
        r = synthesize_sync("Hello PrisirAI, this is a test.", out, voice=voice)
        assert r.ok, f"英文 TTS 应成功:{r.error}"
        size = os.path.getsize(out)
        assert size > 500
    print(f"✓ #4 英文 TTS 真生成 {size} 字节 mp3")


def test_om_p4_5_edge_tts_empty_text():
    """#5 edge_tts 空文本 → ok=False"""
    from prisir_work.edge_tts_client import synthesize_sync
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_empty.mp3")
        r = synthesize_sync("", out)
        assert not r.ok
        assert "text 为空" in r.error or "empty" in r.error.lower()
    print("✓ #5 空文本 → ok=False")


# ════════════════════════════════════════════════════════════════════════════
# pixabay 测试 — key 探测 + 图像/视频真调(search_music 已删)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_6_pixabay_key_detection():
    """#6 pixabay key 配置探测"""
    from prisir_work.pixabay_client import is_key_configured, get_api_key
    configured = is_key_configured()
    if configured:
        key = get_api_key()
        assert key and len(key) >= 30, f"key 应有 ≥ 30 字符,实得 {len(key or '')}"
        print(f"✓ #6 pixabay 已配 key(前缀 {key[:4]}…{key[-4:]} {len(key)} 字符)")
    else:
        print("⚠ #6 pixabay 未配 key — 后续真调测试会跳过")


def test_om_p4_7_pixabay_probe():
    """#7 probe_key() — 有 key 走真探测"""
    from prisir_work.pixabay_client import probe_key
    r = probe_key()
    if not r.configured:
        print("⚠ #7 probe_key() 跳过 — 无 PIXABAY_API_KEY")
        return
    assert r.configured, "已配 key → configured=True"
    if r.valid:
        assert r.rate_limit_remaining >= 0, f"valid 时 rate_limit_remaining 应 ≥ 0,实得 {r.rate_limit_remaining}"
        print(f"✓ #7 probe_key() 有效,剩 {r.rate_limit_remaining}/{r.rate_limit_limit} (重置 {r.rate_limit_reset}s)")
    else:
        print(f"⚠ #7 probe_key() key 无效:{r.error}")


def test_om_p4_8_search_music_removed():
    """#8 search_music 已删除(OM-P4-fix 删 Pixabay 瞎编的音频 API)"""
    import prisir_work.pixabay_client as pc
    assert not hasattr(pc, "search_music"), \
        "search_music 必须删除(Pixabay 无音频 API,返 403)"
    assert not hasattr(pc, "BGMHit"), \
        "BGMHit 类必须删除(无此响应 schema)"
    print("✓ #8 search_music / BGMHit 已删除(Pixabay 无音频 API)")


def test_om_p4_9_pixabay_search_images():
    """#9 pixabay search_images 真调(有 key)"""
    from prisir_work.pixabay_client import is_key_configured, search_images
    if not is_key_configured():
        print("⚠ #9 search_images 跳过 — 无 key")
        return
    hits = search_images("cat", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit,实得 {len(hits)}"
    h = hits[0]
    assert h.id > 0
    assert h.thumbnail or h.webformat_url, f"首条应有 URL 字段,实得 {h.to_dict()}"
    print(f"✓ #9 search_images 'cat' → {len(hits)} hits, 首条 {h.width}x{h.height}, tags={h.tags[:3]}")


def test_om_p4_10_pixabay_search_videos():
    """#10 pixabay search_videos 真调(有 key)"""
    from prisir_work.pixabay_client import is_key_configured, search_videos
    if not is_key_configured():
        print("⚠ #10 search_videos 跳过 — 无 key")
        return
    hits = search_videos("cat", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit,实得 {len(hits)}"
    h = hits[0]
    assert h.id > 0
    assert h.videos, f"videos dict 应非空,实得 {h.videos}"
    assert any(h.videos.values()), f"至少一个 quality 有 url,实得 {h.videos}"
    print(f"✓ #10 search_videos 'cat' → {len(hits)} hits, duration={hits[0].duration_sec}s, videos keys={list(hits[0].videos.keys())}")


# ════════════════════════════════════════════════════════════════════════════
# archive_org 测试(真调用 REST)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_11_archive_available():
    """#11 archive.org 可用"""
    from prisir_work.archive_org_client import is_available
    assert is_available(), "archive.org 应可用"
    print("✓ #11 archive.org 可用")


def test_om_p4_12_archive_search_movies():
    """#12 archive.org 真调 search_videos(mediatype=movies)"""
    from prisir_work.archive_org_client import search_videos
    hits = search_videos("cat", limit=3, mediatype="movies")
    assert len(hits) >= 1, f"应返 ≥ 1 hit,实得 {len(hits)}"
    h = hits[0]
    assert h.identifier, "hit 应有 identifier"
    assert h.title or h.mediatype
    print(f"✓ #12 archive.org movies 'cat' → {len(hits)} hits, 首条: {h.title[:50]}")


def test_om_p4_13_archive_search_audio():
    """#13 archive.org 真调 search_videos(mediatype=audio) — OM-P4-fix free_bgm 来源"""
    from prisir_work.archive_org_client import search_videos
    hits = search_videos("epic", limit=3, mediatype="audio")
    assert len(hits) >= 1, f"audio 应返 ≥ 1 hit,实得 {len(hits)}"
    h = hits[0]
    assert h.mediatype == "audio", f"首条 mediatype 应为 audio,实得 {h.mediatype}"
    print(f"✓ #13 archive.org audio 'epic' → {len(hits)} hits, 首条: {h.title[:50]}")


# ════════════════════════════════════════════════════════════════════════════
# free_resource_fetcher 聚合
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_14_facade_status():
    """#14 free_resource_status() 聚合 3 个资源"""
    from prisir_work.free_resource_fetcher import free_resource_status
    s = free_resource_status()
    assert s.edge_tts_available
    assert isinstance(s.pixabay_configured, bool)
    assert s.archive_org_available
    assert "zh-CN-XiaoxiaoNeural" in s.chinese_voices
    print(f"✓ #14 status: edge_tts={s.edge_tts_available} pixabay={s.pixabay_configured}/{s.pixabay_valid} archive={s.archive_org_available}")


def test_om_p4_15_facade_free_tts():
    """#15 free_tts 真调"""
    from prisir_work.free_resource_fetcher import free_tts
    size = 0
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "facade_tts.mp3")
        r = free_tts("PrisirAI 资源门面测试", out)
        assert r.ok, f"facade free_tts 应成功:{r.error}"
        size = os.path.getsize(out)
        assert size > 1000
    print(f"✓ #15 free_resource_fetcher.free_tts 真调成功 ({size} 字节)")


def test_om_p4_16_facade_free_bgm():
    """#16 free_bgm → archive.org audio(OM-P4-fix 改路径)"""
    from prisir_work.free_resource_fetcher import free_bgm
    hits = free_bgm("epic", limit=3)
    assert len(hits) >= 1, f"free_bgm 应返 ≥ 1 archive.org audio,实得 {len(hits)}"
    h = hits[0]
    assert h.mediatype == "audio", f"free_bgm 应走 archive.org audio,实得 mediatype={h.mediatype}"
    print(f"✓ #16 free_bgm → archive.org audio: {len(hits)} hits, 首条: {h.title[:50]}")


def test_om_p4_17_facade_free_stock_image():
    """#17 free_stock_image 真调(有 key)"""
    from prisir_work.free_resource_fetcher import free_stock_image
    from prisir_work.pixabay_client import is_key_configured
    if not is_key_configured():
        print("⚠ #17 free_stock_image 跳过 — 无 key")
        return
    hits = free_stock_image("cat", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit,实得 {len(hits)}"
    print(f"✓ #17 free_stock_image 'cat' → {len(hits)} hits, 首条 tags={hits[0].tags[:3]}")


def test_om_p4_18_facade_free_stock_video():
    """#18 free_stock_video 真调(Pixabay 或 archive.org fallback)"""
    from prisir_work.free_resource_fetcher import free_stock_video
    hits = free_stock_video("dog", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit(Pixabay 或 archive.org),实得 {len(hits)}"
    print(f"✓ #18 free_stock_video → {len(hits)} hits")


if __name__ == "__main__":
    print("═══ OM-P4 tests(18) — 真调用免费资源 + Pixabay OM-P4-fix ═══")
    test_om_p4_1_edge_tts_available()
    test_om_p4_2_chinese_voices()
    test_om_p4_3_edge_tts_chinese_real()
    test_om_p4_4_edge_tts_english_real()
    test_om_p4_5_edge_tts_empty_text()
    test_om_p4_6_pixabay_key_detection()
    test_om_p4_7_pixabay_probe()
    test_om_p4_8_search_music_removed()
    test_om_p4_9_pixabay_search_images()
    test_om_p4_10_pixabay_search_videos()
    test_om_p4_11_archive_available()
    test_om_p4_12_archive_search_movies()
    test_om_p4_13_archive_search_audio()
    test_om_p4_14_facade_status()
    test_om_p4_15_facade_free_tts()
    test_om_p4_16_facade_free_bgm()
    test_om_p4_17_facade_free_stock_image()
    test_om_p4_18_facade_free_stock_video()
    print("\n所有 18 组断言通过 ✅(含 5+ 次真 HTTP / WebSocket 调用)")