"""
tests/test_phase_12_om_p4_free_resources.py — Phase 12 OM-P4 测试(2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

OM-P4:渐进 ship 3 个免费资源真集成 — edge_tts / pixabay / archive_org。

## 测试矩阵(11 项)
  1. edge_tts_client.is_available → True
  2. edge_tts_client.list_chinese_voices 含 XiaoxiaoNeural
  3. edge_tts 真调用生成 mp3(中文「你好,PrisirAI 测试」)
  4. edge_tts 真调用英文(英文发音验证)
  5. edge_tts 空文本 → ok=False
  6. pixabay_client.is_key_configured / get_api_key
  7. pixabay_client.probe_key(无 key)→ configured=False
  8. pixabay 真调(无 key)→ search_music 返 []
  9. archive_org_client.is_available → True
 10. archive_org 真调 search_videos → 返 ≥ 1 hit
 11. free_resource_fetcher 聚合 status 正确
 12. free_resource_fetcher.free_tts 真调
 13. free_resource_fetcher.free_stock_video 真调
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
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_en.mp3")
        r = synthesize_sync("Hello PrisirAI, this is a test.", out, voice=voice)
        assert r.ok, f"英文 TTS 应成功:{r.error}"
        assert os.path.getsize(out) > 500
    print(f"✓ #4 英文 TTS 真生成 mp3")


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
# pixabay 测试(无 key 时 graceful)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_6_pixabay_no_key():
    """#6 pixabay 无 key → is_key_configured=False"""
    # 清 env 强制无 key
    import os
    old = os.environ.pop("PIXABAY_API_KEY", None)
    try:
        from prisir_work.pixabay_client import is_key_configured, get_api_key
        # easel/.env 可能存在,先看
        if get_api_key():
            print("⚠ #6 跳过 — easel/.env 有 PIXABAY_API_KEY")
            return
        assert not is_key_configured()
    finally:
        if old:
            os.environ["PIXABAY_API_KEY"] = old
    print("✓ #6 pixabay 无 key → configured=False")


def test_om_p4_7_pixabay_probe_no_key():
    """#7 probe_key() 无 key → configured=False, error=未配置"""
    import os
    old = os.environ.pop("PIXABAY_API_KEY", None)
    try:
        from prisir_work.pixabay_client import probe_key
        r = probe_key()
        assert not r.configured
        assert not r.valid
        assert "未配置" in r.error
    finally:
        if old:
            os.environ["PIXABAY_API_KEY"] = old
    print(f"✓ #7 probe_key() 无 key → configured=False")


def test_om_p4_8_pixabay_search_no_key():
    """#8 pixabay 无 key → search_music 返 []"""
    import os
    old = os.environ.pop("PIXABAY_API_KEY", None)
    try:
        from prisir_work.pixabay_client import search_music
        hits = search_music("epic")
        assert hits == [], "无 key 应返空"
    finally:
        if old:
            os.environ["PIXABAY_API_KEY"] = old
    print("✓ #8 search_music 无 key → 返 []")


# ════════════════════════════════════════════════════════════════════════════
# archive_org 测试(真调用 REST)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_9_archive_available():
    """#9 archive.org 可用"""
    from prisir_work.archive_org_client import is_available
    assert is_available(), "archive.org 应可用"
    print("✓ #9 archive.org 可用")


def test_om_p4_10_archive_search_real():
    """#10 archive.org 真调 search_videos → 返 hits"""
    from prisir_work.archive_org_client import search_videos
    hits = search_videos("cat", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit,实得 {len(hits)}"
    h = hits[0]
    assert h.identifier, "hit 应有 identifier"
    assert h.title or h.mediatype
    print(f"✓ #10 archive.org 真搜 'cat' → {len(hits)} hits, 首条: {h.title[:50]}")


# ════════════════════════════════════════════════════════════════════════════
# free_resource_fetcher 聚合
# ════════════════════════════════════════════════════════════════════════════

def test_om_p4_11_facade_status():
    """#11 free_resource_status() 聚合 3 个资源"""
    from prisir_work.free_resource_fetcher import free_resource_status
    s = free_resource_status()
    assert s.edge_tts_available
    # pixabay 视 env
    assert isinstance(s.pixabay_configured, bool)
    assert s.archive_org_available
    assert "zh-CN-XiaoxiaoNeural" in s.chinese_voices
    print(f"✓ #11 status: edge_tts={s.edge_tts_available} pixabay={s.pixabay_configured} archive={s.archive_org_available}")


def test_om_p4_12_facade_free_tts():
    """#12 free_tts 真调"""
    from prisir_work.free_resource_fetcher import free_tts
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "facade_tts.mp3")
        r = free_tts("PrisirAI 资源门面测试", out)
        assert r.ok, f"facade free_tts 应成功:{r.error}"
        assert os.path.getsize(out) > 1000
    print("✓ #12 free_resource_fetcher.free_tts 真调成功")


def test_om_p4_13_facade_free_stock_video():
    """#13 free_stock_video 真调(archive.org fallback)"""
    from prisir_work.free_resource_fetcher import free_stock_video
    hits = free_stock_video("dog", limit=3)
    assert len(hits) >= 1, f"应返 ≥ 1 hit(archive.org fallback),实得 {len(hits)}"
    print(f"✓ #13 facade free_stock_video → {len(hits)} hits (Pixabay 无 key 时 archive.org fallback)")


if __name__ == "__main__":
    print("═══ OM-P4 tests(13) — 真调用免费资源 ═══")
    test_om_p4_1_edge_tts_available()
    test_om_p4_2_chinese_voices()
    test_om_p4_3_edge_tts_chinese_real()
    test_om_p4_4_edge_tts_english_real()
    test_om_p4_5_edge_tts_empty_text()
    test_om_p4_6_pixabay_no_key()
    test_om_p4_7_pixabay_probe_no_key()
    test_om_p4_8_pixabay_search_no_key()
    test_om_p4_9_archive_available()
    test_om_p4_10_archive_search_real()
    test_om_p4_11_facade_status()
    test_om_p4_12_facade_free_tts()
    test_om_p4_13_facade_free_stock_video()
    print("\n所有 13 组断言通过 ✅(含 4 次真 HTTP / WebSocket 调用)")