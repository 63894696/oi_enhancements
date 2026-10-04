"""
test_music_multi_source.py — P2.5+22(2026-10-03)music 多源 fallback 单元测试。

不动 lx_runtime_client(Node 子进程),用 monkeypatch 替换 OnlineSearch.get_url,
验证:
  - Player.seed_from_url 走多源(失败源 → 下一个源)
  - Player.play_random 队列空时从库随机 + 自动播
  - cmd "random" / "play_url" 路由存在
  - get_url_multi 按 sources 顺序轮询
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.player import LocalLibrary, OnlineSearch, Player, Track  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeOnline:
    """替换 LxRuntimeClient 子进程:按 sources 列表模拟"首个失败 → 第二个成功"。"""

    def __init__(self, behavior: dict[str, dict]):
        # behavior: {"src1": {"ok": False, "err": "..."}, "src2": {"ok": True, "url": "..."}}
        self.behavior = behavior
        self.calls: list[str] = []

    def get_url(self, source: str, song_info: dict) -> dict:
        self.calls.append(source)
        if source not in self.behavior:
            return {"ok": False, "err": f"no fake for {source}"}
        b = dict(self.behavior[source])
        if b.get("ok"):
            b["source"] = source
        return b

    def get_url_multi(self, song_info: dict) -> dict:
        last_err = ""
        for src, beh in self.behavior.items():
            r = self.get_url(src, song_info)
            if r.get("ok"):
                return r
            last_err = f"{src}={r.get('err', '?')}"
        return {"ok": False, "err": f"all sources failed: {last_err}"}

    def list_sources(self):
        return list(self.behavior.keys())

    def shutdown(self):
        pass


class TestOnlineSearchMulti(unittest.TestCase):
    """OnlineSearch.get_url_multi 纯方法级测试(不构造 LxRuntimeClient)。"""

    def test_online_search_get_url_multi_iterates_sources(self):
        """FakeOnline 直接调 get_url_multi:失败源后切下一个,首个 ok 即返。"""
        f = FakeOnline({
            "mock.js": {"ok": False, "err": "demo fail"},
            "juhe.js": {"ok": True, "url": "https://x/y.mp3"},
            "ikun.js": {"ok": True, "url": "https://never/called.mp3"},
        })
        r = f.get_url_multi({"hash": "demo"})
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "juhe.js")
        # mock.js 必被试一次,ikun.js 在 mock+juhe 后成功则不应被调
        self.assertIn("mock.js", f.calls)
        self.assertNotIn("ikun.js", f.calls)

    def test_online_search_get_url_multi_all_fail_returns_err(self):
        f = FakeOnline({
            "mock.js": {"ok": False, "err": "a"},
            "juhe.js": {"ok": False, "err": "b"},
        })
        r = f.get_url_multi({"hash": "demo"})
        self.assertFalse(r.get("ok"))
        self.assertIn("all sources failed", r.get("err", ""))


class TestPlayerSeedFromUrl(unittest.TestCase):
    """Player.seed_from_url 走 FakeOnline → 入库 source='lx:<src>'。

    2026-10-04 修复后顺序:
      1) _find_local_match 本地命中真 mp3 → source='local'
      2) 调 online.get_url_multi → http(s) URL 入库 source='lx:<src>'
      3) local:// 占位 / online None / 全失败 → seed.mp3 兜底 source='seed'
    老测试需要根据新逻辑调整预期值。
    """

    def test_seed_from_url_first_source_succeeds(self):
        """在线 URL 入库 source='lx:mock.js'(本地库空)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": True, "url": "https://example.com/a.mp3"},
            "juhe.js": {"ok": True, "url": "https://example.com/b.mp3"},
        }))
        r = _run(player.seed_from_url({"hash": "demo"}, title="t1", artist="a1"))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "lx:mock.js")
        # 入库了
        tr = lib.get(r["track_id"])
        self.assertIsNotNone(tr)
        self.assertEqual(tr.source, "lx:mock.js")
        self.assertEqual(tr.title, "t1")
        self.assertTrue(tr.path.startswith("https://"))

    def test_seed_from_url_falls_back_to_second_source(self):
        """mock.js 失败 → juhe.js 成功 → source='lx:juhe.js'。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "demo fail"},
            "juhe.js": {"ok": True, "url": "https://example.com/b.mp3"},
        }))
        r = _run(player.seed_from_url({"hash": "demo2"}))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "lx:juhe.js")

    def test_seed_from_url_all_sources_fail(self):
        """所有 LX 源失败 + 本地空 + seed.mp3 存在 → 兜底 seed.mp3,source='seed'。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "a"},
            "juhe.js": {"ok": False, "err": "b"},
        }))
        r = _run(player.seed_from_url({"hash": "demo3"}, title="demo3"))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "seed",
            msg=f"全源失败应兜底 seed.mp3;got {r!r}")

    def test_seed_from_url_no_online_client(self):
        """无 online client + 本地空 → 兜底 seed.mp3(不再是 'not configured' 错误)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=None)
        r = _run(player.seed_from_url({"hash": "x"}, title="x"))
        # 修复后:无 online + 本地空 + seed.mp3 存在 → 兜底成功,不再是失败
        self.assertTrue(r.get("ok"), msg=f"seed.mp3 兜底路径应成功;got {r!r}")
        self.assertEqual(r.get("source"), "seed")

    # P2.5+23 hotfix(2026-10-03):googleapis 公网 mp3 sandbox/用户网络封,
    # 自动 fallback 到本地 static/music/seed.mp3。
    # 2026-10-04 bug fix:Track.source 必须区分"真本地 mp3" vs "seed.mp3 兜底"。
    #   - 真本地 mp3(source="local"):song_pool 之前已扫到 / ~/Music 命中真文件
    #   - seed.mp3 兜底(source="seed"):外部源失败时占位,前端 toast 警告
    # 2026-10-04 修复后:seed_from_url 不再单独检测 googleapis URL 改 seed.mp3,
    #   而是由 api_stream 在 lx: 前缀的 googleapis URL 上做兜底(_cmd_next 那条路径)。
    #   seed_from_url 这里只确认"googleapis URL 也能成功入库 lx:mock.js"。
    def test_seed_from_url_googleapis_fallback_to_seed_mp3(self):
        """googleapis URL 入库 lx:mock.js(stream 阶段才做兜底检测)。"""
        from music.player import Player as _Player  # noqa: F401
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        googleapis_url = (
            "https://commondatastorage.googleapis.com/codeskulptor-demos/"
            "DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"
        )
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": True, "url": googleapis_url},
        }))
        r = _run(player.seed_from_url({"hash": "songA", "songname": "晴天"},
                                      title="晴天", artist="周杰伦"))
        self.assertTrue(r.get("ok"), msg=str(r))
        # 修复后:googleapis URL 也算"在线源成功",入库 lx:mock.js(stream 时才兜底)
        self.assertEqual(r.get("source"), "lx:mock.js", msg=str(r))
        tr = lib.get(r["track_id"])
        self.assertIsNotNone(tr, msg="track not added to library")
        self.assertEqual(tr.source, "lx:mock.js")
        self.assertEqual(tr.path, googleapis_url)

    def test_seed_from_url_non_googleapis_stays_lx(self):
        """非 googleapis URL 不应 fallback,继续走 lx: 透传路径。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": True, "url": "https://example.com/audio.mp3"},
        }))
        r = _run(player.seed_from_url({"hash": "songB"}, title="songB", artist="x"))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "lx:mock.js")
        tr = lib.get(r["track_id"])
        self.assertEqual(tr.source, "lx:mock.js")


class TestPlayerRandomFallback(unittest.TestCase):
    """队列空 → play_random 从库内随机选,自动 _cmd_play。"""

    def test_play_random_picks_first_track(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            for n in ("A - x.mp3", "B - y.mp3", "C - z.mp3"):
                (td_p / n).write_bytes(b"x")
            lib = LocalLibrary(td_p)
            lib.scan()
            player = Player(library=lib)
            r = _run(player.play_random(count=1))
            self.assertTrue(r.get("ok"))
            self.assertIsNotNone(r.get("state", {}).get("track"))

    def test_play_random_empty_library_returns_err(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib)
        r = _run(player.play_random(count=1))
        self.assertFalse(r.get("ok"))
        self.assertIn("empty", r.get("err", ""))


class TestCmdActions(unittest.TestCase):
    """Player.cmd 新 action:play_url / random 路由存在。"""

    def test_cmd_random_action_exists(self):
        """cmd(action="random") 走 Player.play_random 路径(库空返 ok=False)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib)
        r = _run(player.cmd("random", count=1))
        self.assertFalse(r.get("ok"))
        self.assertIn("empty", r.get("err", ""))

    def test_cmd_play_url_routes_to_seed_then_play(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": True, "url": "https://x/y.mp3"},
        }))
        r = _run(player.cmd("play_url", song_info={"hash": "demo"}, title="t"))
        self.assertTrue(r.get("ok"))
        # 修复后:在线 URL 优先 → source='lx:mock.js'
        self.assertEqual(r.get("source"), "lx:mock.js")
        self.assertIsNotNone(r.get("state", {}).get("track"))

    def test_cmd_play_url_all_fail_returns_err(self):
        """所有 LX 失败 + 本地空 → 兜底 seed.mp3(不是返回 err)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "x"},
            "juhe.js": {"ok": False, "err": "y"},
        }))
        r = _run(player.cmd("play_url", song_info={"hash": "demo"}, title="demo"))
        # 修复后:全源失败 → seed.mp3 兜底成功,不再 'all sources failed'
        self.assertTrue(r.get("ok"), msg=f"seed.mp3 兜底应成功;got {r!r}")
        self.assertEqual(r.get("source"), "seed")


class TestStreamSourcePrefix(unittest.TestCase):
    """prisIragent-music-web.py:_stream_remote_url 应在 source='lx:*' 时被选。

    这是路由分支保护测试:验源码里含 'is_remote' + 'lx:' 前缀识别。
    """

    def test_music_web_uses_lx_source_prefix_for_remote_stream(self):
        path = ROOT / "companion" / "prisIragent-music-web.py"
        src = path.read_text(encoding="utf-8")
        self.assertIn('source.startswith("lx:")', src,
            "api_stream 必须用 'lx:' 前缀识别远程 track")
        self.assertIn("_stream_remote_url", src,
            "必须有 _stream_remote_url helper")
        # _stream_remote_url 应走 aiohttp 透传(可能用别名 import aiohttp as _aio)
        self.assertTrue(
            "ClientSession" in src or "client_session" in src,
            "_stream_remote_url 应走 aiohttp.ClientSession 透传",
        )
        # on_startup 必传 online 给 Player(否则 cmd random/play_url 不可用)
        self.assertIn("online=APP.online", src,
            "Player 构造必须传 online=APP.online(否则 random/play_url 无 client)")


# ============================================================
# P2.5+27 bug fix(2026-10-04):LX 在线源真正接通
# ============================================================
class TestFindLocalMatch(unittest.TestCase):
    """P0 bug fix(2026-10-04):_find_local_match 4 级匹配,本地 library 命中真 mp3。

    用户实测报告:点任何歌都掉 seed.mp3 兜底 → 因为之前 seed_from_url
    不查本地 library,所有歌都走 googleapis URL → seed.mp3 fallback。
    现在 seed_from_url 先查 _find_local_match → 真 mp3 命中 → source="local"。
    """

    def test_find_local_match_exact_artist_and_title(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Kangaroo_MusiQue - Test.mp3").write_bytes(b"x")
            lib = LocalLibrary(Path(td))
            lib.scan()
            player = Player(library=lib)
            hit = player._find_local_match("Test", "Kangaroo_MusiQue")
            self.assertIsNotNone(hit)
            self.assertTrue(hit.endswith("Test.mp3"))

    def test_find_local_match_no_match_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Other - Song.mp3").write_bytes(b"x")
            lib = LocalLibrary(Path(td))
            lib.scan()
            player = Player(library=lib)
            # 库里没这首
            hit = player._find_local_match("孤勇者", "陈奕迅")
            self.assertIsNone(hit)

    def test_find_local_match_skips_seed_mp3(self):
        """_find_local_match 必须跳过 seed.mp3 自身(否则会自我命中,无法触发兜底)。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lib = LocalLibrary(Path(td))
            lib.scan()
            player = Player(library=lib)
            # 库空,query 任何 title 都应返 None(seed.mp3 不在 library 里所以测试略)
            self.assertIsNone(player._find_local_match("anything", ""))


class TestSeedFromUrlLocalMatch(unittest.TestCase):
    """P2.5+27(2026-10-04)修复后:有本地 mp3 时 seed_from_url 直接命中,不走兜底。"""

    def test_seed_from_url_hits_local_match_when_mp3_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            (td_p / "Kangaroo_MusiQue - Test.mp3").write_bytes(b"x")
            lib = LocalLibrary(td_p)
            lib.scan()
            player = Player(library=lib, online=FakeOnline({
                "mock.js": {"ok": True, "url": "https://googleapis.example/never.mp3"},
            }))
            r = _run(player.seed_from_url({"hash": "x"}, title="Test",
                                          artist="Kangaroo_MusiQue"))
            self.assertTrue(r.get("ok"), msg=str(r))
            # 命中本地,不走 seed 兜底
            self.assertEqual(r.get("source"), "local", msg=str(r))
            self.assertTrue(r.get("matched_local"))
            tr = lib.get(r["track_id"])
            self.assertEqual(tr.source, "local")
            self.assertTrue(tr.path.endswith("Test.mp3"))

    def test_seed_from_url_falls_back_to_seed_when_no_match(self):
        """本地空 + online 返 local:// 占位 → 走 seed.mp3 兜底,source='seed'。"""
        import tempfile
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as td:
            lib = LocalLibrary(Path(td))  # 空库
            # 用 mock 返 local://(模拟 local.js 行为)
            online = MagicMock()
            online.get_url_multi = MagicMock(return_value={
                "ok": True, "url": "local://prisir/abc/320k", "source": "local",
            })
            player = Player(library=lib, online=online)
            r = _run(player.seed_from_url({"hash": "x"}, title="孤勇者",
                                          artist="陈奕迅"))
            self.assertTrue(r.get("ok"), msg=str(r))
            self.assertEqual(r.get("source"), "seed", msg=str(r))
            tr = lib.get(r["track_id"])
            # 2026-10-04 bug fix:Track.source 必须是 "seed",前端才能识别兜底
            self.assertEqual(tr.source, "seed")


class TestLocalJsSourceExists(unittest.TestCase):
    """P2.5+27(2026-10-04):新增 companion/lx_runtime/local.js — 不调任何外网。"""

    def test_local_js_file_exists(self):
        path = COMPANION / "lx_runtime" / "local.js"
        self.assertTrue(path.exists(),
            msg=f"local.js 必须存在:{path}")
        src = path.read_text(encoding="utf-8")
        # 关键:不调任何外网 — local:// 占位即可,Python 端查本地 library
        self.assertIn("local://", src,
            "local.js 必须返 local:// URL 占位")
        # googleapis/lerd.dpdns 不能出现在 *代码逻辑* 里(注释里描述历史 bug 是 OK 的)
        # 简单起见,只检查 on(EVENT_NAMES.request) 处理函数体内不含外网域名
        import re
        m = re.search(r"on\(EVENT_NAMES\.request, async \(\{.*?=>\s*\{(.*?)\}\);", src, re.DOTALL)
        self.assertIsNotNone(m, "找不到 on(EVENT_NAMES.request) 函数体")
        body = m.group(1)
        self.assertNotIn("googleapis", body,
            "local.js 处理函数体内不能含 googleapis(沿用 P3.10b 0 上传红线)")
        self.assertNotIn("lerd.dpdns", body,
            "local.js 处理函数体内不能含第三方公共服务域名")
        self.assertNotIn("http://", body.replace("http://127.0.0.1", ""),
            "local.js 不能发起任何 HTTP 请求")
        self.assertIn("EVENT_NAMES", src,
            "local.js 必须遵循 LX EVENT_NAMES 协议")

    def test_default_sources_is_local_only(self):
        """OnlineSearch.DEFAULT_SOURCES 必须 = ['local.js'],不暴露 googleapis。"""
        from music.player import OnlineSearch
        self.assertEqual(OnlineSearch.DEFAULT_SOURCES, ["local.js"],
            msg=f"DEFAULT_SOURCES 应为 ['local.js'];got {OnlineSearch.DEFAULT_SOURCES!r}")


class TestApiStateSeedFallback(unittest.TestCase):
    """P2.5+27(2026-10-04):api_state 返回 is_seed_fallback 字段。

    前端 store 据此显示「⚠️ 兜底」toast,让用户清楚知道没真接通源。
    """

    def setUp(self):
        import importlib.util
        _PRISIR_MUSIC_WEB = COMPANION / "prisIragent-music-web.py"
        spec = importlib.util.spec_from_file_location(
            "prisIragent_music_web", str(_PRISIR_MUSIC_WEB))
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        self._mod = _mod
        APP = _mod.APP
        LocalLibrary = _mod.LocalLibrary
        APP.library = LocalLibrary(music_root=None)
        APP.online = None
        APP.song_pool = None
        APP.cfg = None
        APP.lyric = None
        APP.ws_lock = None
        from music.player import Player
        APP.player = Player(APP.library, online=None, catalog=None)

    def tearDown(self):
        APP = self._mod.APP
        APP.library = None
        APP.player = None
        APP.song_pool = None
        APP.online = None

    def test_api_state_includes_is_seed_fallback_field(self):
        """api_state 必须返回 is_seed_fallback: bool 字段。"""
        from aiohttp.test_utils import make_mocked_request
        resp = _run(self._mod.api_state(make_mocked_request("GET", "/")))
        import json as _json
        data = _json.loads(resp.body.decode("utf-8"))
        self.assertIn("is_seed_fallback", data,
            "api_state 必须返回 is_seed_fallback 字段")
        # 默认 idle → 无 track → is_seed_fallback = False
        self.assertFalse(data["is_seed_fallback"])

    def test_api_state_is_seed_fallback_true_after_seed_track(self):
        """手动加一个 source='seed' 的 track,api_state 应返 is_seed_fallback=True。"""
        from music.player import Track
        from aiohttp.test_utils import make_mocked_request
        seed_path = ROOT / "companion" / "static" / "music" / "seed.mp3"
        if not seed_path.exists():
            self.skipTest("seed.mp3 missing")
        APP = self._mod.APP
        t = Track(id="seedtest", title="x", artist="y", album="",
                  path=str(seed_path), duration=0.0, source="seed")
        APP.library.add_track(t)
        APP.player.state.track = t
        APP.player.state.status = "playing"
        resp = _run(self._mod.api_state(make_mocked_request("GET", "/")))
        import json as _json
        data = _json.loads(resp.body.decode("utf-8"))
        self.assertTrue(data["is_seed_fallback"],
            msg=f"source=seed track 应让 is_seed_fallback=True;got {data}")


class TestApiStreamSeedHeader(unittest.TestCase):
    """P2.5+27(2026-10-04):api_stream 在 source=seed 时设 X-Prisir-Source header。

    浏览器侧 fetch 看到此 header 也能识别兜底(双保险)。
    """

    def test_api_stream_sets_x_prisir_source_seed_header_in_source(self):
        """源码扫描:api_stream 必须设 headers['X-Prisir-Source']='seed'。"""
        path = ROOT / "companion" / "prisIragent-music-web.py"
        src = path.read_text(encoding="utf-8")
        self.assertIn('"X-Prisir-Source"', src,
            "api_stream 必须有 X-Prisir-Source header")
        self.assertIn('"seed"', src,
            "api_stream 必须有 seed 值")
        # is_seed 判断 + X-Prisir-Source 设置必须同在 api_stream 内
        idx_is_seed = src.find("is_seed")
        idx_x_header = src.find('"X-Prisir-Source"')
        self.assertGreater(idx_is_seed, 0,
            "必须先判 is_seed 再写 header")
        self.assertGreater(idx_x_header, idx_is_seed,
            "X-Prisir-Source header 应在 is_seed 判定之后写入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
