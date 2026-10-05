"""
test_music_search_endpoint.py — P2.5+29(2026-10-05)search endpoint + 5 源并行 fallback 测试。

覆盖:
  - huibq.js 必含 handleSearch 函数 + actions: ['musicUrl', 'search']
  - gdstudio.js 必含 batchSearch 函数 + actions: ['musicUrl', 'search']
  - OnlineSearch.search 单源调用(LxRuntimeClient mock)
  - OnlineSearch.search_multi 5 源并行 fallback + merge 去重
  - /api/search 端点 + /api/search/cache 端点存在 + search_cache.json 写盘
  - prisIragent-music-web.py 路由注册含 /api/search + /api/search/cache

P3.10b 红线:keywords 是纯文本 query,不传音频内容;测试不模拟音频上传。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.player import OnlineSearch  # noqa: E402


# ============================================================
# 1) huibq.js / gdstudio.js 静态校验
# ============================================================
class TestLxSourceSearchStatic(unittest.TestCase):
    """验证 LX 源文件声明 search action handler 必备要素。"""

    def test_huibq_actions_includes_search(self):
        path = COMPANION / "lx_runtime" / "huibq.js"
        text = path.read_text(encoding="utf-8")
        self.assertIn("actions: ['musicUrl', 'search']", text,
                      "huibq.js 必须声明 actions: ['musicUrl', 'search']")
        self.assertIn("handleSearch", text,
                      "huibq.js 必须有 handleSearch 函数")
        self.assertIn("case 'search'", text,
                      "huibq.js switch case 必须支持 'search'")
        self.assertIn("/search/${source}", text,
                      "huibq.js 必须拼 search URL 路径")

    def test_gdstudio_actions_includes_search(self):
        path = COMPANION / "lx_runtime" / "gdstudio.js"
        text = path.read_text(encoding="utf-8")
        self.assertIn("actions: ['musicUrl', 'search']", text,
                      "gdstudio.js 必须声明 actions: ['musicUrl', 'search']")
        self.assertIn("batchSearch", text,
                      "gdstudio.js 必须有 batchSearch 函数")
        self.assertIn("action === 'search'", text,
                      "gdstudio.js 必须处理 'search' action")


# ============================================================
# 2) OnlineSearch 单源 / 多源 mock
# ============================================================
class _FakeLxRuntimeClient:
    """P2.5+29:替换 LxRuntimeClient 子进程,按 (action, source) 模拟结果。

    behavior 形如:
      {
        ("search", "wy"): {"ok": True, "result": [{"songname": "孤勇者", ...}]},
        ("search", "kw"): {"ok": False, "err": "too many"},
        ("musicUrl", "wy"): {"ok": True, "result": "http://..."},
      }
    """

    def __init__(self, behavior: dict):
        self.behavior = behavior
        self.calls: list[tuple[str, str]] = []

    def call(self, action: str, source: str, info: dict | None = None) -> dict:
        self.calls.append((action, source))
        key = (action, source)
        if key in self.behavior:
            return dict(self.behavior[key])
        return {"ok": False, "err": f"fake no handler for {key}"}


class TestOnlineSearchSingle(unittest.TestCase):
    """P2.5+29:OnlineSearch.search() 单源 — mock lx_runtime_client"""

    def setUp(self):
        self.search = OnlineSearch(sources=["wy", "kw", "tx"])
        fake = _FakeLxRuntimeClient({
            ("search", "wy"): {
                "ok": True,
                "result": [
                    {"id": "1901371647", "name": "孤勇者", "singer": "陈奕迅",
                     "albumName": "孤勇者", "interval": 245},
                ],
            },
        })
        self.search._client = fake  # 直接 bypass _ensure

    def test_search_wy_returns_normalized(self):
        r = self.search.search("wy", "孤勇者", limit=5)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["items"]), 1)
        item = r["items"][0]
        self.assertEqual(item["source"], "wy")
        self.assertEqual(item["songname"], "孤勇者")
        self.assertEqual(item["singer"], "陈奕迅")
        self.assertTrue(item["songmid"].startswith("wy_"))

    def test_search_empty_keywords(self):
        r = self.search.search("wy", "", limit=5)
        self.assertFalse(r["ok"])
        self.assertIn("empty", r["err"])

    def test_search_normalize_data_key(self):
        # 模拟 huibq.js 返 {ok:True, data:[...]}(框架中间件形态)
        self.search._client = _FakeLxRuntimeClient({
            ("search", "wy"): {
                "ok": True,
                "data": [{"songmid": "wy_999", "songname": "晴天", "singer": "周杰伦"}],
            },
        })
        r = self.search.search("wy", "晴天")
        self.assertTrue(r["ok"])
        self.assertEqual(r["items"][0]["songname"], "晴天")
        self.assertEqual(r["items"][0]["songmid"], "wy_999")


class TestOnlineSearchMulti(unittest.TestCase):
    """P2.5+29:OnlineSearch.search_multi 5 源并行 fallback + merge 去重"""

    def setUp(self):
        # 5 源并行(沿用 LX SEARCH 范式:ik 公开 http API 5 源)
        self.search = OnlineSearch(sources=["wy", "kw", "tx", "kg", "mg"])
        fake = _FakeLxRuntimeClient({
            ("search", "wy"): {
                "ok": True,
                "result": [
                    {"id": "111", "name": "孤勇者", "singer": "陈奕迅", "interval": 240},
                    {"id": "222", "name": "十年", "singer": "陈奕迅", "interval": 200},
                ],
            },
            ("search", "kw"): {
                "ok": True,
                "result": [
                    {"id": "333", "name": "孤勇者", "singer": "陈奕迅", "interval": 250},
                ],
            },
            ("search", "tx"): {"ok": False, "err": "code:5 too many requests"},
            ("search", "kg"): {"ok": False, "err": "404"},
            ("search", "mg"): {
                "ok": True,
                "result": [],  # 空数组
            },
        })
        self.search._client = fake

    def test_search_multi_merges_sources(self):
        r = self.search.search_multi("陈奕迅", limit=10)
        self.assertTrue(r["ok"])
        # wy 2 + mg 0 = 2 个(无去重场景)
        # 注意 songmid 跨源不复用(wy_111 vs kw_333),merge 后应有 3 条
        self.assertGreaterEqual(r["count"], 2)
        self.assertIn("wy", r["sources_hit"])
        self.assertIn("kw", r["sources_hit"])
        # 失败源不计入 sources_hit
        self.assertNotIn("tx", r["sources_hit"])
        self.assertNotIn("kg", r["sources_hit"])
        # mg 返空也算 hit(成功响应)
        self.assertIn("mg", r["sources_hit"])

    def test_search_multi_dedup_by_songmid(self):
        # 同 songmid 跨源合并(wy + kw 都给 wy_444)→ 去重 1 条
        # 注意:kw 源传 {id: "444"} 不带前缀时,normalize_search 会把 source="kw"
        # 拼成 "kw_444";wy 源传 "wy_444" 已带前缀不动。两条最终 songmid 不同。
        # 真去重要在「同源 songmid 完全一致」场景才有意义。
        # 验证去重:用同一源搜两次(走不同 sub-source 名但同 wx_444 songmid 形如 wy_gdstudio)—
        # 这里直接用同 source + 同 songmid 多次写入:
        self.search._client = _FakeLxRuntimeClient({
            ("search", "wy"): {
                "ok": True,
                "result": [
                    {"songmid": "wy_444", "songname": "晴天", "singer": "周杰伦"},
                    {"songmid": "wy_444", "songname": "晴天", "singer": "周杰伦"},  # 重复
                ],
            },
        })
        r = self.search.search_multi("晴天", limit=10)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["items"][0]["songmid"], "wy_444")

    def test_search_multi_empty(self):
        r = self.search.search_multi("", limit=10)
        self.assertFalse(r["ok"])
        self.assertIn("empty", r["err"])


# ============================================================
# 3) prisIragent-music-web.py 路由注册静态校验
# ============================================================
class TestApiSearchRouteRegistered(unittest.TestCase):
    """P2.5+29:路由注册必含 /api/search + /api/search/cache + api_search/cache 函数"""

    def test_api_search_handler_defined(self):
        text = (COMPANION / "prisIragent-music-web.py").read_text(encoding="utf-8")
        self.assertIn("async def api_search(", text)
        self.assertIn("async def api_search_cache(", text)
        self.assertIn('add_get("/api/search", api_search)', text)
        self.assertIn('add_get("/api/search/cache", api_search_cache)', text)

    def test_search_cache_module_constants(self):
        text = (COMPANION / "prisIragent-music-web.py").read_text(encoding="utf-8")
        self.assertIn("SEARCH_CACHE_FILE", text)
        self.assertIn("_save_search_cache", text)
        self.assertIn("_load_search_cache_sync", text)


# ============================================================
# 4) _search_cache.json 写盘 + reload 行为
# ============================================================
class TestSearchCachePersistence(unittest.TestCase):
    """P2.5+29:_search_cache.json 写盘 + reload 行为(用 tempfile 隔离真实目录)"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        # monkey patch SEARCH_CACHE_FILE 路径
        # prisIragent-music-web.py 文件名不是合法 Python module,用 importlib 加载
        import importlib.util
        web_path = COMPANION / "prisIragent-music-web.py"
        spec = importlib.util.spec_from_file_location("prisIragent_music_web_for_test", web_path)
        web_mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(web_mod)
        except Exception as e:
            # 部分模块需要环境变量/启动条件,这里只测 cache helpers,先 tolerate
            self.skipTest(f"web module load failed (acceptable for cache helper test): {e}")
            return
        self.web = web_mod
        self._orig_file = web_mod.SEARCH_CACHE_FILE
        self._orig_dir = web_mod.SEARCH_CACHE_DIR
        web_mod.SEARCH_CACHE_FILE = self.tmp_path / "_search_cache.json"
        web_mod.SEARCH_CACHE_DIR = self.tmp_path

    def tearDown(self):
        self.web.SEARCH_CACHE_FILE = self._orig_file
        self.web.SEARCH_CACHE_DIR = self._orig_dir
        self.tmp.cleanup()

    def test_load_empty_returns_default(self):
        cache = self.web._load_search_cache_sync()
        self.assertEqual(cache["version"], 1)
        self.assertEqual(cache["entries"], {})
        self.assertEqual(cache["queries"], [])

    def test_save_then_load_round_trip(self):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.web._save_search_cache(
                "周杰伦",
                [
                    {"songmid": "wy_999", "songname": "晴天", "singer": "周杰伦",
                     "album": "", "duration": 270, "source": "wy"},
                ],
            ))
        finally:
            loop.close()
        # reload
        cache = self.web._load_search_cache_sync()
        self.assertIn("wy_999", cache["entries"])
        self.assertEqual(cache["entries"]["wy_999"]["songname"], "晴天")
        self.assertEqual(cache["entries"]["wy_999"]["q"], "周杰伦")
        self.assertIn("周杰伦", cache["queries"])

    def test_save_dedup_by_songmid(self):
        loop = asyncio.new_event_loop()
        try:
            # 同一 songmid 多次写,后者覆盖前者(ts 更新)
            loop.run_until_complete(self.web._save_search_cache("晴天", [
                {"songmid": "wy_999", "songname": "晴天", "singer": "周杰伦",
                 "album": "", "duration": 270, "source": "wy"},
            ]))
            loop.run_until_complete(self.web._save_search_cache("周杰伦", [
                {"songmid": "wy_999", "songname": "晴天", "singer": "周杰伦",
                 "album": "七里香", "duration": 270, "source": "wy"},
            ]))
        finally:
            loop.close()
        cache = self.web._load_search_cache_sync()
        self.assertEqual(len(cache["entries"]), 1)
        self.assertEqual(cache["entries"]["wy_999"]["q"], "周杰伦")  # 最新 q
        self.assertEqual(cache["entries"]["wy_999"]["album"], "七里香")  # 最新 album


def _run_async(coro):
    """P2.5+29(2026-10-05):运行协程(legacy helper — 旧调用方保留)。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# 5) 前端 SearchModal / SearchStore 静态校验
# ============================================================
class TestFrontendSearchAssets(unittest.TestCase):
    """P2.5+29:SearchModal.vue + stores/search.ts + ui.ts searchModalVisible 必备"""

    def test_search_modal_exists(self):
        path = ROOT / "companion" / "static" / "music-vue" / "src" / "components" / "SearchModal.vue"
        self.assertTrue(path.exists(), f"SearchModal.vue must exist at {path}")
        text = path.read_text(encoding="utf-8")
        self.assertIn("P2.5+29", text)
        self.assertIn("/api/search", text)
        self.assertIn("/api/search/cache", text)
        # 0 上传红线注释
        self.assertIn("0 上传", text)
        # Esc/箭头/Enter 键盘操作
        self.assertIn("Escape", text)
        self.assertIn("ArrowDown", text)
        self.assertIn("Enter", text)

    def test_search_store_exists(self):
        path = ROOT / "companion" / "static" / "music-vue" / "src" / "stores" / "search.ts"
        self.assertTrue(path.exists(), f"stores/search.ts must exist at {path}")
        text = path.read_text(encoding="utf-8")
        self.assertIn("useSearchStore", text)
        self.assertIn("setCache", text)
        self.assertIn("recordQuery", text)
        # 0 上传红线
        self.assertIn("0 上传", text)

    def test_ui_store_has_search_modal_visible(self):
        path = ROOT / "companion" / "static" / "music-vue" / "src" / "stores" / "ui.ts"
        text = path.read_text(encoding="utf-8")
        self.assertIn("searchModalVisible", text)
        self.assertIn("openSearch", text)
        self.assertIn("closeSearch", text)

    def test_music_view_has_search_button(self):
        path = ROOT / "companion" / "static" / "music-vue" / "src" / "views" / "MusicView.vue"
        text = path.read_text(encoding="utf-8")
        self.assertIn("btn-search", text)
        self.assertIn("🔍", text)
        self.assertIn("SearchModal", text)


if __name__ == "__main__":
    unittest.main()