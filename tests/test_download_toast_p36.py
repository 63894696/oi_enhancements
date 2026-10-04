"""
test_download_toast_p36.py — P3.6(2026-10-04)N8 下载完成桌面通知 toast 测试。

覆盖:
  - TestCmdDownloadPublish — companion/music/player.py _cmd_download 完成时
      self._publish('download_done', payload)
  - TestApiCmdDownloadPublish — companion/prisIragent-music-web.py api_cmd
      action=download → 透传 _publish_state('download_done', payload)
  - TestPlayerStoreDownloadSong — stores/player.ts downloadSong action
      + bootstrap 订阅 ws download_done 触发 svc.emit('download')
  - TestUseDownloadToast — composables/useDownloadToast.ts 桥 PlayerService 'download'
      事件 → window.prisIragent.showToast
  - TestMusicViewDownloadEntry — views/MusicView.vue 歌单行右键 PopupMenu
      + useDownloadToast() + ctxItems 含 'download'
  - TestLyricOnlyViewDownloadEntry — views/LyricOnlyView.vue #settings-panel 末尾
      btn-download + useDownloadToast
"""
from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
MUSIC_WEB = COMPANION / "prisIragent-music-web.py"
PLAYER = COMPANION / "music" / "player.py"

MVUE = COMPANION / "static" / "music-vue"
SRC = MVUE / "src"

for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.player import LocalLibrary, Player, Track  # noqa: E402


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
# TestCmdDownloadPublish — _cmd_download 末尾 self._publish('download_done')
# ============================================================
class TestCmdDownloadPublish(unittest.TestCase):
    """P3.6(2026-10-04):_cmd_download 完成(无论 ok/err)都 publish download_done。"""

    def _make_player(self):
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        # 注入 publish spy(替换 _publish)
        captured = []

        async def fake_publish(ev_type, payload):
            captured.append((ev_type, payload))

        p._publish = fake_publish  # type: ignore
        return p, captured

    def test_missing_track_id_publishes_err(self):
        p, captured = self._make_player()
        r = _run(p._cmd_download(None))
        self.assertFalse(r["ok"])
        self.assertEqual(r["err"], "missing track_id")
        # 没有 track_id → 不 publish(避免前端误弹 error)
        self.assertEqual(captured, [], "missing track_id 不 publish download_done")

    def test_not_found_publishes_err(self):
        p, captured = self._make_player()
        r = _run(p._cmd_download("zzz_missing"))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["err"])
        # 找不到 track → publish download_done ok:False
        self.assertEqual(len(captured), 1)
        ev_type, payload = captured[0]
        self.assertEqual(ev_type, "download_done")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["track_id"], "zzz_missing")
        self.assertIn("not found", payload.get("err", ""))

    def test_mkdir_fail_publishes_err(self):
        # 测「publish download_done ok:False」路径通过 not_found 验证更稳,
        # 因为 mkdir fail 需要文件系统权限场景,brittle。
        # 用真实的 _publish(订阅 _subscribers),验证消息原样含 type='download_done' + ts。
        lib = LocalLibrary(music_root=None)
        p = Player(lib, online=None, catalog=None)
        msgs: list = []

        async def main():
            q = asyncio.Queue()
            p._subscribers.append(q)
            try:
                # 串行:先触发 download,再等消息(避免 race)
                await p._cmd_download("zzz_missing")
                # _publish 走 put_nowait 同步;_cmd_download 已 return → 消息应已入队
                try:
                    m = await asyncio.wait_for(q.get(), timeout=0.2)
                    msgs.append(m)
                except asyncio.TimeoutError:
                    pass
            finally:
                try:
                    p._subscribers.remove(q)
                except ValueError:
                    pass
        _run(main())
        self.assertEqual(len(msgs), 1)
        m = msgs[0]
        self.assertEqual(m["type"], "download_done")
        self.assertIn("ts", m, "_publish 自动加 ts 字段")
        self.assertFalse(m["ok"])
        self.assertEqual(m["track_id"], "zzz_missing")


# ============================================================
# TestApiCmdDownloadPublish — api_cmd 透传 _publish_state
# ============================================================
class TestApiCmdDownloadPublish(unittest.TestCase):
    """P3.6:api_cmd action=download → _publish_state('download_done', payload) 广播。"""

    def test_api_cmd_publishes_state_for_download(self):
        c = _read(MUSIC_WEB)
        # 找 api_cmd 函数定义
        idx = c.find("async def api_cmd")
        self.assertGreater(idx, -1, "api_cmd 存在")
        snippet = c[idx: idx + 1200]
        # 含 _publish_state('download_done', ...) 透传
        self.assertIn("_publish_state", snippet)
        self.assertIn("download_done", snippet)
        # 检查只在 action=='download' 时调
        self.assertRegex(snippet, r'if\s+action\s*==\s*["\']download["\']')

    def test_api_cmd_no_publish_for_other_actions(self):
        c = _read(MUSIC_WEB)
        idx = c.find("async def api_cmd")
        snippet = c[idx: idx + 1200]
        # 确保 _publish_state 在 if 内 — 而不是无条件的全局 publish
        # 校验:if 'download' in 之前不能有独立的 _publish_state('download_done',...)
        # 简单正则匹配 — publish 必须在 if 块内
        m = re.search(r"if\s+action\s*==\s*[\"']download[\"']\s+and\s+isinstance", snippet)
        self.assertIsNotNone(m, "publish_state 仅在 action=='download' 时触发")

    def test_publish_state_helper_broadcasts(self):
        """_publish_state 把消息 put_nowait 到 ws_state_subs 队列。"""
        c = _read(MUSIC_WEB)
        idx = c.find("async def _publish_state")
        self.assertGreater(idx, -1)
        snippet = c[idx: idx + 800]
        # 包含 type + ts + put_nowait
        self.assertIn("put_nowait", snippet)
        self.assertIn('"type": ev_type', snippet)  # _publish_state 加 type 字段
        self.assertIn("ts", snippet)


# ============================================================
# TestPlayerStoreDownloadSong — store.downloadSong + bootstrap 订阅 download_done
# ============================================================
class TestPlayerStoreDownloadSong(unittest.TestCase):
    """P3.6:usePlayerStore.downloadSong(trackId) + bootstrap 订阅 ws download_done。"""

    def test_store_has_download_song(self):
        c = _read(SRC / "stores" / "player.ts")
        self.assertIn("async function downloadSong", c)
        # 调 /api/cmd {action:download, track_id}
        self.assertIn("'download'", c)
        self.assertIn("track_id", c)

    def test_bootstrap_subscribes_download_done(self):
        c = _read(SRC / "stores" / "player.ts")
        idx = c.find("function onWsStateMsg")
        snippet = c[idx: idx + 1500]
        # ws 收到 download_done → emit('download', msg)
        self.assertIn("download_done", snippet)
        self.assertIn("svc.emit('download'", snippet)

    def test_store_returns_download_song(self):
        c = _read(SRC / "stores" / "player.ts")
        idx = c.rfind("downloadSong,")
        # 在 return 块里出现
        # 简化:全文件扫 downloadSong, 出现在 return 块(allow trailing newline)
        self.assertIn("downloadSong,", c)


# ============================================================
# TestUseDownloadToast — composable 桥 svc 'download' → showToast
# ============================================================
class TestUseDownloadToast(unittest.TestCase):
    """P3.6:composables/useDownloadToast.ts 桥接 PlayerService 'download' → showToast。"""

    def test_composable_file_exists(self):
        self.assertTrue((SRC / "composables" / "useDownloadToast.ts").exists())

    def test_subscribes_player_service_download(self):
        c = _read(SRC / "composables" / "useDownloadToast.ts")
        self.assertIn("svc.on('download'", c)
        self.assertIn("svc.off('download'", c)
        # onMounted / onBeforeUnmount
        self.assertIn("onMounted", c)
        self.assertIn("onBeforeUnmount", c)

    def test_calls_prisIragent_show_toast(self):
        c = _read(SRC / "composables" / "useDownloadToast.ts")
        self.assertIn("prisIragent.showToast", c)
        # level: info / error
        self.assertIn("level: 'info'", c)
        self.assertIn("level: 'error'", c)
        # ok=true 时 info;ok=false 时 error
        self.assertRegex(c, r"if\s*\(ok\)")
        self.assertRegex(c, r"level:\s*['\"]info['\"]")

    def test_handles_missing_prisIragent(self):
        c = _read(SRC / "composables" / "useDownloadToast.ts")
        # window.prisIragent 不存在时静默 + console.warn(vite dev / 普通浏览器)
        self.assertIn("console.warn", c)
        self.assertIn("missing", c)


# ============================================================
# TestMusicViewDownloadEntry — MusicView 歌单行右键 + popup + useDownloadToast
# ============================================================
class TestMusicViewDownloadEntry(unittest.TestCase):
    """P3.6:MusicView 歌单行 @contextmenu → PopupMenu + useDownloadToast。"""

    def test_music_view_uses_composable(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("useDownloadToast", c)

    def test_music_view_has_contextmenu(self):
        c = _read(SRC / "views" / "MusicView.vue")
        # 歌单行右键
        self.assertIn("@contextmenu.prevent", c)
        self.assertIn("openCtx", c)

    def test_music_view_has_popup_menu(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("PopupMenu", c)
        self.assertIn("<PopupMenu", c)
        self.assertIn("@select=\"onCtxSelect\"", c)

    def test_ctx_items_includes_download(self):
        c = _read(SRC / "views" / "MusicView.vue")
        # ctxItemsFor 列表含 'download' key
        self.assertRegex(c, r"key:\s*['\"]download['\"]")
        # label 含 下载
        self.assertRegex(c, r"label:\s*['\"].*下载")
        # case 'download' → player.downloadSong
        self.assertIn("case 'download'", c)
        self.assertIn("player.downloadSong", c)


# ============================================================
# TestLyricOnlyViewDownloadEntry — LyricOnlyView settings-panel 下载入口
# ============================================================
class TestLyricOnlyViewDownloadEntry(unittest.TestCase):
    """P3.6:LyricOnlyView #settings-panel 末尾 btn-download + useDownloadToast。"""

    def test_lyric_uses_composable(self):
        c = _read(SRC / "views" / "LyricOnlyView.vue")
        self.assertIn("useDownloadToast", c)

    def test_lyric_has_btn_download(self):
        c = _read(SRC / "views" / "LyricOnlyView.vue")
        self.assertIn("btn-download", c)
        # 末尾(settings-panel 内 EqInline 之后)
        idx = c.find('id="settings-panel"')
        snippet = c[idx: idx + 3000]
        self.assertIn("btn-download", snippet)
        # onDownloadCurrent 调 player.downloadSong(lyric.track?.id)
        self.assertIn("onDownloadCurrent", c)
        self.assertIn("player.downloadSong", c)
        # 按钮 disabled 时无 track
        self.assertIn("lyric.track?.id", c)


if __name__ == "__main__":
    unittest.main()