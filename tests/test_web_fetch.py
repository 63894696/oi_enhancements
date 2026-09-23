# -*- coding: utf-8 -*-
"""tests/test_web_fetch.py — P2.5+16 cache.py + web_fetch.py 测试。

覆盖(全部必绿,无 skip):
  · test_cache_roundtrip           — put + get + 过期
  · test_cache_concurrent_put       — 多线程并发 put 到同 url 不爆
  · test_fetch_uses_cache           — 二次 fetch,cached=True,只调一次
  · test_fetch_fallback_to_empty    — 全部 raise → 不抛,content="" + error="all_failed"
  · test_fetch_picks_fastest        — 三个 fetcher 不同 sleep → 最快的赢
  · test_url_normalize              — 带 fragment 的 url 缓存命中
  · test_a11y_local_only            — http url 跳 a11y
  · test_disk_cache_path            — Windows 上 = ~/AppData/Local/PrisirAI/cache/web
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 必须在 import 被测模块前 monkeypatch 缓存目录
import tempfile as _tempfile
_TMP_ROOT = Path(_tempfile.mkdtemp(prefix="prisIrai_test_cache_"))

# 用 prisir_work 子模块的 _CACHE_DIR_OVERRIDE
import prisir_work.cache as _cache_mod
_cache_mod._CACHE_DIR_OVERRIDE = _TMP_ROOT

# 避免 import web_fetch 时自动跑真实 http(只对本次 import)
import prisir_work.web_fetch as _wf_mod


def _reset_fetcher_registry() -> None:
    """清空 fetcher 列表(测试要插 mock)。"""
    _wf_mod._FETCHERS.clear()
    _wf_mod._MEM_CACHE.clear()


def _setup_cache_override() -> None:
    """保证每个 test 拿到的 cache_dir 都在 _TMP_ROOT。"""
    _cache_mod._CACHE_DIR_OVERRIDE = _TMP_ROOT


# ---------------------------------------------------------------------------
# cache.py 测试
# ---------------------------------------------------------------------------

def test_cache_roundtrip():
    """put + get → 命中;人为过期 → None。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    url = "https://example.com/roundtrip"
    payload = {"content": "hello world", "meta": {"status": 200}}
    _cache_mod.cache_put(url, payload, ttl_days=7)
    hit = _cache_mod.cache_get(url)
    assert hit is not None, "cache miss after put"
    assert hit["url"] == url
    assert hit["payload"] == payload
    assert hit["fetched_at"], "fetched_at missing"
    assert hit["expires_at"], "expires_at missing"

    # 手动改写 expires_at 为过去 → 应该 miss
    import json
    p = _cache_mod._key_path(url)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["expires_at"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(data), encoding="utf-8")
    miss = _cache_mod.cache_get(url)
    assert miss is None, f"过期不应命中,got {miss}"
    print("✓ cache put + get + 过期 命中预期")


def test_cache_concurrent_put():
    """多线程并发 put 到同 url,不能爆(半写 / 锁冲突)。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    url = "https://example.com/concurrent"
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            _cache_mod.cache_put(url, {"content": f"data-{i}", "meta": {"i": i}}, ttl_days=7)
        except Exception as e:  # pragma: no cover — 异常必须被吞,且函数本身不抛
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"并发 put 抛了: {errors}"
    hit = _cache_mod.cache_get(url)
    assert hit is not None
    # 内容必须是某个 worker 写的(i ∈ 0..7)
    assert hit["payload"]["meta"]["i"] in range(8)
    print("✓ 并发 put 8 线程无异常,落盘文件最终一致")


# ---------------------------------------------------------------------------
# web_fetch.py 测试
# ---------------------------------------------------------------------------

def test_fetch_uses_cache():
    """二次 fetch 同 url → 第二次 cached=True,真实 fetcher 只被调一次。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    _reset_fetcher_registry()

    call_count = {"n": 0}
    lock = threading.Lock()

    def slow_fetcher(url: str, options: dict) -> dict:
        with lock:
            call_count["n"] += 1
        time.sleep(0.05)
        return {"content": f"hello-{url}", "meta": {"ok": True, "fetcher": "mock"}}

    _wf_mod.register_fetcher("mock", slow_fetcher)

    r1 = _wf_mod.fetch("https://example.com/uses-cache", options={}, timeout=5.0)
    assert r1["content"].startswith("hello-"), r1
    assert r1["cached"] is False

    r2 = _wf_mod.fetch("https://example.com/uses-cache", options={}, timeout=5.0)
    assert r2["cached"] is True, f"第二次应命中缓存,got {r2}"
    assert r2["content"] == r1["content"]
    # 关键:fetcher 只能被调一次(第二次走缓存)
    assert call_count["n"] == 1, f"fetcher 被调 {call_count['n']} 次,应=1"
    print("✓ 二次 fetch → 缓存命中,fetcher 只跑一次")


def test_fetch_fallback_to_empty():
    """所有 fetcher 都 raise → fetch 不抛,返 content='' + meta.error='all_failed'。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    _reset_fetcher_registry()

    def boom1(url, options):
        raise RuntimeError("boom-1")

    def boom2(url, options):
        raise ValueError("boom-2")

    _wf_mod.register_fetcher("boom1", boom1)
    _wf_mod.register_fetcher("boom2", boom2)

    r = _wf_mod.fetch("https://example.com/all-fail", options={}, timeout=2.0)
    assert r["content"] == "", f"应空,got {r['content']!r}"
    assert r["meta"].get("error") == "all_failed", f"应 all_failed,got {r['meta']}"
    assert "cached" in r and r["cached"] is False
    print("✓ 全部 fetcher raise → 不抛,降级返 all_failed")


def test_fetch_picks_fastest():
    """三个 fetcher 不同 sleep → 最快的赢。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    _reset_fetcher_registry()

    def fast(url, options):
        time.sleep(0.05)
        return {"content": "FAST", "meta": {"ok": True}}

    def medium(url, options):
        time.sleep(0.5)
        return {"content": "MEDIUM", "meta": {"ok": True}}

    def slow(url, options):
        time.sleep(2.0)
        return {"content": "SLOW", "meta": {"ok": True}}

    _wf_mod.register_fetcher("fast", fast)
    _wf_mod.register_fetcher("medium", medium)
    _wf_mod.register_fetcher("slow", slow)

    t0 = time.monotonic()
    r = _wf_mod.fetch("https://example.com/race", options={}, timeout=1.0)
    dt = time.monotonic() - t0
    assert r["content"] == "FAST", f"fastest 应胜,got {r['content']!r}"
    assert dt < 0.4, f"应不阻塞等 slow,实际 {dt:.2f}s"
    print(f"✓ 三 fetcher 竞速,最快胜({dt:.2f}s)")


def test_url_normalize():
    """带 fragment 的 url 应该和去 fragment 后命中同一缓存。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    _reset_fetcher_registry()

    _wf_mod.register_fetcher("mock", lambda u, o: {"content": "frag-test", "meta": {"ok": True}})

    r1 = _wf_mod.fetch("https://example.com/page", options={}, timeout=2.0)
    r2 = _wf_mod.fetch("https://example.com/page#section-1", options={}, timeout=2.0)
    assert r1["content"] == "frag-test"
    assert r2["cached"] is True, f"去 fragment 后应命中缓存,got {r2}"
    assert r2["url"] == "https://example.com/page", f"规范化 url,got {r2['url']}"
    print("✓ fragment 规范化后缓存命中")


def test_a11y_local_only():
    """http url 跳过 a11y;file:// 才走 a11y。"""
    _setup_cache_override()
    _cache_mod.cache_clear()
    _reset_fetcher_registry()

    # 注入假 a11y_extract 模块
    class _FakeA11y:
        called_with: list[str] = []

        @staticmethod
        def extract(url: str):
            _FakeA11y.called_with.append(url)
            return {"text": f"a11y of {url}"}

    import types
    fake_mod = types.ModuleType("a11y_extract")
    fake_mod.extract = _FakeA11y.extract
    sys.modules["a11y_extract"] = fake_mod

    # 情况 1:http url → a11y 不该被调用
    _wf_mod.register_fetcher("http_urllib", lambda u, o: {
        "content": "http-body", "meta": {"ok": True, "fetcher": "http_urllib"}})
    r1 = _wf_mod.fetch("https://example.com/skip-a11y", options={}, timeout=2.0)
    assert r1["content"] == "http-body"
    assert r1["fetcher"] == "http_urllib"
    assert not _FakeA11y.called_with, f"a11y 不该被 http url 触发,got {_FakeA11y.called_with}"

    # 情况 2:file url → a11y 该被调(注册 a11y,确保它是注册路径之一被选)
    _cache_mod.cache_clear()
    _reset_fetcher_registry()
    def file_fetcher(u, o):
        return {"content": f"file of {u}", "meta": {"ok": True, "fetcher": "file"}}
    _wf_mod.register_fetcher("file_only", file_fetcher)
    r2 = _wf_mod.fetch("file:///C:/test.txt", options={}, timeout=2.0)
    assert r2["content"] == "file of file:///C:/test.txt"

    # 直接验证 a11y_provider 函数对 http 返 a11y_unavailable
    r3 = _wf_mod.a11y_provider("https://example.com", {})
    assert r3["meta"].get("error") == "a11y_unavailable", r3["meta"]
    print("✓ http url 跳 a11y;file url 走 file; a11y_provider http 返 unavailable")


def test_disk_cache_path():
    """Windows 上 cache_dir() = ~/AppData/Local/PrisirAI/cache/web(可 monkeypatch HOME)。"""
    # 用临时 HOME 模拟
    fake_home = _tempfile.mkdtemp(prefix="prisIrai_fake_home_")
    if sys.platform.startswith("win"):
        monkey_env = {"LOCALAPPDATA": str(Path(fake_home) / "AppData" / "Local")}
        saved = {}
        for k, v in monkey_env.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            d = _cache_mod.cache_dir()
            # 清除 override 才能走真实路径逻辑
            _cache_mod._CACHE_DIR_OVERRIDE = None
            d2 = _cache_mod.cache_dir()
            assert d2 == Path(monkey_env["LOCALAPPDATA"]) / "PrisirAI" / "cache" / "web", \
                f"Windows cache_dir 应 = %LOCALAPPDATA%/PrisirAI/cache/web,got {d2}"
            # 再恢复 override 给后续 test
            _cache_mod._CACHE_DIR_OVERRIDE = _TMP_ROOT
            print(f"✓ Windows cache_dir = {d2}")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    else:
        # linux/mac 兜底:至少路径含 PrisirAI/cache/web
        _cache_mod._CACHE_DIR_OVERRIDE = None
        d = _cache_mod.cache_dir()
        assert "PrisirAI" in str(d) and "cache" in str(d) and d.name == "web", \
            f"非 Windows 也该落到 PrisirAI/cache/web,got {d}"
        _cache_mod._CACHE_DIR_OVERRIDE = _TMP_ROOT
        print(f"✓ 非 Windows cache_dir = {d}")


# ---------------------------------------------------------------------------
# main — 直接 python tests/test_web_fetch.py 也跑得起来
# ---------------------------------------------------------------------------

def main() -> int:
    import pytest as _pytest
    rc = _pytest.main([__file__, "-v"])
    return rc


if __name__ == "__main__":
    sys.exit(main())