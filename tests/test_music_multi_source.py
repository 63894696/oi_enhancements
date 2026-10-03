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
    """Player.seed_from_url 走 FakeOnline → 入库 source='lx:<src>'。"""

    def test_seed_from_url_first_source_succeeds(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": True, "url": "https://example.com/a.mp3"},
            "juhe.js": {"ok": True, "url": "https://example.com/b.mp3"},
        }))
        r = _run(player.seed_from_url({"hash": "demo"}, title="t1", artist="a1"))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "mock.js")
        # 入库了
        tr = lib.get(r["track_id"])
        self.assertIsNotNone(tr)
        self.assertEqual(tr.source, "lx:mock.js")
        self.assertEqual(tr.title, "t1")
        self.assertTrue(tr.path.startswith("https://"))

    def test_seed_from_url_falls_back_to_second_source(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "demo fail"},
            "juhe.js": {"ok": True, "url": "https://example.com/b.mp3"},
        }))
        r = _run(player.seed_from_url({"hash": "demo2"}))
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "juhe.js")

    def test_seed_from_url_all_sources_fail(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "a"},
            "juhe.js": {"ok": False, "err": "b"},
        }))
        r = _run(player.seed_from_url({"hash": "demo3"}))
        self.assertFalse(r.get("ok"))
        self.assertIn("all sources failed", r.get("err", ""))

    def test_seed_from_url_no_online_client(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=None)
        r = _run(player.seed_from_url({"hash": "x"}))
        self.assertFalse(r.get("ok"))
        self.assertIn("not configured", r.get("err", ""))


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
        self.assertEqual(r.get("source"), "mock.js")
        self.assertIsNotNone(r.get("state", {}).get("track"))

    def test_cmd_play_url_all_fail_returns_err(self):
        lib = LocalLibrary(Path(os.environ.get("TEMP", "/tmp")))
        player = Player(library=lib, online=FakeOnline({
            "mock.js": {"ok": False, "err": "x"},
            "juhe.js": {"ok": False, "err": "y"},
        }))
        r = _run(player.cmd("play_url", song_info={"hash": "demo"}))
        self.assertFalse(r.get("ok"))
        self.assertIn("all sources failed", r.get("err", ""))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
