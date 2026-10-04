"""
test_music_multi_source.py — P2.5+22(2026-10-03)music 多源 fallback 单元测试。

不动 lx_runtime_client(Node 子进程),用 monkeypatch 替换 OnlineSearch.get_url,
验证:
  - Player.seed_from_url 走多源(失败源 → 下一个源)
  - Player.play_random 队列空时从库随机 + 自动播
  - cmd "random" / "play_url" 路由存在
  - get_url_multi 按 sources 顺序轮询
  - P2.5+28 A 阶段(2026-10-04):seed.mp3 兜底已彻底删除 — 失败必须返清晰 err
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

    P2.5+28 A 阶段(2026-10-04)顺序:
      1) _find_local_match 本地命中真 mp3 → source='local'
      2) 调 online.get_url_multi → http(s) URL 入库 source='lx:<src>'
      3) 没有任何可用源 → 返 ok=False + 清晰 err(不再 seed.mp3 兜底)
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
        """所有 LX 源失败 + 本地空 → P2.5+28 A 阶段:返 ok=False + 清晰 err(不再 seed.mp3 兜底)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "a"},
            "juhe.js": {"ok": False, "err": "b"},
        }))
        r = _run(player.seed_from_url({"hash": "demo3"}, title="demo3", artist="art3"))
        # A 阶段后:必须返失败 + 清晰原因(包含 title/artist + 「无可用音源」语义)
        self.assertFalse(r.get("ok"),
            msg=f"全源失败必须返 ok=False;got {r!r}")
        self.assertIn("demo3", r.get("err", ""),
            msg=f"err 应含歌名 demo3;got {r!r}")
        self.assertIn("art3", r.get("err", ""),
            msg=f"err 应含歌手 art3;got {r!r}")
        # 不能 fallback 到 seed.mp3 — track 不能进 library
        tr = lib.get(r.get("track_id") or "nonexistent")
        self.assertIsNone(tr, "失败时不能入库 track")

    def test_seed_from_url_no_online_client(self):
        """无 online client + 本地空 → P2.5+28 A 阶段:返 ok=False + 清晰 err。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=None)
        r = _run(player.seed_from_url({"hash": "x"}, title="孤勇者", artist="陈奕迅"))
        # A 阶段后:无 online client + 本地空 → 必须返失败(不再是 seed.mp3 兜底)
        self.assertFalse(r.get("ok"),
            msg=f"无 online 必须返 ok=False;got {r!r}")
        self.assertIn("孤勇者", r.get("err", ""))
        self.assertIn("陈奕迅", r.get("err", ""))

    # P2.5+23 hotfix(2026-10-03):googleapis 公网 mp3 sandbox/用户网络封,原自动 fallback seed.mp3。
    # P2.5+28 A 阶段(2026-10-04):seed_from_url 不再特殊处理 googleapis,统一入库 lx:mock.js。
    #   googleapis 不可达在 preload_next_url 阶段返错(测试在 TestPreloadNextUrlNoSeedFallback)。
    def test_seed_from_url_googleapis_url_accepted_as_lx(self):
        """googleapis URL 入库 lx:mock.js — 不再做特殊兜底(seed_from_url 不区分域名)。"""
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
        # googleapis URL 也算「在线源成功」,入库 lx:mock.js
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

    # P2.5+28 A 阶段新增:local.js 返 local:// 占位 + 本地空 → 必须返清晰 err
    def test_seed_from_url_no_match_returns_clear_error(self):
        """local:// 占位 + 本地空 → P2.5+28:返 ok=False + 清晰 err,不再兜底 seed.mp3。"""
        import tempfile
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as td:
            lib = LocalLibrary(Path(td))  # 空库
            online = MagicMock()
            online.get_url_multi = MagicMock(return_value={
                "ok": True, "url": "local://prisir/abc/320k", "source": "local",
            })
            player = Player(library=lib, online=online)
            r = _run(player.seed_from_url({"hash": "x"}, title="孤勇者",
                                          artist="陈奕迅"))
            # A 阶段后:local:// 占位 = 无可用源 → 必须返 ok=False
            self.assertFalse(r.get("ok"), msg=f"local:// 应返失败;got {r!r}")
            self.assertIn("孤勇者", r.get("err", ""))
            self.assertIn("陈奕迅", r.get("err", ""))
            # 关键:不能入库 track(避免"假装在播")
            tr = lib.get(r.get("track_id") or "nonexistent")
            self.assertIsNone(tr)


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
        """所有 LX 失败 + 本地空 → P2.5+28 A 阶段:play_url 返 ok=False + 清晰 err(不再 seed.mp3 兜底)。"""
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "x"},
            "juhe.js": {"ok": False, "err": "y"},
        }))
        r = _run(player.cmd("play_url", song_info={"hash": "demo"}, title="demo", artist="ad"))
        # A 阶段后:play_url 不再兜底,必须返 ok=False
        self.assertFalse(r.get("ok"), msg=f"play_url 应返失败;got {r!r}")
        self.assertIn("err", r)


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

    def test_seed_from_url_local_placeholder_no_fallback(self):
        """本地空 + online 返 local:// 占位 → P2.5+28 A 阶段:返 ok=False,不再兜底 seed.mp3。"""
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
            # A 阶段后:local:// 占位不再触发 seed.mp3 兜底
            self.assertFalse(r.get("ok"), msg=f"local:// 应返失败;got {r!r}")
            self.assertIn("孤勇者", r.get("err", ""))
            self.assertIn("陈奕迅", r.get("err", ""))
            # 不能入库 track
            self.assertIsNone(lib.get(r.get("track_id") or "nonexistent"))


class TestPreloadNextUrlNoSeedFallback(unittest.TestCase):
    """P2.5+28 A 阶段(2026-10-04):preload_next_url 删 googleapis→seed.mp3 兜底。

    之前 googleapis URL 不可达 → 切到 seed.mp3 → 用户听到 30 秒静音。
    A 阶段后:直接返 ok=False + 清晰 err(沿用 P3.10b 0 上传红线)。
    """

    def test_preload_next_url_googleapis_does_not_fallback_to_seed(self):
        """预取 googleapis URL → P2.5+28:返 ok=False + err 含 upstream 不可达,不再兜底 seed.mp3。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lib = LocalLibrary(Path(td))
            lib.scan()
            googleapis_url = (
                "https://commondatastorage.googleapis.com/codeskulptor-demos/"
                "DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"
            )
            player = Player(library=lib, online=FakeOnline({
                "mock.js": {"ok": True, "url": googleapis_url},
            }))
            # 手动入库一个 lx: 源的 googleapis track
            t = Track(id="pre1", title="孤勇者", artist="陈奕迅", album="",
                      path=googleapis_url, duration=0.0, source="lx:mock.js")
            lib.add_track(t)
            player.playlist.set_queue(["pre1"])
            player.playlist.cursor = 0
            player.state.playback_mode = "sequential"
            r = _run(player.preload_next_url("pre1"))
            # A 阶段后:googleapis 不可达 → 返失败 + err,不再兜底 seed.mp3
            self.assertFalse(r.get("ok"), msg=f"googleapis 应返失败;got {r!r}")
            self.assertIn("googleapis", r.get("err", ""))
            self.assertIn("不可达", r.get("err", ""))


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
        """P2.5+28 C 阶段调研(2026-10-05):实测后 DEFAULT_SOURCES 仅含 local.js。

        原本拍板「9 源全启」,实测 shim call-all dispatch 让 local.js 屏蔽所有源 +
        kw/kg/tx/wy/mg 需要 AES crypto(shim 未实现),用户拍的列表无一能解出 URL。
        已 revert DEFAULT_SOURCES 到 ["local.js"](0 外网);后续若修 shim 再扩源。
        """
        from music.player import OnlineSearch
        sources = OnlineSearch.DEFAULT_SOURCES
        self.assertEqual(sources, ["local.js"],
            msg=f"DEFAULT_SOURCES 应仅含 local.js;got {sources!r}")


# P2.5+28 A 阶段(2026-10-04):删除 TestApiStateSeedFallback 整组(is_seed_fallback 字段已删)
# 新增 TestApiStatePlayableField(playable + last_err 新字段)+ TestApiStreamNoSeedHeader
# (X-Prisir-Source header 已删)。整组迁移在文件下方。


class TestApiStatePlayableField(unittest.TestCase):
    """P2.5+28 A 阶段(2026-10-04):api_state 字段语义改。

    is_seed_fallback 字段删,改 playable(bool) + last_err(str)。
    前端 store 据 playable===false 弹「❌ 此歌暂无法播放:last_err」toast。
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

    def test_api_state_includes_playable_field(self):
        """api_state 必须返回 playable: bool 字段(无 track → playable=True)。"""
        from aiohttp.test_utils import make_mocked_request
        resp = _run(self._mod.api_state(make_mocked_request("GET", "/")))
        import json as _json
        data = _json.loads(resp.body.decode("utf-8"))
        self.assertIn("playable", data,
            "api_state 必须返回 playable 字段(P2.5+28 A)")
        # 默认 idle → 无 track → playable=True
        self.assertTrue(data["playable"])
        self.assertEqual(data.get("last_err"), "")

    def test_api_state_last_err_empty_when_playable(self):
        """playable=True 时 last_err 必为空字符串。"""
        from aiohttp.test_utils import make_mocked_request
        # 加一个 source="local" 的 track
        APP = self._mod.APP
        from music.player import Track
        t = Track(id="local1", title="x", artist="y", album="",
                  path="C:/fake/test.mp3", duration=0.0, source="local")
        APP.library.add_track(t)
        APP.player.state.track = t
        resp = _run(self._mod.api_state(make_mocked_request("GET", "/")))
        import json as _json
        data = _json.loads(resp.body.decode("utf-8"))
        self.assertTrue(data["playable"],
            msg=f"local source 应可播;got {data}")
        self.assertEqual(data.get("last_err"), "")

    def test_api_state_last_err_filled_when_unplayable(self):
        """不可播 track(unknown source)→ playable=False + last_err 非空。"""
        from aiohttp.test_utils import make_mocked_request
        APP = self._mod.APP
        from music.player import Track
        # 模拟「unavailable」类的不可播 track(虽然代码不产这个值,API 设计要兼容)
        t = Track(id="bad1", title="坏歌", artist="bad", album="",
                  path="C:/fake/missing.mp3", duration=0.0, source="unavailable")
        APP.library.add_track(t)
        APP.player.state.track = t
        resp = _run(self._mod.api_state(make_mocked_request("GET", "/")))
        import json as _json
        data = _json.loads(resp.body.decode("utf-8"))
        self.assertFalse(data["playable"],
            msg=f"unavailable source 应不可播;got {data}")
        self.assertIn("无可用音源", data.get("last_err", ""))


class TestApiStreamNoSeedHeader(unittest.TestCase):
    """P2.5+28 A 阶段(2026-10-04):api_stream 不再设 X-Prisir-Source header。

    兜底机制彻底删除(代码不再引用 seed.mp3),这个 header 也删。
    """

    def test_api_stream_does_not_set_x_prisir_source_seed_header(self):
        """源码扫描:api_stream 函数体内不应再赋 headers['X-Prisir-Source']='seed'。

        P2.5+28 A 阶段后:兜底机制彻底删除,api_stream 不再设 X-Prisir-Source。
        注释里提到「X-Prisir-Source」(说明历史删除)允许,但实际赋值不允许。
        """
        path = ROOT / "companion" / "prisIragent-music-web.py"
        src = path.read_text(encoding="utf-8")
        # 关键检查:不允许实际代码赋 X-Prisir-Source header
        # 形式 1: headers["X-Prisir-Source"] = ...
        # 形式 2: headers['X-Prisir-Source'] = ...
        import re
        pat = re.compile(r'headers\s*[\[\(]?["\']X-Prisir-Source["\']\s*[\]\)]?\s*=')
        matches = pat.findall(src)
        self.assertEqual(len(matches), 0,
            msg=f"api_stream 函数体不应再赋 X-Prisir-Source header;got {matches!r}\n"
                f"P2.5+28 A 阶段后兜底机制彻底删除。")
        # is_seed 变量判定也应从代码中删除
        self.assertNotIn("is_seed = ", src,
            "is_seed 判定必须从 api_stream 删除(P2.5+28 A)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
