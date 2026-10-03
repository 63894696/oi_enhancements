"""
test_music_lyric_vue_p26.py — P2.5+26(2026-10-03)桌面歌词窗状态测试。

覆盖:
  - TestLyricStateIO — userData/lyric-window-state.json 读写 + 损坏容错 + 落盘
  - TestStateApply — _lyric_apply_state 真调 mock electron window
  - TestIPC — 4 新 IPC handler 存在 + 路径校验
  - TestTraySubmenu — 「🎤 桌面歌词」 改 submenu + 5 子项 + 2 checkbox
  - TestLyricCssLocked — lyric.css 加 .lyric-locked #drag-bar no-drag
  - TestLyricViewBootstrap — LyricOnlyView 加 getLyricState + onLyricStateChanged + class 绑定
  - TestPreloadP26 — preload.js 暴露 4 新方法 + onLyricStateChanged
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MUSIC_VUE = ROOT / "companion" / "static" / "music-vue"
SHELL = ROOT / "prisiragent-shell"


# ============================================================
# 主进程 lyric_state IO
# ============================================================
class TestLyricStateIO(unittest.TestCase):
    """P2.5+26(2026-10-03):_lyric_state_load/_save 真函数 grep + 默认值。"""

    def _read_main(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_state_path_uses_userdata(self):
        """_lyric_state_path 必须走 app.getPath('userData')。"""
        content = self._read_main()
        self.assertIn("_lyric_state_path", content, "missing _lyric_state_path")
        self.assertIn('app.getPath("userData")', content,
                      "must use app.getPath('userData') for cross-platform")
        self.assertIn("lyric-window-state.json", content,
                      "must use lyric-window-state.json filename")

    def test_state_load_function_exists(self):
        """_lyric_state_load 必须有 — load 时校验字段 + 坏 JSON 兜底默认。"""
        content = self._read_main()
        self.assertIn("function _lyric_state_load", content)
        # 必返 alwaysOnTop / lockDrag / bounds 三键
        # 用 regex 抓函数体内 literal
        m = re.search(r"function _lyric_state_load[^{]*\{(.+?)^\}", content, re.S | re.M)
        self.assertIsNotNone(m, "could not extract _lyric_state_load body")
        body = m.group(1)
        for k in ("alwaysOnTop", "lockDrag", "bounds"):
            self.assertIn(k, body, f"_lyric_state_load must default key {k}")

    def test_state_save_function_exists(self):
        content = self._read_main()
        self.assertIn("function _lyric_state_save", content)
        self.assertIn("writeFileSync", content, "_lyric_state_save must use writeFileSync")
        self.assertIn("JSON.stringify", content, "_lyric_state_save must JSON serialize")

    def test_state_corrupt_backup(self):
        """JSON 损坏必须备份 .corrupt-<timestamp> 不让用户卡死。"""
        content = self._read_main()
        self.assertIn("corrupt-", content,
                      "missing corrupt backup pattern (Date.now() rename)")
        self.assertIn("renameSync", content,
                      "must renameSync corrupt file aside")

    def test_state_module_level_init(self):
        """模块加载即初始化 _lyric_state = _lyric_state_load()(不能 lazy init)。"""
        content = self._read_main()
        self.assertRegex(
            content,
            r"let _lyric_state\s*=\s*_lyric_state_load",
            "must init _lyric_state at module load (not lazy)",
        )


# ============================================================
# _lyric_apply_state
# ============================================================
class TestStateApply(unittest.TestCase):
    """P2.5+26(2026-10-03):_lyric_apply_state 真调 setAlwaysOnTop + setBounds。"""

    def _read_main(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_apply_function_exists(self):
        content = self._read_main()
        self.assertIn("function _lyric_apply_state", content)

    def test_apply_calls_set_always_on_top(self):
        content = self._read_main()
        m = re.search(r"function _lyric_apply_state[^{]*\{(.+?)^\}", content, re.S | re.M)
        self.assertIsNotNone(m, "could not extract _lyric_apply_state body")
        body = m.group(1)
        self.assertIn("setAlwaysOnTop", body, "must call setAlwaysOnTop")
        self.assertIn("floating", body, "Win 需要 level='floating' 才稳定")

    def test_apply_calls_set_bounds(self):
        content = self._read_main()
        m = re.search(r"function _lyric_apply_state[^{]*\{(.+?)^\}", content, re.S | re.M)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("setBounds", body, "must call setBounds when x/y finite")

    def test_create_child_window_lyric_branch(self):
        """_createChildWindow 在 spec.label === 'lyric' 分支必 apply + 监听 move/resize 落盘。"""
        content = self._read_main()
        # 必须含 label === "lyric" 分支
        self.assertIn('spec.label === "lyric"', content,
                      "_createChildWindow must have lyric-only branch")
        # 必须 apply + move/resize debounce + _lyric_state_save
        idx = content.find('_createChildWindow')
        # 抓函数尾 — 用 try to find matching brace; 简单做法:抓全文,grep
        for kw in ("_lyric_apply_state", 'w.on("move"', 'w.on("resize"', "_lyric_state_save"):
            self.assertIn(kw, content, f"_createChildWindow lyric branch missing {kw}")

    def test_bounds_persistence_debounce(self):
        """move/resize 必须 setTimeout debounce,不允许高频落盘。"""
        content = self._read_main()
        self.assertIn("clearTimeout", content, "must debounce move/resize with clearTimeout")
        # 250ms 或类似 ms 阈值
        self.assertRegex(content, r"setTimeout\([^,]+,\s*\d{2,4}\)",
                         "must debounce with setTimeout(_, Nms)")


# ============================================================
# 4 新 IPC handler
# ============================================================
class TestIPC(unittest.TestCase):
    """P2.5+26(2026-10-03):4 新 IPC handler — toggleAlwaysOnTop / toggleLockDrag / getState / setBounds。"""

    def _read_main(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_toggle_always_on_top_ipc(self):
        content = self._read_main()
        self.assertIn('"shell:toggleLyricAlwaysOnTop"', content,
                      "missing shell:toggleLyricAlwaysOnTop handler")
        # 必须调 _toggleLyricAlwaysOnTop
        idx = content.find('"shell:toggleLyricAlwaysOnTop"')
        self.assertGreater(idx, 0)
        # 后面 800 字符内必含 _toggleLyricAlwaysOnTop()
        snippet = content[idx: idx + 600]
        self.assertIn("_toggleLyricAlwaysOnTop()", snippet)

    def test_toggle_lock_drag_ipc(self):
        content = self._read_main()
        self.assertIn('"shell:toggleLyricLockDrag"', content)
        idx = content.find('"shell:toggleLyricLockDrag"')
        snippet = content[idx: idx + 600]
        self.assertIn("_toggleLyricLockDrag()", snippet)

    def test_get_lyric_state_ipc(self):
        content = self._read_main()
        self.assertIn('"shell:getLyricState"', content)
        # 必须返 alwaysOnTop / lockDrag / bounds 三字段
        idx = content.find('"shell:getLyricState"')
        snippet = content[idx: idx + 600]
        for k in ("alwaysOnTop", "lockDrag", "bounds"):
            self.assertIn(k, snippet, f"shell:getLyricState must return {k}")

    def test_set_lyric_bounds_ipc(self):
        content = self._read_main()
        self.assertIn('"shell:setLyricBounds"', content)
        idx = content.find('"shell:setLyricBounds"')
        snippet = content[idx: idx + 800]
        # 必须校验 w >= 480 + h >= 240(防太小)
        self.assertIn(">=480", snippet, "must reject w < 480")
        self.assertIn(">=240", snippet, "must reject h < 240")
        # 必须 _lyric_state_save
        self.assertIn("_lyric_state_save", snippet)


# ============================================================
# 托盘 submenu 改造
# ============================================================
class TestTraySubmenu(unittest.TestCase):
    """P2.5+26(2026-10-03):「🎤 桌面歌词」 改 submenu + 5 子项 + 2 checkbox。"""

    def _read_main(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_lyric_submenu(self):
        content = self._read_main()
        # 🎤 桌面歌词 必须是 submenu(不是 click:)
        self.assertIn("🎤 桌面歌词", content)
        # 找 「🎤 桌面歌词」 那段,后面 1000 字符必含 submenu:[]
        idx = content.find("🎤 桌面歌词")
        snippet = content[idx: idx + 1000]
        self.assertIn("submenu:", snippet,
                      "🎤 桌面歌词 必须改成 submenu 类型")
        self.assertIn("打开歌词窗口", snippet, "submenu 必须含打开歌词窗口")
        self.assertIn("始终在上", snippet, "submenu 必须含始终在上")
        self.assertIn("拖动已锁定", snippet, "submenu 必须含拖动已锁定")
        self.assertIn("📌 锁定当前位置", snippet, "submenu 必须含📌锁定当前位置")
        self.assertIn("🚪 关闭歌词窗口", snippet, "submenu 必须含🚪关闭歌词窗口")

    def test_checkbox_states_bound_to_lyric_state(self):
        """2 个 checkbox checked 必须来自 _lyric_state.alwaysOnTop / lockDrag。"""
        content = self._read_main()
        self.assertIn('checked: _lyric_state.alwaysOnTop', content,
                      "始终在上 checkbox 必须绑 alwaysOnTop")
        self.assertIn('checked: _lyric_state.lockDrag', content,
                      "拖动已锁定 checkbox 必须绑 lockDrag")

    def test_rebuild_tray_menu(self):
        """rebuildTrayMenu 必存在,toggle 后重建菜单(checkbox 状态由构造期属性决定)。"""
        content = self._read_main()
        self.assertIn("rebuildTrayMenu", content,
                      "must have rebuildTrayMenu function/let")
        # rebuildTrayMenu 必须调 buildTrayItems
        self.assertIn("buildTrayItems", content,
                      "must extract buildTrayItems function for reuse")

    def test_toggle_helpers_call_rebuild(self):
        """_toggleLyricAlwaysOnTop / _toggleLyricLockDrag 必调 rebuildTrayMenu + 推 webContents。"""
        content = self._read_main()
        for fn in ("_toggleLyricAlwaysOnTop", "_toggleLyricLockDrag"):
            idx = content.find(f"function {fn}")
            self.assertGreater(idx, 0)
            snippet = content[idx: idx + 800]
            self.assertIn("rebuildTrayMenu", snippet, f"{fn} must rebuild tray menu")
            self.assertIn("shell:lyricStateChanged", snippet,
                          f"{fn} must push state to lyric window webContents")


# ============================================================
# lyric.css 加 lock 模式
# ============================================================
class TestLyricCssLocked(unittest.TestCase):
    """P2.5+26(2026-10-03):lyric.css 加 .lyric-only-view.lyric-locked #drag-bar no-drag。"""

    def _read_css(self):
        return (MUSIC_VUE / "src" / "styles" / "lyric.css").read_text(encoding="utf-8")

    def test_locked_selector_exists(self):
        content = self._read_css()
        self.assertIn(".lyric-only-view.lyric-locked", content,
                      "missing .lyric-only-view.lyric-locked selector")
        self.assertIn("#drag-bar", content)

    def test_locked_no_drag(self):
        content = self._read_css()
        # 抓 locked 段
        idx = content.find(".lyric-only-view.lyric-locked")
        self.assertGreater(idx, 0)
        snippet = content[idx: idx + 600]
        self.assertIn("-webkit-app-region: no-drag", snippet,
                      "lock 态必须把 drag-bar 改 no-drag")
        self.assertIn("cursor: not-allowed", snippet,
                      "lock 态光标应为 not-allowed 视觉提示")
        # 必须保留 hover 视觉反馈
        self.assertIn(":hover", snippet, "locked 必须保留 hover 提示")

    def test_original_drag_intact(self):
        """原 #drag-bar drag 段不动(向后兼容)。"""
        content = self._read_css()
        # #drag-bar { ... -webkit-app-region: drag ... }
        m = re.search(r"#drag-bar\s*\{[^}]*\}", content)
        self.assertIsNotNone(m, "missing #drag-bar block")
        self.assertIn("-webkit-app-region: drag", m.group(0),
                      "原 drag 段不能动")


# ============================================================
# LyricOnlyView bootstrap 拉状态 + 订阅
# ============================================================
class TestLyricViewBootstrap(unittest.TestCase):
    """P2.5+26(2026-10-03):LyricOnlyView bootstrap 拉初始态 + 订阅 onLyricStateChanged。"""

    def _read_view(self):
        return (MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue").read_text(encoding="utf-8")

    def test_lock_state_ref(self):
        content = self._read_view()
        # 必须有 lock ref(false 默认)
        self.assertRegex(content, r"const lock\s*=\s*ref\(false\)",
                         "must have lock=false default")

    def test_bootstrap_get_lyric_state(self):
        content = self._read_view()
        self.assertIn("onMounted", content, "must use onMounted for bootstrap")
        self.assertIn("getLyricState", content, "must call getLyricState IPC")
        # lockDrag 必须应用到 lock.value
        self.assertRegex(content, r"lock\.value\s*=\s*s\.lockDrag",
                         "must apply s.lockDrag to lock.value")

    def test_subscribe_state_changes(self):
        content = self._read_view()
        self.assertIn("onLyricStateChanged", content,
                      "must subscribe onLyricStateChanged IPC channel")
        self.assertIn("unsubscribeState", content,
                      "must hold unsubscribe function for onBeforeUnmount cleanup")
        self.assertIn("onBeforeUnmount", content,
                      "must cleanup listener in onBeforeUnmount")

    def test_root_class_binding(self):
        content = self._read_view()
        # 根 div 必有 :class="{ 'lyric-locked': lock }"
        self.assertIn("'lyric-locked': lock", content,
                      "root must bind lyric-locked class when lock=true")

    def test_lock_pushed_payload(self):
        """推过来的 payload.lockDrag 必应用到 lock.value。"""
        content = self._read_view()
        self.assertRegex(content, r"payload\.lockDrag",
                         "must read payload.lockDrag from push")


# ============================================================
# preload.js 暴露 4 新方法 + 订阅
# ============================================================
class TestPreloadP26(unittest.TestCase):
    """P2.5+26(2026-10-03):preload.js 暴露 4 新方法 + onLyricStateChanged。"""

    def _read(self):
        return (SHELL / "preload.js").read_text(encoding="utf-8")

    def test_toggle_always_on_top_exposed(self):
        content = self._read()
        self.assertIn("toggleLyricAlwaysOnTop", content,
                      "missing toggleLyricAlwaysOnTop expose")
        self.assertIn("shell:toggleLyricAlwaysOnTop", content,
                      "must invoke shell:toggleLyricAlwaysOnTop IPC")

    def test_toggle_lock_drag_exposed(self):
        content = self._read()
        self.assertIn("toggleLyricLockDrag", content)
        self.assertIn("shell:toggleLyricLockDrag", content)

    def test_get_lyric_state_exposed(self):
        content = self._read()
        self.assertIn("getLyricState", content)
        self.assertIn("shell:getLyricState", content)

    def test_set_lyric_bounds_exposed(self):
        content = self._read()
        self.assertIn("setLyricBounds", content)
        self.assertIn("shell:setLyricBounds", content)

    def test_on_lyric_state_changed_exposed(self):
        content = self._read()
        self.assertIn("onLyricStateChanged", content,
                      "missing onLyricStateChanged subscriber")
        # 必须用 ipcRenderer.on + removeListener 返回 unsubscribe
        self.assertIn("ipcRenderer.on(\"shell:lyricStateChanged\"", content,
                      "must subscribe shell:lyricStateChanged channel")
        self.assertIn("removeListener(\"shell:lyricStateChanged\"", content,
                      "must return removeListener for cleanup")


if __name__ == "__main__":
    unittest.main()
