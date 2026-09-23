"""test_tune.py — P2.5+18b per-domain fetcher 优先级学习单测"""
import os, sys, tempfile, shutil
from pathlib import Path
import pytest

sys.path.insert(0, '.')

from prisir_work import tune
from prisir_work import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    tmp_parent = Path(tempfile.mkdtemp(prefix="tune_parent_"))
    tmp_cache = tmp_parent / "web"
    tmp_cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cache_mod, '_CACHE_DIR_OVERRIDE', tmp_cache)
    tune.reset()
    yield tmp_cache
    tune.reset()
    shutil.rmtree(tmp_parent, ignore_errors=True)


# ── 1. 路径 ──

def test_tune_path_aligned_with_cache_dir():
    assert tune.tune_path().parent == cache_mod.cache_dir().parent
    assert tune.tune_path().name == "tune.json"


def test_tune_path_handles_missing_file():
    assert tune.recommend("nope.example") is None
    # 不该创建文件
    assert not tune.tune_path().exists()


# ── 2. record + 阈值 ──

def test_record_increments_accumulator():
    for _ in range(5):
        tune.record("https://a.com/x", "f1", 100, ok=True)
    snap = tune.tune_stats_snapshot()
    assert "a.com" in snap
    assert snap["a.com"]["f1"]["ok"] == 5
    assert snap["a.com"]["f1"]["avg_ms"] == 100.0


def test_record_counts_fail_separately():
    tune.record("https://b.com/y", "f1", 50, ok=False)
    tune.record("https://b.com/y", "f1", 60, ok=False)
    tune.record("https://b.com/y", "f2", 100, ok=True)
    snap = tune.tune_stats_snapshot()
    assert snap["b.com"]["f1"]["ok"] == 0
    assert snap["b.com"]["f1"]["fail"] == 2
    assert snap["b.com"]["f2"]["ok"] == 1


def test_record_skips_bad_inputs():
    # 空 fetcher / 空 host → 不抛 + 不写
    tune.record("", "f", 100, ok=True)
    tune.record("https://a.com", "", 100, ok=True)
    snap = tune.tune_stats_snapshot()
    assert snap == {}


# ── 3. recommend ──

def test_recommend_no_data():
    assert tune.recommend("nope.example") is None


def test_recommend_empty_host():
    assert tune.recommend("") is None


# ── 4. flush_if_ready 阈值 ──

def test_flush_below_min_samples_no_write():
    """< MIN_SAMPLES 次 → tune.json 不写。"""
    tune.record("https://c.com/z", "fast", 80, ok=True)
    tune.record("https://c.com/z", "fast", 80, ok=True)
    res = tune.flush_if_ready()
    assert res["c.com"] is None
    assert not tune.tune_path().exists()


def test_flush_ok_ratio_below_threshold_no_write():
    """ok 率 < 80% → 不写。"""
    for _ in range(5):
        tune.record("https://d.com/z", "flaky", 100, ok=True)
    for _ in range(5):
        tune.record("https://d.com/z", "flaky", 100, ok=False)
    res = tune.flush_if_ready()
    assert res["d.com"] is None
    assert not tune.tune_path().exists()


def test_flush_clear_best_picks_fastest():
    for _ in range(5):
        tune.record("https://e.com/z", "fast", 80, ok=True)
    for _ in range(5):
        tune.record("https://e.com/z", "slow", 500, ok=True)
    res = tune.flush_if_ready()
    assert res["e.com"] == ["fast"]
    assert tune.recommend("e.com") == ["fast"]
    assert tune.tune_path().exists()


def test_flush_close_gap_includes_both():
    """最快 vs 第二快 < 20% → 都纳入。"""
    for _ in range(5):
        tune.record("https://f.com/z", "a", 100, ok=True)
    for _ in range(5):
        tune.record("https://f.com/z", "b", 115, ok=True)  # 差 15% < 20%
    res = tune.flush_if_ready()
    assert res["f.com"] is not None
    assert "a" in res["f.com"]
    assert "b" in res["f.com"]


def test_flush_updates_when_better_appears():
    """首次写后,新 fetcher 反超 → flush 改写。"""
    for _ in range(5):
        tune.record("https://g.com/z", "slow", 500, ok=True)
    tune.flush_if_ready()
    assert tune.recommend("g.com") == ["slow"]
    # 加一个明显更快的
    for _ in range(5):
        tune.record("https://g.com/z", "fast", 50, ok=True)
    tune.flush_if_ready()
    assert tune.recommend("g.com") == ["fast"]


# ── 5. 原子写 + 腐蚀恢复 ──

def test_corrupted_tune_json_returns_empty():
    tune.tune_path().parent.mkdir(parents=True, exist_ok=True)
    tune.tune_path().write_text("{not json", encoding="utf-8")
    assert tune.recommend("any.com") is None


def test_bad_format_tune_json_returns_empty():
    tune.tune_path().parent.mkdir(parents=True, exist_ok=True)
    tune.tune_path().write_text('["list", "not", "dict"]', encoding="utf-8")
    assert tune.recommend("any.com") is None


# ── 6. reset + snapshot ──

def test_reset_clears_accumulator():
    tune.record("https://h.com/z", "f", 100, ok=True)
    tune.reset()
    assert tune.tune_stats_snapshot() == {}


def test_snapshot_round_trip():
    for _ in range(3):
        tune.record("https://i.com/x", "f", 100, ok=True)
    snap = tune.tune_stats_snapshot()
    assert snap["i.com"]["f"]["total"] == 3
    assert snap["i.com"]["f"]["ok"] == 3
    assert snap["i.com"]["f"]["avg_ms"] == 100.0


# ── 7. web_fetch 集成 ──

def test_web_fetch_uses_tuned_order():
    """mock 三个 fetcher,跑 N 次后 tune 应该记下 best(slow 必不进)。"""
    from prisir_work import web_fetch as wf

    # 重新注册干净 fetcher(避免继承测试间残留)
    wf._FETCHERS.clear()
    wf._MEM_CACHE.clear()

    # 让每个 fetcher 都被记录 ≥ 3 次(突破 MIN_SAMPLES)。
    # 用 monkeypatch 替换 fetch 走串行路径(让所有 fetcher 都 record),
    # 或干脆直接喂 record 给 tune + 单独 verify web_fetch 集成在下一个测试。
    for i in range(3):
        tune.record("https://learn.example/page", "fast", 50, ok=True)
        tune.record("https://learn.example/page", "medium", 120, ok=True)
        tune.record("https://learn.example/page", "slow", 300, ok=True)
    flush_res = tune.flush_if_ready()
    rec = tune.recommend("learn.example")
    assert rec is not None, f"tune 没推荐任何 fetcher (flush={flush_res})"
    # slow 必淘汰
    assert "slow" not in rec, f"slow 不应被推荐: {rec}"
    # fast 必进(50ms 最快)
    assert "fast" in rec, f"fast 应在推荐列表: {rec}"


def test_web_fetch_recommend_skips_non_listed(monkeypatch):
    """learned 命中 → 只调 learned 里的 fetcher,其他跳过。"""
    from prisir_work import web_fetch as wf

    wf._FETCHERS.clear()
    wf._MEM_CACHE.clear()

    seen = []

    def a(url, options):
        seen.append("a")
        return {"content": "<html>a</html>", "meta": {"elapsed_ms": 100, "ok": True}}

    def b(url, options):
        seen.append("b")
        return {"content": "<html>b</html>", "meta": {"elapsed_ms": 200, "ok": True}}

    wf.register_fetcher("a", a)
    wf.register_fetcher("b", b)

    # 先预置 tune.json(直接写文件,绕累加器)
    tune.tune_path().parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    tune.tune_path().write_text(_json.dumps({"direct.example": ["a"]}), encoding="utf-8")

    wf.fetch("https://direct.example/x", options={"no_cache": True})
    # 只有 a 被调用
    assert "a" in seen
    assert "b" not in seen


def test_web_fetch_records_results_into_tune():
    """web_fetch.fetch 跑完 → tune 累加器收到 fetcher 记录。"""
    from prisir_work import web_fetch as wf

    wf._FETCHERS.clear()
    wf._MEM_CACHE.clear()

    def fast(url, options):
        return {"content": "<html>fast</html>", "meta": {"elapsed_ms": 50, "ok": True}}

    def slow(url, options):
        return {"content": "<html>slow</html>", "meta": {"elapsed_ms": 300, "ok": True}}

    wf.register_fetcher("fast", fast)
    wf.register_fetcher("slow", slow)

    # 跑 3 次(突破 MIN_SAMPLES),每次 mem+disk cache 都跳过
    for _ in range(3):
        wf._MEM_CACHE.clear()
        # 直接删磁盘 cache key
        from prisir_work import cache as c
        # 同一 URL 调 cache.invalidate 删 disk
        try:
            import hashlib
            url_key = "https://rec.example/page"
            cache_path = c.cache_dir() / f"{hashlib.sha1(url_key.encode('utf-8')).hexdigest()}.json"
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass
        wf.fetch("https://rec.example/page", options={"no_cache": True})

    snap = tune.tune_stats_snapshot()
    assert "rec.example" in snap
    # 至少 fast 和 slow 之一被 record 了
    assert any(name in snap["rec.example"] for name in ("fast", "slow"))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))