"""
test_music_favorite_menu.py — P3.3(2026-10-03)N7 长按收藏菜单测试。

覆盖:
  - 后端 Player.list_favorites / remove_favorite 单元(过滤 source / 删除校验)
  - 后端 /api/favorites GET 端点(空 / 有)
  - 后端 /api/favorites/remove POST 端点(按 fav_id 删 / 不存在返 err)
  - 前端 PopupMenu.vue 模板(items prop + select/close emit + danger class + mousedown.stop)
  - 前端 MiniBar.vue ♥/♡ 加 @contextmenu + touchstart/touchend 长按 600ms + 4 项菜单
  - 前端 Toast.vue 加 list 类型分支(.list-title / .list-items)
  - 前端 ui.ts pushListToast 实现
"""
from __future__ import annotations

import asyncio
import json
import re
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

from music.player import LocalLibrary, Player, Track  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
# Player.list_favorites / remove_favorite 单元
# ============================================================
class TestPlayerListFavorites(unittest.TestCase):
    """P3.3(2026-10-03):list_favorites 仅返 source='song_pool_fav'。"""

    def test_empty_library_returns_empty(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        self.assertEqual(p.list_favorites(), [])

    def test_filters_only_song_pool_fav(self):
        lib = LocalLibrary(music_root=None)
        # 3 首歌:1 个 favorite + 1 个 lx + 1 个 local
        lib.add_track(Track(id="a1", title="晴天", artist="周杰伦",
                            album="", path="x", source="lx:mock.js"))
        lib.add_track(Track(id="b1", title="一程山路", artist="毛不易",
                            album="", path="x", source="lx:mock.js"))
        lib.add_track(Track(id="c1", title="本地歌", artist="t",
                            album="", path="x", source="local"))
        p = Player(lib, online=None, catalog=None)
        # 收藏 a1
        _run(p._cmd_favorite("a1"))
        favs = p.list_favorites()
        self.assertEqual(len(favs), 1)
        self.assertEqual(favs[0].source, "song_pool_fav")
        self.assertEqual(favs[0].title, "晴天")
        self.assertEqual(favs[0].artist, "周杰伦")

    def test_limit_default_50(self):
        lib = LocalLibrary(music_root=None)
        # 加 60 首 favorite
        for i in range(60):
            lib.add_track(Track(id=f"fav_{i}", title=f"歌{i}", artist="a",
                                album="", path="x", source="song_pool_fav"))
        p = Player(lib, online=None, catalog=None)
        favs = p.list_favorites()
        self.assertEqual(len(favs), 50, msg="默认 limit=50")


class TestPlayerRemoveFavorite(unittest.TestCase):
    """P3.3(2026-10-03):remove_favorite 按 fav_id 删 + 校验 source。"""

    def test_remove_existing_favorite(self):
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(id="x", title="晴天", artist="周杰伦",
                            album="", path="p", source="lx:mock.js"))
        p = Player(lib, online=None, catalog=None)
        # 收藏
        r = _run(p._cmd_favorite("x"))
        self.assertTrue(r["ok"])
        fav_id = r["fav_id"]
        self.assertIsNotNone(lib.get(fav_id))
        # 删除
        rm = p.remove_favorite(fav_id)
        self.assertTrue(rm["ok"])
        self.assertEqual(rm["removed"]["id"], fav_id)
        self.assertIsNone(lib.get(fav_id))

    def test_remove_missing_id(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        r = p.remove_favorite("nonexistent")
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["err"])

    def test_remove_non_favorite_track_rejected(self):
        """P3.3(2026-10-03):非 song_pool_fav source 不能删(避免误删 lx/local 源)。"""
        lib = LocalLibrary(music_root=None)
        lib.add_track(Track(id="lx_1", title="远端歌", artist="a",
                            album="", path="p", source="lx:mock.js"))
        p = Player(lib, online=None, catalog=None)
        r = p.remove_favorite("lx_1")
        self.assertFalse(r["ok"])
        self.assertIn("not a favorite", r["err"])
        # track 仍存在
        self.assertIsNotNone(lib.get("lx_1"))

    def test_remove_empty_id_rejected(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        r = p.remove_favorite("")
        self.assertFalse(r["ok"])
        self.assertIn("missing", r["err"])


# ============================================================
# /api/favorites 端点(走 aiohttp handler + make_mocked_request)
# ============================================================
class TestApiFavoritesEndpoint(unittest.TestCase):
    """P3.3(2026-10-03):/api/favorites GET 应返 count + favorites[]."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "prisIragent_music_web", str(COMPANION / "prisIragent-music-web.py"))
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        self._mod = _mod
        APP = _mod.APP
        LocalLibrary = _mod.LocalLibrary
        APP.library = LocalLibrary(music_root=None)
        from music.player import Player
        APP.player = Player(APP.library, online=None, catalog=None)
        APP.cfg = None
        APP.lyric = None
        APP.ws_lock = None

    def tearDown(self):
        APP = self._mod.APP
        APP.library = None
        APP.player = None

    def _run_handler(self, handler, query=""):
        from aiohttp.test_utils import make_mocked_request
        path = "/"
        if query:
            q = query.lstrip("?")
            path = f"/?{q}"
        req = make_mocked_request("GET", path)
        resp = _run(handler(req))
        return json.loads(resp.body.decode("utf-8"))

    def test_empty_returns_zero(self):
        data = self._run_handler(self._mod.api_favorites, "")
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["favorites"], [])

    def test_with_one_favorite(self):
        APP = self._mod.APP
        APP.library.add_track(Track(
            id="t1", title="晴天", artist="周杰伦",
            album="", path="x", source="lx:mock.js"))
        _run(APP.player._cmd_favorite("t1"))
        data = self._run_handler(self._mod.api_favorites, "")
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["favorites"]), 1)
        f = data["favorites"][0]
        self.assertEqual(f["title"], "晴天")
        self.assertEqual(f["artist"], "周杰伦")
        self.assertIn("fav_id", f)
        self.assertEqual(len(f["fav_id"]), 16, msg="fav_id 是 sha1 截 16")


class TestApiFavoriteRemoveEndpoint(unittest.TestCase):
    """P3.3(2026-10-03):/api/favorites/remove POST 按 fav_id 删。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "prisIragent_music_web", str(COMPANION / "prisIragent-music-web.py"))
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        self._mod = _mod
        APP = _mod.APP
        LocalLibrary = _mod.LocalLibrary
        APP.library = LocalLibrary(music_root=None)
        from music.player import Player
        APP.player = Player(APP.library, online=None, catalog=None)
        APP.cfg = None
        APP.lyric = None
        APP.ws_lock = None

    def tearDown(self):
        APP = self._mod.APP
        APP.library = None
        APP.player = None

    def _run_handler_post(self, handler, query=""):
        from aiohttp.test_utils import make_mocked_request
        path = "/"
        if query:
            q = query.lstrip("?")
            path = f"/?{q}"
        req = make_mocked_request("POST", path)
        resp = _run(handler(req))
        return json.loads(resp.body.decode("utf-8"))

    def test_remove_existing_returns_ok(self):
        APP = self._mod.APP
        APP.library.add_track(Track(
            id="t1", title="A", artist="B", album="", path="x", source="lx:mock.js"))
        r = _run(APP.player._cmd_favorite("t1"))
        fav_id = r["fav_id"]
        data = self._run_handler_post(self._mod.api_favorite_remove, f"?fav_id={fav_id}")
        self.assertTrue(data["ok"])
        self.assertEqual(data["removed"]["id"], fav_id)
        # 再 get 应空
        from aiohttp.test_utils import make_mocked_request
        req = make_mocked_request("GET", "/")
        resp = _run(self._mod.api_favorites(req))
        d2 = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(d2["count"], 0)

    def test_remove_nonexistent_returns_err(self):
        data = self._run_handler_post(self._mod.api_favorite_remove, "?fav_id=nope")
        self.assertFalse(data["ok"])
        self.assertIn("not found", data["err"])

    def test_remove_missing_id_returns_err(self):
        data = self._run_handler_post(self._mod.api_favorite_remove, "")
        self.assertFalse(data["ok"])
        self.assertIn("missing", data["err"])


# ============================================================
# 前端 PopupMenu.vue 组件
# ============================================================
class TestPopupMenuComponent(unittest.TestCase):
    """P3.3(2026-10-03):PopupMenu.vue 模板 + 接口签名。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "components" / "PopupMenu.vue").read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue((MUSIC_VUE / "src" / "components" / "PopupMenu.vue").exists(),
                        "PopupMenu.vue must exist")

    def test_has_menuitem_interface(self):
        c = self._read()
        self.assertIn("interface MenuItem", c)
        self.assertIn("key:", c)
        self.assertIn("label:", c)
        self.assertIn("danger?", c)
        self.assertIn("hidden?", c)

    def test_has_props(self):
        c = self._read()
        self.assertIn("x: number", c)
        self.assertIn("y: number", c)
        self.assertIn("items: MenuItem[]", c)
        self.assertIn("visible: boolean", c)

    def test_has_emits(self):
        c = self._read()
        self.assertIn("'select'", c)
        self.assertIn("'close'", c)

    def test_renders_menu_items(self):
        c = self._read()
        self.assertIn("v-for=\"item in visibleItems\"", c)
        self.assertIn("class=\"menu-item\"", c)
        self.assertIn(":class=\"{ danger: item.danger }\"", c)
        self.assertIn("@click=\"onPick(item)\"", c)

    def test_click_outside_close(self):
        c = self._read()
        # document mousedown listener
        self.assertIn("document.addEventListener('mousedown', onDocMouseDown)", c)
        self.assertIn("emit('close')", c)

    def test_esc_key_close(self):
        c = self._read()
        self.assertIn("'keydown'", c)
        self.assertIn("'Escape'", c)

    def test_mousedown_stop_propagation(self):
        """P3.3(2026-10-03):popup 内 mousedown 不能冒泡触发 click-outside 关菜单。"""
        c = self._read()
        self.assertIn("@mousedown.stop", c, "popup must @mousedown.stop")

    def test_viewport_clamp(self):
        """P3.3(2026-10-03):popup 必须 clamp 到 viewport(防止越界)。"""
        c = self._read()
        self.assertIn("window.innerWidth", c)
        self.assertIn("window.innerHeight", c)

    def test_filter_hidden(self):
        """hidden 字段为 true 的菜单项不渲染。"""
        c = self._read()
        self.assertIn("filter((i) => !i.hidden)", c)


# ============================================================
# 前端 MiniBar.vue ♡/♥ 按钮 + 长按/contextmenu
# ============================================================
class TestMiniBarFavoriteMenu(unittest.TestCase):
    """P3.3(2026-10-03):MiniBar ♡/♥ 按钮加 contextmenu + touch 长按 600ms。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "components" / "MiniBar.vue").read_text(encoding="utf-8")

    def test_contextmenu_handler(self):
        c = self._read()
        self.assertIn("@contextmenu", c, "♥/♡ 按钮必须绑 @contextmenu")
        self.assertIn("onFavContextMenu", c)
        self.assertIn("e.preventDefault()", c, "必须 preventDefault 阻止原生菜单")

    def test_touch_long_press_handler(self):
        c = self._read()
        self.assertIn("@touchstart", c, "♥/♡ 按钮必须绑 @touchstart")
        self.assertIn("@touchend", c, "♥/♡ 按钮必须绑 @touchend")
        self.assertIn("LONG_PRESS_MS", c)
        # 600ms 阈值(借鉴落雪 800ms 改紧凑)
        m = re.search(r"LONG_PRESS_MS\s*=\s*(\d+)", c)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 500, "长按阈值至少 500ms")
        self.assertLessEqual(int(m.group(1)), 1000, "长按阈值不超过 1000ms")

    def test_setTimeout_in_touch_handler(self):
        c = self._read()
        self.assertIn("setTimeout", c)
        self.assertIn("clearTimeout", c)

    def test_click_no_double_fire(self):
        """长按命中菜单时,后续 click 不能触发 toggleFavorite。"""
        c = self._read()
        self.assertIn("longPressTriggered", c)
        # onFavClick 应检查 longPressTriggered
        self.assertRegex(c, r"if\s*\(\s*longPressTriggered\s*\)")

    def test_four_menu_items(self):
        c = self._read()
        # 4 项:copy / play / list / unfav
        self.assertIn("key: 'copy'", c)
        self.assertIn("key: 'play'", c)
        self.assertIn("key: 'list'", c)
        self.assertIn("key: 'unfav'", c)
        self.assertIn("复制曲名", c)
        self.assertIn("立即播放", c)
        self.assertIn("查看所有收藏", c)
        self.assertIn("取消收藏", c)

    def test_unfav_hidden_when_not_favorite(self):
        c = self._read()
        # 「取消收藏」hidden 字段绑 !player.isFavorite
        self.assertIn("hidden: !player.isFavorite", c)

    def test_danger_class(self):
        """P3.3(2026-10-03):取消收藏 danger=true(红色)"""
        c = self._read()
        self.assertIn("danger: true", c)

    def test_onSelectMenu_branches(self):
        c = self._read()
        self.assertIn("if (item.key === 'copy')", c)
        self.assertIn("if (item.key === 'play')", c)
        self.assertIn("if (item.key === 'list')", c)
        self.assertIn("if (item.key === 'unfav')", c)

    def test_clipboard_fallback(self):
        c = self._read()
        self.assertIn("navigator.clipboard.writeText", c, "复制走 Clipboard API")
        # 失败时 toast warn
        self.assertIn("'warn'", c)
        self.assertIn("复制失败", c)

    def test_listFavorites_action_called(self):
        c = self._read()
        self.assertIn("player.listFavorites()", c, "「查看所有收藏」调 store action")
        self.assertIn("ui.pushListToast", c, "调 ui 列表 toast")

    def test_popupmenu_component_used(self):
        c = self._read()
        self.assertIn("import PopupMenu", c)
        self.assertIn("<PopupMenu", c)
        self.assertIn(":x=\"popupX\"", c)
        self.assertIn(":y=\"popupY\"", c)
        self.assertIn(":visible=\"popupVisible\"", c)


# ============================================================
# 前端 Toast.vue list 类型分支
# ============================================================
class TestToastListType(unittest.TestCase):
    """P3.3(2026-10-03):Toast.vue 加 list 类型分支(收藏列表 8s TTL)。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "components" / "Toast.vue").read_text(encoding="utf-8")

    def test_list_type_branch(self):
        c = self._read()
        self.assertIn("t.type === 'list'", c)
        self.assertIn("list-title", c)
        self.assertIn("list-items", c)
        self.assertIn(".list-title", c)
        self.assertIn(".list-items", c)

    def test_list_toast_styling(self):
        c = self._read()
        # list 类型用 paper 色背景 + ink 文字
        self.assertIn(".toast.list-toast", c)
        self.assertIn("--gh-paper", c)


# ============================================================
# 前端 ui.ts pushListToast
# ============================================================
class TestUiPushListToast(unittest.TestCase):
    """P3.3(2026-10-03):ui store 加 pushListToast + ListToastMsg 接口。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "stores" / "ui.ts").read_text(encoding="utf-8")

    def test_interface_defined(self):
        c = self._read()
        self.assertIn("interface ListToastMsg", c)
        self.assertIn("type: 'list'", c)
        self.assertIn("title: string", c)
        self.assertIn("items: { title: string; subtitle?: string }[]", c)

    def test_function_defined(self):
        c = self._read()
        self.assertIn("function pushListToast", c)
        # 默认 TTL 8000(用更宽的正则匹配跨行)— 必须是 pushListToast 函数体里的 ttlMs
        # 函数签名:pushListToast(title, items, ttlMs = 8000)
        m = re.search(r"pushListToast\s*\(\s*title\s*:[^)]*ttlMs\s*=\s*(\d+)", c, re.DOTALL)
        self.assertIsNotNone(m, "pushListToast 默认 ttlMs 应为 8000")
        self.assertEqual(int(m.group(1)), 8000)

    def test_returned_in_store(self):
        c = self._read()
        self.assertIn("pushListToast,", c, "must be exposed in store return")


# ============================================================
# 前端 player.ts listFavorites / removeFavorite action
# ============================================================
class TestPlayerStoreFavoriteActions(unittest.TestCase):
    """P3.3(2026-10-03):player store 加 listFavorites + removeFavorite action。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "stores" / "player.ts").read_text(encoding="utf-8")

    def test_listFavorites_function(self):
        c = self._read()
        self.assertIn("async function listFavorites", c)
        self.assertIn("/api/favorites", c)

    def test_removeFavorite_function(self):
        c = self._read()
        self.assertIn("async function removeFavorite", c)
        self.assertIn("/api/favorites/remove", c)
        self.assertIn("fav_id=", c)

    def test_returned_in_store(self):
        c = self._read()
        # 必须在 return 中暴露
        self.assertRegex(c, r"listFavorites,\s*removeFavorite")


# ============================================================
# 后端 router 注册
# ============================================================
class TestRoutesRegistered(unittest.TestCase):
    """P3.3(2026-10-03):prisIragent-music-web.py router 必须注册 2 个 favorites 端点。"""

    def _read(self):
        return (COMPANION / "prisIragent-music-web.py").read_text(encoding="utf-8")

    def test_favorites_route(self):
        c = self._read()
        self.assertIn("add_get(\"/api/favorites\", api_favorites)", c)

    def test_remove_route(self):
        c = self._read()
        self.assertIn("add_post(\"/api/favorites/remove\", api_favorite_remove)", c)


if __name__ == "__main__":
    unittest.main()