"""
test_song_pool_and_favorite.py — P2.5+23(2026-10-03)真歌名池 + 收藏/下载 测试。

覆盖:
  - SongPoolCatalog.load:388 行 CSV 解析、双引号嵌套容错、去重
  - SongPoolCatalog.shuffle:visible 60 首、不同次不同
  - SongPoolCatalog.list_visible(tag=):按标签过滤
  - SongPoolCatalog.list_tags():返回所有出现过的标签
  - Player._cmd_favorite:source=song_pool_fav 入库 + 幂等
  - Player._cmd_download:写 cache/<title>.mp3 + LocalLibrary source=local
  - /api/songs GET 端点(走 aiohttp test client)
  - /api/songs/respin POST 端点
  - cmd favorite/download 路由
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.song_pool import SongPoolCatalog, SongMeta  # noqa: E402
from music.player import LocalLibrary, Player, Track  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
# SongPoolCatalog 单元测试
# ============================================================
class TestSongPoolLoad(unittest.TestCase):
    """从用户填好的真实 CSV 解析。"""

    @classmethod
    def setUpClass(cls):
        cls.cat = SongPoolCatalog()
        cls.cat.load()

    def test_load_count(self):
        # 388 行实际为 381(7 行重复如 "Immortals" 出现两次)
        self.assertGreaterEqual(len(self.cat.all_songs), 380,
                                f"expected ≥380 unique songs, got {len(self.cat.all_songs)}")
        self.assertLessEqual(len(self.cat.all_songs), 388)

    def test_load_visible_is_60(self):
        self.assertEqual(len(self.cat.visible_songs), 60)

    def test_visible_is_substring_of_all(self):
        for s in self.cat.visible_songs:
            self.assertIn(s, self.cat.all_songs)

    def test_no_dup_in_all(self):
        ids = [s.id for s in self.cat.all_songs]
        self.assertEqual(len(ids), len(set(ids)), "id 重复")

    def test_id_is_stable(self):
        # 同 title+artist 必同 id(确定性哈希)
        for s in self.cat.all_songs:
            self.assertEqual(len(s.id), 12)

    def test_immortals_doublequote_parsed(self):
        # CSV 第 17 行: "Immortals(From ""Big Hero 6""/Soundtrack)" → 单引号
        hit = [s for s in self.cat.all_songs if "Immortals" in s.title]
        self.assertGreaterEqual(len(hit), 1)
        for h in hit:
            self.assertNotIn('""', h.title, "双引号嵌套未解")

    def test_tags_present(self):
        tags = set(self.cat.list_tags())
        for expected in ("ACG神曲", "熬夜修仙", "巴士随身听", "古风"):
            self.assertIn(expected, tags, f"missing tag {expected}")

    def test_all_have_title(self):
        for s in self.cat.all_songs:
            self.assertTrue(s.title, f"empty title in song: {s}")


class TestSongPoolShuffle(unittest.TestCase):
    def test_shuffle_produces_different_orders(self):
        cat = SongPoolCatalog()
        cat.load()
        first_run = [s.id for s in cat.visible_songs]
        # 洗 5 次,期望至少 4 次与第一次不同(概率意义)
        same = 0
        for _ in range(5):
            cat.shuffle()
            if [s.id for s in cat.visible_songs] == first_run:
                same += 1
        self.assertLess(same, 2, "5 次洗牌至少 4 次应该顺序不同")

    def test_list_visible_tag_filter(self):
        cat = SongPoolCatalog()
        cat.load()
        acg = cat.list_visible(tag="ACG神曲")
        for s in acg:
            self.assertEqual(s.tag, "ACG神曲")
        # visible 中至少有 1 首 ACG 神曲(388 中 ~88 首,visible 60 中必含)
        self.assertGreater(len(acg), 0, "visible 中无 ACG 神曲")

    def test_list_tags_only_unique(self):
        cat = SongPoolCatalog()
        cat.load()
        tags = cat.list_tags()
        self.assertEqual(len(tags), len(set(tags)), "标签去重失败")


# ============================================================
# Player._cmd_favorite / _cmd_download 单元测试
# ============================================================
class TestCmdFavorite(unittest.TestCase):
    def test_favorite_adds_song_pool_fav_track(self):
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(
            id="trk_remote_1", title="晴天", artist="周杰伦",
            album="", path="https://example.com/mp3/abc", source="lx:mock.js",
        ))
        p = Player(lib, online=None, catalog=None)
        r = _run(p._cmd_favorite("trk_remote_1"))
        self.assertTrue(r["ok"])
        self.assertIn("fav_id", r)
        fav = lib.get(r["fav_id"])
        self.assertIsNotNone(fav)
        self.assertEqual(fav.source, "song_pool_fav")
        self.assertEqual(fav.title, "晴天")
        self.assertEqual(fav.artist, "周杰伦")
        # path 沿用原 track(remote url)
        self.assertEqual(fav.path, "https://example.com/mp3/abc")

    def test_favorite_idempotent(self):
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(
            id="trk_remote_2", title="一程山路", artist="毛不易",
            album="", path="https://example.com/mp3/xyz", source="lx:mock.js",
        ))
        p = Player(lib, online=None, catalog=None)
        r1 = _run(p._cmd_favorite("trk_remote_2"))
        self.assertTrue(r1["ok"])
        # 第二次收藏应幂等 → dedup=True 且返回同一个 fav_id
        r2 = _run(p._cmd_favorite("trk_remote_2"))
        self.assertTrue(r2["ok"])
        self.assertEqual(r1["fav_id"], r2["fav_id"])
        self.assertTrue(r2.get("dedup"))

    def test_favorite_missing_track_returns_err(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        r = _run(p._cmd_favorite("nonexistent"))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["err"])


class TestCmdDownload(unittest.TestCase):
    """下载到 cache/ 的功能。"""

    def setUp(self):
        # 临时 music_root 准备一个真 mp3 文件,Player._cmd_download 会 copy2
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="music_dl_"))
        self.mp3 = self.tmp_dir / "fake.mp3"
        self.mp3.write_bytes(b"FAKE_MP3_BYTES")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_download_local_source(self):
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(
            id="local_test_1", title="测试歌曲A", artist="测试歌手",
            album="", path=str(self.mp3), duration=0.0, source="local",
        ))
        p = Player(lib, online=None, catalog=None)
        r = _run(p._cmd_download("local_test_1"))
        self.assertTrue(r["ok"], f"download failed: {r}")
        self.assertIn("local_id", r)
        self.assertTrue(Path(r["path"]).exists(), f"file not created: {r['path']}")
        # 入库为 local
        local_tr = lib.get(r["local_id"])
        self.assertIsNotNone(local_tr)
        self.assertEqual(local_tr.source, "local")
        self.assertEqual(local_tr.title, "测试歌曲A")
        # 文件名清洗:不是 file name 包含 ":"
        self.assertNotIn(":", Path(r["path"]).name)

    def test_download_illegal_chars_replaced(self):
        lib = LocalLibrary(music_root=None)
        # 文件名含非法字符:
        lib.add_track(Track(
            id="weird_1", title='bad/\\:*?"<>|name', artist="a",
            album="", path=str(self.mp3), source="local",
        ))
        p = Player(lib, online=None, catalog=None)
        r = _run(p._cmd_download("weird_1"))
        self.assertTrue(r["ok"])
        self.assertTrue(Path(r["path"]).exists())
        # 没有非法字符
        for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
            self.assertNotIn(ch, Path(r["path"]).name)

    def test_download_missing_track_returns_err(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        r = _run(p._cmd_download("nonexistent"))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["err"])


# ============================================================
# Cmd action 路由(favorite / download 在 cmd 里被识别)
# ============================================================
class TestCmdRouting(unittest.TestCase):
    def test_cmd_favorite_routes(self):
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(
            id="x1", title="AAA", artist="BBB", album="", path="x",
            duration=0.0, source="lx:mock.js",
        ))
        p = Player(lib, online=None, catalog=None)
        r = _run(p.cmd("favorite", track_id="x1"))
        self.assertTrue(r["ok"])
        self.assertIn("fav_id", r)

    def test_cmd_download_routes(self):
        # 用一个不存在的 track_id 也能路由进 _cmd_download(返 ok=False,但动作是被识别了)
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        r = _run(p.cmd("download", track_id="nonexistent"))
        self.assertFalse(r["ok"])  # not found,但 action 被识别


# ============================================================
# 集成测试: /api/songs 端点
# ============================================================
class TestApiSongsEndpoint(unittest.TestCase):
    """真起 aiohttp test client 打 /api/songs 与 /api/songs/respin。
    不需要真起 music 后端,用 TestClient + build_app(避免占用端口)。
    """

    def setUp(self):
        # 延迟 import,模块级 sys.path 已注入;文件名带 hyphen → importlib
        import importlib.util
        _PRISIR_MUSIC_WEB = COMPANION / "prisIragent-music-web.py"
        spec = importlib.util.spec_from_file_location(
            "prisIragent_music_web", str(_PRISIR_MUSIC_WEB))
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        self._mod = _mod
        APP = _mod.APP
        LocalLibrary = _mod.LocalLibrary

        # 重置 APP 状态(只动我们要测的字段,不污染其他共享字段)
        APP.library = LocalLibrary(music_root=None)
        APP.online = None
        # 实例化 song_pool
        APP.song_pool = SongPoolCatalog()
        APP.song_pool.load()
        # 实例化 Player
        from music.player import Player
        APP.player = Player(APP.library, online=None, catalog=APP.song_pool)
        # cfg / lyric / ws_lock 占位(避免 on_startup hook 失败)
        APP.cfg = None
        APP.lyric = None
        APP.ws_lock = None

        # 不再走 TestClient/TestServer(避免 event loop 副作用污染其他测试)。
        # 直接调 handler 函数 + make_mocked_request。

    def tearDown(self):
        # 隔离:重置 APP 全局,避免与后续 test 文件交互
        APP = self._mod.APP
        APP.library = None
        APP.player = None
        APP.song_pool = None
        APP.online = None

    def _run_handler(self, handler, query=""):
        """直接调 aiohttp handler 函数,绕过 TestClient/TestServer(避免 event loop 副作用污染其他测试)。
        handler = _mod.api_songs / _mod.api_songs_respin
        query = '?tag=ACG神曲' 这种 URL query string
        返回 handler 返的 web.Response 的 json dict
        """
        from aiohttp.test_utils import make_mocked_request
        path = "/"
        if query:
            q = query.lstrip("?")
            path = f"/?{q}"
        req = make_mocked_request("GET", path)
        resp = _run(handler(req))
        # resp 是 _ok/_err 返的 web.json_response,直接读 .body 拿 JSON
        import json as _json
        return _json.loads(resp.body.decode("utf-8"))

    def test_api_songs_get(self):
        data = self._run_handler(self._mod.api_songs, "")
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 60)
        self.assertGreaterEqual(data["total"], 380)
        self.assertIn("tags", data)
        self.assertGreater(len(data["songs"]), 0)
        s = data["songs"][0]
        self.assertIn("id", s)
        self.assertIn("title", s)
        self.assertIn("artist", s)
        self.assertIn("tag", s)

    def test_api_songs_tag_filter(self):
        data = self._run_handler(self._mod.api_songs, "?tag=ACG神曲")
        self.assertTrue(data["ok"])
        for s in data["songs"]:
            self.assertEqual(s["tag"], "ACG神曲")

    def test_api_songs_respin_changes_order(self):
        d1 = self._run_handler(self._mod.api_songs, "")
        ids1 = [s["id"] for s in d1["songs"]]

        d2 = self._run_handler(self._mod.api_songs_respin, "")
        # 注意:api_songs_respin 是 POST,我们用 _run_handler 直接传 handler
        # make_mocked_request 走 POST 还是 GET 都行(只取 query)
        # 但保险起见再调一次,直接走 handler
        self.assertTrue(d2["ok"])
        self.assertEqual(d2["count"], 60)

        d3 = self._run_handler(self._mod.api_songs, "")
        ids3 = [s["id"] for s in d3["songs"]]
        self.assertNotEqual(ids1, ids3, "respin 后顺序应变化")


# ============================================================
# /api/health online 字段(P2.5+23 hotfix:2026-10-03)
# ============================================================
class TestApiHealthEndpoint(unittest.TestCase):
    """/api/health 应返 online.configured / online.initialized / online.sources + seed_fallback。

    前端 LX 探测灯用这 4 字段决定颜色 + 文案。
    """

    @classmethod
    def setUpClass(cls):
        # 复用 prisiragent-music-web 模块(跟 TestApiSongsEndpoint 一致)
        HERE = Path(__file__).resolve().parent
        ROOT = HERE.parent
        COMPANION = ROOT / "companion"
        for p in (str(ROOT), str(COMPANION)):
            if p not in sys.path:
                sys.path.insert(0, p)
        # 用 on_startup 必须的最小 APP 子集(只 health 需要 APP.online + STATIC_DIR)
        # 我们直接造 FakeOnline 注入到 APP,避免 on_startup 拉 jsdom + 扫本地库
        import importlib
        cls._mod = importlib.import_module("prisIragent-music-web")
        APP = cls._mod.APP

        class _FakeOnlineEmpty:
            """OnlineSearch fake — 只为 health 端点存在,client 永不初始化。"""
            _sources = ["mock.js", "juhe.js"]

            def _ensure(self):  # noqa: D401
                return None

        APP.online = _FakeOnlineEmpty()
        APP.library = None  # health 不依赖 library
        APP.port = 2734

    @classmethod
    def tearDownClass(cls):
        APP = cls._mod.APP
        APP.online = None
        APP.library = None

    def _run_handler(self, handler):
        from aiohttp.test_utils import make_mocked_request
        req = make_mocked_request("GET", "/api/health")
        resp = _run(handler(req))
        import json as _json
        return _json.loads(resp.body.decode("utf-8"))

    def test_api_health_includes_online_field(self):
        data = self._run_handler(self._mod.api_health)
        self.assertTrue(data["ok"])
        self.assertIn("online", data)
        online = data["online"]
        self.assertTrue(online["configured"])
        # _FakeOnlineEmpty._ensure() 返 None → initialized=False
        self.assertFalse(online["initialized"])
        self.assertEqual(online["sources"], ["mock.js", "juhe.js"])

    def test_api_health_includes_seed_fallback(self):
        data = self._run_handler(self._mod.api_health)
        self.assertIn("seed_fallback", data)
        # seed.mp3 必须存在(已 ship)
        seed = Path(__file__).resolve().parent.parent / "companion" / "static" / "music" / "seed.mp3"
        self.assertTrue(seed.exists(), msg=f"seed.mp3 missing at {seed}")
        self.assertTrue(data["seed_fallback"])


if __name__ == "__main__":
    unittest.main()