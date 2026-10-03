"""
test_music_lyric_p32.py — P3.2(2026-10-03)歌词窗视觉调档测试(透明度+字号缩放)。

覆盖:
  - TestLyricStateSchema — main.js _lyric_state_load 兜底 opacity/scale 字段
  - TestLyricStateClamp — _clampNumber 4 边界(下限/上限/NaN/null/字符串)
  - TestLyricIPCOpacityScale — 2 个新 IPC handler 存在 + 正确 clamp + 落盘 + 推 webContents
  - TestLyricPreloadP32 — preload.js 暴露 setLyricOpacity/setLyricScale
  - TestLyricOnlyViewSettings — LyricOnlyView opacity/scale ref + 2 滑杆 + onOpacity/ScaleChange
  - TestLyricCssWindowVars — lyric.css 加 --lyric-window-opacity / --lyric-window-scale vars
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


# ============================================================
# _lyric_state 兜底字段(opacity + scale)
# ============================================================
class TestLyricStateSchema(unittest.TestCase):
    """P3.2(2026-10-03):_lyric_state_load 默认值加 opacity:0.85 + scale:1.0。"""

    def _read(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_state_load_has_opacity_default(self):
        """_lyric_state_load 必须有 opacity: 0.85 默认。"""
        content = self._read()
        idx = content.find("function _lyric_state_load")
        snippet = content[idx: idx + 1500]
        self.assertIn("opacity:", snippet, "must set opacity in _lyric_state_load")
        self.assertIn("0.85", snippet, "opacity default should be 0.85")

    def test_state_load_has_scale_default(self):
        """_lyric_state_load 必须有 scale: 1.0 默认。"""
        content = self._read()
        idx = content.find("function _lyric_state_load")
        snippet = content[idx: idx + 1500]
        self.assertIn("scale:", snippet, "must set scale in _lyric_state_load")

    def test_clamp_opacity_range(self):
        """opacity 必用 _clampNumber 夹到 [0.3, 1.0]。"""
        content = self._read()
        idx = content.find("function _lyric_state_load")
        snippet = content[idx: idx + 1500]
        # 找 opacity 那行紧邻 _clampNumber 调用
        self.assertRegex(snippet, r"opacity:\s*_clampNumber\([^,]+,\s*0\.3,\s*1\.0",
                         "opacity must clamp to [0.3, 1.0]")

    def test_clamp_scale_range(self):
        """scale 必用 _clampNumber 夹到 [0.7, 1.6]。"""
        content = self._read()
        idx = content.find("function _lyric_state_load")
        snippet = content[idx: idx + 1500]
        self.assertRegex(snippet, r"scale:\s*_clampNumber\([^,]+,\s*0\.7,\s*1\.6",
                         "scale must clamp to [0.7, 1.6]")


# ============================================================
# _clampNumber 边界
# ============================================================
class TestLyricStateClamp(unittest.TestCase):
    """P3.2(2026-10-03):_clampNumber 容错 — NaN/null/字符串/超界 → 返 dflt。"""

    def _read(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def _extract_fn(self):
        content = self._read()
        m = re.search(r"function _clampNumber\s*\([^)]*\)\s*\{(.*?)^\}", content,
                      re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "_clampNumber must be defined")
        return m.group(1)

    def test_returns_lo_below(self):
        """< lo 必返 lo — 通过 Math.max(lo, ...) 保证下限。"""
        body = self._extract_fn()
        self.assertRegex(body, r"Math\.max\s*\(\s*lo",
                         "must clamp lower bound via Math.max(lo, ...)")
        self.assertIn("return ", body)

    def test_returns_hi_above(self):
        """> hi 必返 hi — 通过 Math.min(..., hi) 保证上限。"""
        body = self._extract_fn()
        self.assertRegex(body, r"Math\.min\s*\(\s*hi",
                         "must clamp upper bound via Math.min(..., hi)")

    def test_falls_back_on_nan(self):
        """NaN/Infinity/非数必返 dflt。"""
        body = self._extract_fn()
        self.assertIn("isFinite", body,
                      "must use isFinite to detect non-number numeric values")
        self.assertIn("return dflt", body,
                      "must return dflt on non-finite input")

    def test_returns_value_in_range(self):
        """[lo, hi] 内必原值返 — Math.max + Math.min 嵌套返回 n。"""
        body = self._extract_fn()
        self.assertRegex(body, r"Math\.max\s*\(\s*lo,\s*Math\.min\s*\(\s*hi,\s*n\s*\)\s*\)",
                         "in-range must return n via Math.max(lo, Math.min(hi, n))")
        self.assertIn("return ", body)


# ============================================================
# 2 个新 IPC handler
# ============================================================
class TestLyricIPCOpacityScale(unittest.TestCase):
    """P3.2(2026-10-03):shell:setLyricOpacity + shell:setLyricScale IPC handler。"""

    def _read(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_opacity_handler_exists(self):
        content = self._read()
        self.assertIn('ipcMain.handle("shell:setLyricOpacity"',
                      content, "must define shell:setLyricOpacity handler")

    def test_opacity_handler_clamps(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricOpacity"')
        snippet = content[idx: idx + 600]
        self.assertIn("_clampNumber(value, 0.3, 1.0", snippet,
                      "setLyricOpacity must clamp [0.3, 1.0]")

    def test_opacity_handler_persists(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricOpacity"')
        snippet = content[idx: idx + 600]
        self.assertIn("_lyric_state.opacity = v", snippet,
                      "must assign new opacity to _lyric_state")
        self.assertIn("_lyric_state_save", snippet,
                      "must persist to lyric-window-state.json")

    def test_opacity_handler_pushes_state(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricOpacity"')
        snippet = content[idx: idx + 800]
        self.assertIn("shell:lyricStateChanged", snippet,
                      "must push state via shell:lyricStateChanged")
        self.assertIn("opacity: v", snippet,
                      "payload must include opacity")

    def test_opacity_handler_returns(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricOpacity"')
        snippet = content[idx: idx + 800]
        self.assertIn("return { ok: true", snippet,
                      "must return ok:true result")
        self.assertIn("opacity: v", snippet,
                      "must return new opacity value")

    def test_scale_handler_exists(self):
        content = self._read()
        self.assertIn('ipcMain.handle("shell:setLyricScale"',
                      content, "must define shell:setLyricScale handler")

    def test_scale_handler_clamps(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricScale"')
        snippet = content[idx: idx + 600]
        self.assertIn("_clampNumber(value, 0.7, 1.6", snippet,
                      "setLyricScale must clamp [0.7, 1.6]")

    def test_scale_handler_persists_and_pushes(self):
        content = self._read()
        idx = content.find('ipcMain.handle("shell:setLyricScale"')
        snippet = content[idx: idx + 800]
        self.assertIn("_lyric_state.scale = v", snippet)
        self.assertIn("_lyric_state_save", snippet)
        self.assertIn("scale: v", snippet,
                      "payload must include scale")
        self.assertIn("return { ok: true", snippet)

    def test_get_lyric_state_returns_opacity_scale(self):
        """shell:getLyricState 也必须返 opacity + scale(否则 bootstrap 拿不到初始值)。"""
        content = self._read()
        idx = content.find('ipcMain.handle("shell:getLyricState"')
        snippet = content[idx: idx + 600]
        self.assertIn("opacity: _lyric_state.opacity", snippet,
                      "getLyricState must return opacity")
        self.assertIn("scale: _lyric_state.scale", snippet,
                      "getLyricState must return scale")


# ============================================================
# preload.js 暴露
# ============================================================
class TestLyricPreloadP32(unittest.TestCase):
    """P3.2(2026-10-03):preload.js 加 setLyricOpacity + setLyricScale。"""

    def _read(self):
        return (SHELL / "preload.js").read_text(encoding="utf-8")

    def test_opacity_exposed(self):
        content = self._read()
        self.assertIn("setLyricOpacity:", content,
                      "preload must expose setLyricOpacity")
        self.assertIn('ipcRenderer.invoke("shell:setLyricOpacity"',
                      content, "must invoke shell:setLyricOpacity")

    def test_scale_exposed(self):
        content = self._read()
        self.assertIn("setLyricScale:", content,
                      "preload must expose setLyricScale")
        self.assertIn('ipcRenderer.invoke("shell:setLyricScale"',
                      content, "must invoke shell:setLyricScale")


# ============================================================
# LyricOnlyView 集成(opacity/scale refs + 2 滑杆)
# ============================================================
class TestLyricOnlyViewSettings(unittest.TestCase):
    """P3.2(2026-10-03):LyricOnlyView 加 opacity/scale ref + onOpacityChange + onScaleChange。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue").read_text(encoding="utf-8")

    def test_opacity_ref(self):
        content = self._read()
        self.assertIn("const opacity = ref(0.85)", content,
                      "must define opacity ref with default 0.85")

    def test_scale_ref(self):
        content = self._read()
        self.assertIn("const scale = ref(1.0)", content,
                      "must define scale ref with default 1.0")

    def test_bootstrap_reads_opacity(self):
        """onMounted bootstrap getLyricState 必须读 opacity 字段。"""
        content = self._read()
        idx = content.find("onMounted(async () =>")
        snippet = content[idx: idx + 1500]
        self.assertIn("s.opacity", snippet,
                      "bootstrap must read s.opacity from getLyricState")
        self.assertIn("opacity.value = s.opacity", snippet,
                      "must assign opacity.value from bootstrap")

    def test_bootstrap_reads_scale(self):
        content = self._read()
        idx = content.find("onMounted(async () =>")
        snippet = content[idx: idx + 1500]
        self.assertIn("s.scale", snippet,
                      "bootstrap must read s.scale from getLyricState")

    def test_state_push_handler_reads_opacity(self):
        """onLyricStateChanged 回调必处理 payload.opacity + payload.scale。"""
        content = self._read()
        # 找第二个 occurrence(第一个是 main.js onLyricStateChanged 注释引用)
        idx = content.find("prisIragent.onLyricStateChanged")
        self.assertGreater(idx, 0, "must call prisIragent.onLyricStateChanged")
        snippet = content[idx: idx + 800]
        self.assertIn("payload.opacity", snippet,
                      "push handler must read payload.opacity")
        self.assertIn("payload.scale", snippet,
                      "push handler must read payload.scale")

    def test_on_opacity_change_handler(self):
        """必须有 onOpacityChange 调 IPC setLyricOpacity。"""
        content = self._read()
        self.assertIn("function onOpacityChange", content,
                      "must define onOpacityChange handler")
        idx = content.find("function onOpacityChange")
        snippet = content[idx: idx + 400]
        self.assertIn("setLyricOpacity", snippet,
                      "onOpacityChange must call IPC setLyricOpacity")
        self.assertIn("opacity.value = v", snippet,
                      "onOpacityChange must update opacity ref")

    def test_on_scale_change_handler(self):
        content = self._read()
        self.assertIn("function onScaleChange", content,
                      "must define onScaleChange handler")
        idx = content.find("function onScaleChange")
        snippet = content[idx: idx + 400]
        self.assertIn("setLyricScale", snippet,
                      "onScaleChange must call IPC setLyricScale")

    def test_settings_panel_in_template(self):
        """模板必须渲染 #settings-panel + 2 个 .setting-row 滑杆。"""
        content = self._read()
        self.assertIn('id="settings-panel"', content,
                      "must render #settings-panel container")
        self.assertIn('class="setting-row"', content,
                      "must render .setting-row items")
        self.assertIn('@input="onOpacityChange"', content,
                      "must bind @input to onOpacityChange")
        self.assertIn('@input="onScaleChange"', content,
                      "must bind @input to onScaleChange")
        self.assertIn('type="range"', content,
                      "must use range slider inputs")

    def test_opacity_slider_min_max(self):
        """opacity 滑杆必 min=30 max=100(×100 是百分比显示)。"""
        content = self._read()
        idx = content.find('@input="onOpacityChange"')
        # 找前一行(input 开始)— idx 可能=0,所以用 find 找上一个 <input
        prev_input = content.rfind("<input", 0, idx)
        self.assertGreater(prev_input, 0,
                          "opacity slider must be an <input type=range>")
        snippet = content[prev_input: idx + 200]
        self.assertIn('min="30"', snippet)
        self.assertIn('max="100"', snippet)

    def test_scale_slider_min_max(self):
        content = self._read()
        idx = content.find('@input="onScaleChange"')
        prev_input = content.rfind("<input", 0, idx)
        self.assertGreater(prev_input, 0,
                          "scale slider must be an <input type=range>")
        snippet = content[prev_input: idx + 200]
        self.assertIn('min="70"', snippet)
        self.assertIn('max="160"', snippet)


# ============================================================
# lyric.css --lyric-window-opacity / --lyric-window-scale vars
# ============================================================
class TestLyricCssWindowVars(unittest.TestCase):
    """P3.2(2026-10-03):lyric.css 加 :root --lyric-window-opacity/scale + body 应用。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "styles" / "lyric.css").read_text(encoding="utf-8")

    def test_root_var_opacity(self):
        content = self._read()
        self.assertIn("--lyric-window-opacity:", content,
                      ":root must define --lyric-window-opacity")

    def test_root_var_scale(self):
        content = self._read()
        self.assertIn("--lyric-window-scale:", content,
                      ":root must define --lyric-window-scale")

    def test_root_var_opacity_default(self):
        """--lyric-window-opacity 必默认 0.85。"""
        content = self._read()
        idx = content.find("--lyric-window-opacity:")
        snippet = content[idx: idx + 60]
        self.assertIn("0.85", snippet, "default opacity should be 0.85")

    def test_root_var_scale_default(self):
        """--lyric-window-scale 必默认 1.0。"""
        content = self._read()
        idx = content.find("--lyric-window-scale:")
        snippet = content[idx: idx + 60]
        self.assertIn("1.0", snippet, "default scale should be 1.0")

    def test_body_applies_opacity(self):
        content = self._read()
        # body 块必须 opacity: var(--lyric-window-opacity)
        self.assertIn("opacity: var(--lyric-window-opacity",
                      content, "body must consume --lyric-window-opacity")

    def test_body_applies_scale(self):
        content = self._read()
        # body 块必须 transform: scale(var(--lyric-window-scale))
        self.assertIn("scale(var(--lyric-window-scale",
                      content, "body must consume --lyric-window-scale")

    def test_body_has_transition(self):
        """body opacity 必有过渡,免滑杆瞬变刺眼。"""
        content = self._read()
        # 找 body { ... } 块
        m = re.search(r"html,\s*\nbody\s*\{(.*?)^\}", content, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "html, body block must exist")
        body = m.group(1)
        self.assertIn("transition", body,
                      "body opacity transition for smooth slider feel")


if __name__ == "__main__":
    unittest.main()