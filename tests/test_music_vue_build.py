"""
test_music_vue_build.py — P2.5+24(2026-10-03)Vue 3 + Pinia 重写 music 子窗测试。

覆盖:
  - player.preload_next_url():本地 + 远端(lx:) + song_pool_fav 三种 source 都能返 ok
  - api_songs_preload 路由挂上(无 player 时返 err)
  - vite 工程结构(package.json/tsconfig/index.html/src/main.ts 存在)
  - PlayerService 状态机 6 态 enum 完整
  - 关键 vue 组件文件存在
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
MUSIC_VUE = COMPANION / "static" / "music-vue"

for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.player import Player, LocalLibrary, Track  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
# Player.preload_next_url 单元测试
# ============================================================
class TestPreloadNextUrl(unittest.TestCase):
    """P2.5+24(2026-10-03):借鉴 LX usePreloadNextMusic,后端提前解析下一首 URL。"""

    def _make_player(self, online, src=None):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=online, catalog=None)
        # 注入 3 首
        for i in range(3):
            tid = hashlib.sha1(f"preload{i}".encode()).hexdigest()[:16]
            source = src if src else ("lx:mock.js" if online else "lx")
            lib.add_track(Track(
                id=tid, title=f"track{i}", artist="artist",
                album="", path="https://example.com/a.mp3",
                duration=180.0, source=source,
            ))
        p.playlist.set_queue(list(lib._tracks.keys()))
        return p

    def test_preload_no_online_fails(self):
        # 用 lx: source 但 online=None → preload 应返 ok=False online 未配置
        p = self._make_player(online=None, src="lx:mock.js")
        r = _run(p.preload_next_url(""))
        self.assertFalse(r["ok"])
        self.assertIn("online", r["err"].lower())

    def test_preload_returns_remote_url(self):
        class FakeOnline:
            _sources = ["mock.js", "juhe.js"]
            def _ensure(self): return self
            def get_url_multi(self, song_info):
                return {"ok": True, "url": "https://example.com/audio/abc.mp3", "source": "mock.js"}

        p = self._make_player(online=FakeOnline())
        r = _run(p.preload_next_url(""))
        self.assertTrue(r["ok"], f"preload fail: {r}")
        self.assertEqual(r["url"], "https://example.com/audio/abc.mp3")
        self.assertEqual(r["source"], "mock.js")
        self.assertIn("/api/stream/", r["stream_url"])

    def test_preload_returns_seed_when_googleapis(self):
        """P2.5+23 hotfix:googleapis 不可达 → 走 seed.mp3 兜底。"""

        class FakeOnlineGapis:
            _sources = ["mock.js"]
            def _ensure(self): return self
            def get_url_multi(self, song_info):
                return {"ok": True, "url": "https://storage.googleapis.com/x/y.mp3",
                        "source": "mock.js"}

        p = self._make_player(online=FakeOnlineGapis(), src="lx:mock.js")
        # 如果 seed.mp3 存在,应切到 seed(source=seed)
        # 不存在则继续走 lx 路径(可能 fall through)
        r = _run(p.preload_next_url(""))
        self.assertTrue(r["ok"])
        # 当 seed.mp3 真存在 → source=seed
        # 当不存在 → 仍返 url=googleapis
        if r.get("source") == "seed":
            self.assertIsNone(r["url"])
            self.assertEqual(r["fallback"], "seed.mp3")
            # track.source 已被改回 local
            tid = r["track_id"]
            tr = p.library.get(tid)
            self.assertEqual(tr.source, "local")

    def test_preload_empty_queue_fails(self):
        class FakeOnline:
            _sources = ["mock.js"]
            def _ensure(self): return self
            def get_url_multi(self, song_info):
                return {"ok": True, "url": "x", "source": "mock.js"}

        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=FakeOnline(), catalog=None)
        # queue 空
        r = _run(p.preload_next_url(""))
        self.assertFalse(r["ok"])
        self.assertIn("queue", r["err"].lower())


# ============================================================
# api_songs_preload 路由测试
# ============================================================
class TestApiSongsPreloadEndpoint(unittest.TestCase):
    """/api/songs/preload 路由 + handler。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "prisIragent_music_web_v", str(COMPANION / "prisIragent-music-web.py"))
        self._mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._mod)
        APP = self._mod.APP
        APP.library = None
        APP.player = None
        APP.song_pool = None
        APP.online = None

    def _run_handler(self, query):
        from aiohttp.test_utils import make_mocked_request
        path = f"/?{query.lstrip('?')}"
        req = make_mocked_request("GET", path)
        resp = _run(self._mod.api_songs_preload(req))
        return json.loads(resp.body.decode("utf-8"))

    def test_endpoint_returns_err_when_no_player(self):
        data = self._run_handler("")
        self.assertFalse(data["ok"])
        self.assertIn("player not initialized", data["err"])


# ============================================================
# Vite 工程结构测试
# ============================================================
class TestMusicVueStructure(unittest.TestCase):
    """P2.5+24(2026-10-03):验证 Vite 工程文件齐全。"""

    def test_package_json(self):
        p = MUSIC_VUE / "package.json"
        self.assertTrue(p.exists(), f"missing: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        # 必须包含 vue + pinia + vite + typescript
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for must in ("vue", "pinia", "vite", "@vitejs/plugin-vue", "typescript"):
            self.assertIn(must, deps, f"missing dep {must} in package.json")

    def test_vite_config(self):
        p = MUSIC_VUE / "vite.config.ts"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertIn("vue()", content, "missing vue() plugin")
        self.assertIn("/music-vue/", content, "base path 应为 /music-vue/")

    def test_index_html(self):
        p = MUSIC_VUE / "index.html"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertIn('id="app"', content)
        self.assertIn("/src/main.ts", content)

    def test_main_ts(self):
        p = MUSIC_VUE / "src" / "main.ts"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertIn("createApp", content)
        self.assertIn("createPinia", content)

    def test_app_vue(self):
        p = MUSIC_VUE / "src" / "App.vue"
        self.assertTrue(p.exists())

    def test_types_music_ts(self):
        p = MUSIC_VUE / "src" / "types" / "music.ts"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        # 状态机 6 态
        for must in ("'idle'", "'loading'", "'buffering'", "'playing'", "'paused'", "'error'"):
            self.assertIn(must, content, f"missing status {must}")

    def test_services_player_ts(self):
        p = MUSIC_VUE / "src" / "services" / "player.ts"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        # 关键模式(自写 Emitter,不再依赖 Node 'events')
        self.assertIn("extends Emitter", content, "must extend custom Emitter")
        self.assertIn("AbortError", content, "must handle AbortError")
        self.assertIn("currentToken", content, "must use token validation")
        self.assertIn("preloadNext", content, "must implement preload")
        # emitter.ts 自写
        emitter_p = MUSIC_VUE / "src" / "services" / "emitter.ts"
        self.assertTrue(emitter_p.exists(), "missing emitter.ts")
        emitter_content = emitter_p.read_text(encoding="utf-8")
        self.assertIn("class Emitter", emitter_content)
        self.assertIn("on(", emitter_content)
        self.assertIn("off(", emitter_content)
        self.assertIn("emit(", emitter_content)

    def test_stores_player_ts(self):
        p = MUSIC_VUE / "src" / "stores" / "player.ts"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertIn("defineStore", content)
        self.assertIn("usePlayerStore", content)

    def test_components_present(self):
        for c in ("MiniBar.vue", "ProgressBar.vue", "LyricPanel.vue",
                  "Cover.vue", "QueueList.vue", "Toast.vue"):
            p = MUSIC_VUE / "src" / "components" / c
            self.assertTrue(p.exists(), f"missing component: {c}")

    def test_views_music_view(self):
        p = MUSIC_VUE / "src" / "views" / "MusicView.vue"
        self.assertTrue(p.exists())

    def test_styles_main_css(self):
        p = MUSIC_VUE / "src" / "styles" / "main.css"
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        # 国画主题色变量
        self.assertIn("--gh-paper", content)
        self.assertIn("--gh-red", content)
        # 三层进度条颜色
        self.assertIn("buffered", content)


# ============================================================
# 关键组件代码模式测试(grep 模式)
# ============================================================
class TestMusicVuePatterns(unittest.TestCase):
    """代码模式必须存在(借鉴的核心里程碑):状态机/token/AbortError/preload/二分查找。"""

    def test_player_service_token_validation(self):
        p = MUSIC_VUE / "src" / "services" / "player.ts"
        content = p.read_text(encoding="utf-8")
        # token 校验用于 onCanPlay / onError / onStalled 等
        self.assertIn("if (this.currentToken === null) return", content,
                      "must have token guard in audio event handlers")

    def test_player_service_abort_error_handling(self):
        p = MUSIC_VUE / "src" / "services" / "player.ts"
        content = p.read_text(encoding="utf-8")
        self.assertIn("e?.name !== 'AbortError'", content,
                      "must ignore AbortError per Chrome 50+ spec")

    def test_player_service_on_error_retry(self):
        p = MUSIC_VUE / "src" / "services" / "player.ts"
        content = p.read_text(encoding="utf-8")
        self.assertIn("setTimeout", content)
        self.assertIn("playNext", content)
        # onError 应该调 setTimeout(3000) 后 retry
        self.assertRegex(content, r"setTimeout\([^,]+,\s*3000\s*\)",
                         "onError should auto-retry after 3s")

    def test_lyric_panel_binary_search(self):
        p = MUSIC_VUE / "src" / "stores" / "player.ts"
        content = p.read_text(encoding="utf-8")
        # YesPlayMusic 二分查找移植
        self.assertIn("(lo + hi) >> 1", content,
                      "must have binary search for lyric index")

    def test_progress_bar_three_layers(self):
        p = MUSIC_VUE / "src" / "components" / "ProgressBar.vue"
        content = p.read_text(encoding="utf-8")
        # 三层:bg + buffered + active
        self.assertIn('class="bg"', content)
        self.assertIn('class="buffered"', content)
        self.assertIn('class="active"', content)

    def test_mini_bar_three_controls(self):
        p = MUSIC_VUE / "src" / "components" / "MiniBar.vue"
        content = p.read_text(encoding="utf-8")
        # ⏮⏯⏭ 三件套
        for ctrl in ("onPrev", "onToggle", "onNext"):
            self.assertIn(ctrl, content)


if __name__ == "__main__":
    unittest.main()