"""
test_music_lyric_p34.py — P3.4(2026-10-03)歌词窗单/双行 toggle 测试。

覆盖:
  - TestLyricStateLinesField — main.js _lyric_state_load 加 lines 字段(默认 1,容错)
  - TestLyricSetLinesHelper — _setLyricLines helper 存在 + 边界
  - TestLyricSetLinesIPC — ipcMain.handle("shell:setLyricLines") 注册 + 持久化 + 推 webContents
  - TestGetLyricStateReturnsLines — shell:getLyricState 返回值加 lines 字段
  - TestTrayLyricLinesRadio — 托盘子菜单 2 radio(group:lyricLines)+ 双模板双改
  - TestPreloadSetLyricLines — preload.js 暴露 setLyricLines
  - TestLyricLinesToggleComponent — LyricLinesToggle.vue 文件 + 模板 + IPC 调
  - TestLyricOnlyViewLinesClass — LyricOnlyView bootstrap 拉 lines + 订阅 + 根 class 双绑
  - TestLyricCssLinesMode — lyric.css 加 .lyric-lines-1/2 .line.next 切换
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHELL = ROOT / "prisiragent-shell"
MUSIC_VUE = ROOT / "companion" / "static" / "music-vue"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ============================================================
# 后端 _lyric_state 加 lines 字段
# ============================================================
class TestLyricStateLinesField(unittest.TestCase):
    """P3.4(2026-10-03):_lyric_state_load + defaults 加 lines: 1|2 字段。"""

    def test_state_load_has_lines_default(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _lyric_state_load")
        snippet = c[idx: idx + 2000]
        self.assertIn("lines:", snippet, "_lyric_state_load 必须有 lines 字段")

    def test_state_load_lines_default_1(self):
        """_lyric_state_load 必须有默认 lines: 1(单行紧凑)。"""
        c = _read(SHELL / "main.js")
        # defaults 段(异常分支返回)
        m = re.search(r"return\s*\{[^}]*alwaysOnTop:\s*true[\s\S]*?lines:\s*1", c)
        self.assertIsNotNone(m, "defaults 必须有 lines: 1")

    def test_state_load_accepts_lines_2(self):
        """_lyric_state_load 应读 obj.lines === 2 → 2。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _lyric_state_load")
        snippet = c[idx: idx + 2000]
        # 必须有 "obj.lines === 2" 模式
        self.assertRegex(snippet, r"Number\(obj\.lines\)\s*===\s*2", "lines=2 必识")

    def test_state_load_invalid_lines_fallback_1(self):
        """_lyric_state_load 越界值兜底 1。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _lyric_state_load")
        snippet = c[idx: idx + 2000]
        # 表达式必须含 : 1 兜底(无论三元还是 if-else,只要非 2 都给 1)
        self.assertRegex(snippet, r"lines:\s*\(?Number\(obj\.lines\)\s*===\s*2\)?\s*\?\s*2\s*:\s*1",
                         "lines 非 2 必兜底 1")


# ============================================================
# _setLyricLines helper 存在
# ============================================================
class TestLyricSetLinesHelper(unittest.TestCase):
    """P3.4(2026-10-03):_setLyricLines helper 在 main.js 内,被 IPC + 托盘 menu 共用。"""

    def test_helper_exists(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r"function\s+_setLyricLines\s*\(", "_setLyricLines 必须定义")

    def test_helper_clamps_to_1_or_2(self):
        c = _read(SHELL / "main.js")
        # 容错表达式可能换行,走 [\s\S]*?;同时允许 `=== 2` 与 `?` 间出现 `)`
        m = re.search(
            r"function\s+_setLyricLines\s*\([^)]*\)\s*\{[\s\S]*?Number\([\s\S]*?value[\s\S]*?\)\s*===\s*2\s*\)?\s*\?\s*2\s*:\s*1",
            c, re.DOTALL)
        self.assertIsNotNone(m, "_setLyricLines 必把 value 容错到 1|2")

    def test_helper_saves_state(self):
        c = _read(SHELL / "main.js")
        m = re.search(r"function\s+_setLyricLines[\s\S]*?_lyric_state_save\(", c)
        self.assertIsNotNone(m, "_setLyricLines 必调 _lyric_state_save")

    def test_helper_notifies_window(self):
        c = _read(SHELL / "main.js")
        m = re.search(r"function\s+_setLyricLines[\s\S]*?_notifyLyricWindow\(", c)
        self.assertIsNotNone(m, "_setLyricLines 必 _notifyLyricWindow 推 webContents")

    def test_helper_calls_rebuild_tray(self):
        c = _read(SHELL / "main.js")
        m = re.search(r"function\s+_setLyricLines[\s\S]*?rebuildTrayMenu\(", c)
        self.assertIsNotNone(m, "_setLyricLines 必 rebuildTrayMenu(P2.5+26 经验)")


# ============================================================
# shell:setLyricLines IPC
# ============================================================
class TestLyricSetLinesIPC(unittest.TestCase):
    """P3.4(2026-10-03):shell:setLyricLines IPC handler 注册。"""

    def test_ipc_handler_registered(self):
        c = _read(SHELL / "main.js")
        self.assertIn('ipcMain.handle("shell:setLyricLines"', c,
                      "必须注册 shell:setLyricLines IPC")

    def test_ipc_returns_lines(self):
        c = _read(SHELL / "main.js")
        # handler 必返 ok+lines
        m = re.search(
            r'ipcMain\.handle\(\s*"shell:setLyricLines"[\s\S]*?return\s*\{\s*ok:\s*true,\s*lines:',
            c, re.DOTALL)
        self.assertIsNotNone(m, "handler 必返 {ok:true, lines}")


# ============================================================
# shell:getLyricState 返回 lines 字段
# ============================================================
class TestGetLyricStateReturnsLines(unittest.TestCase):
    """P3.4(2026-10-03):getLyricState 返回值加 lines 字段。"""

    def test_get_state_returns_lines(self):
        c = _read(SHELL / "main.js")
        m = re.search(
            r'ipcMain\.handle\(\s*"shell:getLyricState"[\s\S]*?scale:\s*_lyric_state\.scale[\s\S]*?lines:\s*_lyric_state\.lines',
            c, re.DOTALL)
        self.assertIsNotNone(m, "shell:getLyricState 必返 lines 字段")


# ============================================================
# 托盘子菜单加 radio(双模板双改)
# ============================================================
class TestTrayLyricLinesRadio(unittest.TestCase):
    """P3.4(2026-10-03):托盘子菜单 ☝ 单行 / ☟ 双行 radio 双模板都有。"""

    def test_tray_has_singlerow_radio(self):
        c = _read(SHELL / "main.js")
        m = re.search(
            r'label:\s*"☝\s*单行"[^}]+type:\s*"radio"[^}]+checked:\s*_lyric_state\.lines\s*===\s*1[^}]+group:\s*"lyricLines"',
            c, re.DOTALL)
        self.assertIsNotNone(m, "必须 ☝ 单行 radio + group:lyricLines")

    def test_tray_has_doublerow_radio(self):
        c = _read(SHELL / "main.js")
        m = re.search(
            r'label:\s*"☟\s*双行"[^}]+type:\s*"radio"[^}]+checked:\s*_lyric_state\.lines\s*===\s*2[^}]+group:\s*"lyricLines"',
            c, re.DOTALL)
        self.assertIsNotNone(m, "必须 ☟ 双行 radio + group:lyricLines")

    def test_tray_double_template(self):
        """trayItems 与 buildTrayItems 双胞胎模板都要加 radio。"""
        c = _read(SHELL / "main.js")
        # 数 ☝ 单行出现次数,应 >= 2
        self.assertGreaterEqual(c.count('"☝ 单行"'), 2, "☝ 单行 必须出现在 2 个模板中")
        self.assertGreaterEqual(c.count('"☟ 双行"'), 2, "☟ 双行 必须出现在 2 个模板中")

    def test_tray_radio_uses_helper(self):
        """radio click 必调 _setLyricLines(1|2)。"""
        c = _read(SHELL / "main.js")
        self.assertIn("() => _setLyricLines(1)", c, "radio 单行 click 必调 _setLyricLines(1)")
        self.assertIn("() => _setLyricLines(2)", c, "radio 双行 click 必调 _setLyricLines(2)")


# ============================================================
# preload.js 加 setLyricLines
# ============================================================
class TestPreloadSetLyricLines(unittest.TestCase):
    """P3.4(2026-10-03):preload.js 暴露 setLyricLines。"""

    def test_preload_exposes_set_lines(self):
        c = _read(SHELL / "preload.js")
        self.assertIn("setLyricLines:", c, "preload 必暴露 setLyricLines")
        self.assertIn('"shell:setLyricLines"', c, "setLyricLines 必 invoke shell:setLyricLines")


# ============================================================
# 前端 LyricLinesToggle.vue 组件
# ============================================================
class TestLyricLinesToggleComponent(unittest.TestCase):
    """P3.4(2026-10-03):LyricLinesToggle.vue 文件 + 接口签名。"""

    def _path(self):
        return MUSIC_VUE / "src" / "components" / "LyricLinesToggle.vue"

    def test_file_exists(self):
        self.assertTrue(self._path().exists(), "LyricLinesToggle.vue must exist")

    def test_has_props(self):
        c = _read(self._path())
        self.assertIn("value: 1 | 2", c, "props.value 必 1|2 类型")

    def test_has_two_buttons(self):
        c = _read(self._path())
        # Vue template button text 用 >...< 包围,直接测裸字符
        self.assertIn("☝ 单行", c, "必 ☝ 单行 按钮")
        self.assertIn("☟ 双行", c, "必 ☟ 双行 按钮")

    def test_active_class_binding(self):
        c = _read(self._path())
        # active 类绑 value === 1 / value === 2
        self.assertIn("active: value === 1", c, "单行按钮 active 必绑 value===1")
        self.assertIn("active: value === 2", c, "双行按钮 active 必绑 value===2")

    def test_calls_prisiragent_ipc(self):
        c = _read(self._path())
        self.assertIn("prisIragent.setLyricLines", c, "必调 prisIragent.setLyricLines IPC")

    def test_emits_change(self):
        c = _read(self._path())
        self.assertIn("emit('change'", c, "成功 emit('change', v)")

    def test_has_graceful_fallback(self):
        """dev 模式无 IPC 时降级本地 toggle 不报错。"""
        c = _read(self._path())
        # typeof w?.prisIragent?.setLyricLines !== 'function' → 降级
        self.assertIn("typeof w?.prisIragent?.setLyricLines !== 'function'", c,
                      "无 IPC 必降级本地 emit")

    def test_seeded_with_var(self):
        c = _read(self._path())
        self.assertIn("const props = defineProps", c, "必 defineProps")
        self.assertIn("defineEmits", c, "必 defineEmits")


# ============================================================
# LyricOnlyView.vue 集成
# ============================================================
class TestLyricOnlyViewLinesClass(unittest.TestCase):
    """P3.4(2026-10-03):LyricOnlyView bootstrap 拉 lines + 订阅 + 根 class 双绑。"""

    def _read_view(self):
        return _read(MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue")

    def test_imports_toggle(self):
        c = self._read_view()
        self.assertIn("import LyricLinesToggle", c, "必 import LyricLinesToggle")

    def test_has_lines_ref(self):
        c = self._read_view()
        self.assertIn("lines", c, "必 lines ref")
        # ref<1 | 2>(1)
        self.assertRegex(c, r"const\s+lines\s*=\s*ref\s*<\s*1\s*\|\s*2\s*>\s*\(\s*1\s*\)",
                         "lines 必 typed ref<1|2>(1)")

    def test_bootstrap_pulls_lines(self):
        """bootstrap getLyricState 必读 s.lines 容错赋值。"""
        c = self._read_view()
        self.assertIn("s.lines === 2", c, "bootstrap 必识别 lines=2")
        self.assertIn("s.lines === 1", c, "bootstrap 必识别 lines=1")

    def test_subscription_handles_lines(self):
        """onLyricStateChanged 订阅必应用 payload.lines。"""
        c = self._read_view()
        self.assertIn("payload.lines === 2", c, "订阅必处理 payload.lines=2")
        self.assertIn("payload.lines === 1", c, "订阅必处理 payload.lines=1")

    def test_root_class_binds_lines_modes(self):
        c = self._read_view()
        # 根 :class 同时含 lyric-lines-1 和 lyric-lines-2
        self.assertIn("'lyric-lines-1': lines === 1", c, "根 class 必绑 lyric-lines-1")
        self.assertIn("'lyric-lines-2': lines === 2", c, "根 class 必绑 lyric-lines-2")

    def test_uses_toggle_component(self):
        c = self._read_view()
        # 模板里用 <LyricLinesToggle :value="lines" />
        self.assertIn("<LyricLinesToggle", c, "必挂载 LyricLinesToggle")
        self.assertIn(':value="lines"', c, "必绑 :value=\"lines\"")


# ============================================================
# lyric.css 单/双行样式切换
# ============================================================
class TestLyricCssLinesMode(unittest.TestCase):
    """P3.4(2026-10-03):lyric.css 加 .lyric-lines-1/2 .line.next 切换。"""

    def _read(self):
        return _read(MUSIC_VUE / "src" / "styles" / "lyric.css")

    def test_lines_1_hides_next(self):
        c = self._read()
        self.assertIn(".lyric-only-view.lyric-lines-1 #lyrics-stage .line.next", c,
                      "必 .lyric-lines-1 .line.next 规则")
        m = re.search(
            r"\.lyric-only-view\.lyric-lines-1\s+#lyrics-stage\s+\.line\.next\s*\{[^}]*display:\s*none",
            c, re.DOTALL)
        self.assertIsNotNone(m, "单行 .line.next 必 display: none")

    def test_lines_2_shows_next(self):
        c = self._read()
        self.assertIn(".lyric-only-view.lyric-lines-2 #lyrics-stage .line.next", c,
                      "必 .lyric-lines-2 .line.next 规则")
        m = re.search(
            r"\.lyric-only-view\.lyric-lines-2\s+#lyrics-stage\s+\.line\.next\s*\{[^}]*display:\s*block",
            c, re.DOTALL)
        self.assertIsNotNone(m, "双行 .line.next 必 display: block")


if __name__ == "__main__":
    unittest.main()
