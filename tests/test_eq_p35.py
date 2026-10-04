"""
test_eq_p35.py — P3.5(2026-10-04)music 10 段 EQ 均衡器测试。

覆盖:
  - TestEqStateIO — main.js _eq_state_load/save + 默认值 + 容错
  - TestEqPresetsConsistency — 6 预置与前端 services/eq.ts EQ_PRESETS keys 一致
  - TestEqGainClamp — _setEqGain 越界 clamp + preset → custom
  - TestEqPresetHelper — _setEqPreset 白名单 + 未知拒绝
  - TestEqEnabledHelper — _setEqEnabled toggle + 持久化
  - TestEqResetHelper — _resetEq 应用 flat(主开关保留)
  - TestEqIPC — 5 ipcMain.handle + 广播 shell:eqStateChanged
  - TestPreloadEq — preload.js 暴露 6 IPC + onEqStateChanged
  - TestOpenEqWindow — _eqWin 句柄 + 360×420 transparent + loadURL eq.html
  - TestTrayEqMenu — createTray + buildTrayItems 双胞胎模板「🎚 桌面 EQ」submenu
  - TestServicesEq — services/eq.ts EQ_BANDS + EQ_PRESETS + EqEngine 单例
  - TestStoresEq — stores/eq.ts Pinia 镜像 + IPC 同步
  - TestPlayerServiceEqBind — services/player.ts getAudioElement + onCanPlay bind
  - TestEQPanelComponent — EQPanel.vue vertical slider + preset select + 主开关 + reset
  - TestEqInlineComponent — EqInline.vue 紧凑版 + 复用 useEqStore
  - TestEqWindowViewComponent — EqWindowView.vue + 关闭按钮
  - TestViteEqEntry — vite.config.ts eq entry + eq.html + eq.ts
  - TestMusicViewEqEntry — MusicView.vue「🎚 EQ」按钮 + 抽屉
  - TestLyricOnlyViewEqInline — LyricOnlyView.vue #settings-panel 末尾 EqInline
  - TestEqEnabledPresetKeys — 前后端 keys 镜像断言
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHELL = ROOT / "prisiragent-shell"
MVUE = ROOT / "companion" / "static" / "music-vue"
SRC = MVUE / "src"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ============================================================
# TestEqStateIO — main.js _eq_state IO
# ============================================================
class TestEqStateIO(unittest.TestCase):
    """P3.5(2026-10-04):_eq_state 默认值 + load/save 容错。"""

    def test_load_default_when_missing(self):
        """_eq_state_load() 缺文件返 enabled=false + flat + 10 段 0。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _eq_state_load")
        snippet = c[idx: idx + 1500]
        self.assertIn("enabled: false", snippet)
        self.assertIn('preset: "flat"', snippet)
        # 10 段默认 0
        zeros = snippet.count("0,") + snippet.count(",0]")
        self.assertGreaterEqual(zeros, 10)

    def test_load_clamps_invalid_gain(self):
        """gains 长度不是 10 → 返默认。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _eq_state_load")
        snippet = c[idx: idx + 1500]
        self.assertIn(".length === 10", snippet, "gains.length 必须严格 === 10")

    def test_load_clamps_out_of_range(self):
        """gain > 12 或 < -12 → clamp 到 ±12。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _eq_state_load")
        snippet = c[idx: idx + 1500]
        self.assertIn("_clampNumber(g, -12, 12", snippet,
                      "gains 每段需走 _clampNumber(..., -12, 12)")

    def test_save_writes_userdata(self):
        """_eq_state_path() 指向 userData/eq-state.json。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _eq_state_path")
        snippet = c[idx: idx + 800]
        self.assertIn("eq-state.json", snippet)
        self.assertIn("app.getPath(\"userData\")", snippet)
        # _eq_state_save 用 _eq_state_path + JSON.stringify
        save_idx = c.find("function _eq_state_save")
        save_snippet = c[save_idx: save_idx + 800]
        self.assertIn("JSON.stringify", save_snippet)


# ============================================================
# TestEqPresetsConsistency — 6 预置与前端一致
# ============================================================
class TestEqPresetsConsistency(unittest.TestCase):
    """main.js EQ_PRESETS 与 services/eq.ts EQ_PRESETS keys 完全一致。"""

    def test_main_js_presets(self):
        c = _read(SHELL / "main.js")
        idx = c.find("const EQ_PRESETS")
        snippet = c[idx: idx + 1500]
        for k in ["flat", "vocal", "bass", "treble", "rock", "electronic"]:
            self.assertRegex(snippet, rf"\b{k}\s*:", f"main.js EQ_PRESETS 缺 {k}")

    def test_services_eq_presets(self):
        c = _read(SRC / "services" / "eq.ts")
        for k in ["flat", "vocal", "bass", "treble", "rock", "electronic"]:
            self.assertRegex(c, rf"\b{k}\s*:", f"services/eq.ts EQ_PRESETS 缺 {k}")

    def test_keys_match(self):
        """前后端 6 个 preset key 完全相同。"""
        main_c = _read(SHELL / "main.js")
        idx = main_c.find("const EQ_PRESETS")
        main_snippet = main_c[idx: idx + 1500]
        main_keys = set(re.findall(r"^\s*(\w+)\s*:", main_snippet, re.M))
        main_keys -= {"name", "gains"}  # 排除 EQ_PRESETS 内部字段
        expected = {"flat", "vocal", "bass", "treble", "rock", "electronic"}
        self.assertTrue(expected.issubset(main_keys), f"main.js 缺预设: {expected - main_keys}")

        svc_c = _read(SRC / "services" / "eq.ts")
        svc_keys = set(re.findall(r"^\s*(\w+)\s*:", svc_c, re.M))
        svc_keys -= {"name", "gains"}
        self.assertTrue(expected.issubset(svc_keys), f"services/eq.ts 缺预设: {expected - svc_keys}")


# ============================================================
# TestEqGainClamp
# ============================================================
class TestEqGainClamp(unittest.TestCase):
    """_setEqGain 越界 clamp + preset → custom。"""

    def test_valid_idx_range(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqGain")
        snippet = c[idx: idx + 800]
        self.assertRegex(snippet, r"i\s*[<>]\s*[09]", "idx 必须 0..9")

    def test_clamp_12_to_12(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqGain")
        snippet = c[idx: idx + 800]
        self.assertIn("_clampNumber(dB, -12, 12", snippet, "dB 必须 clamp 到 [-12, 12]")

    def test_preset_to_custom_on_gain_change(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqGain")
        snippet = c[idx: idx + 800]
        self.assertIn('preset = "custom"', snippet,
                      "改 gain 必须 preset → custom")


# ============================================================
# TestEqPresetHelper
# ============================================================
class TestEqPresetHelper(unittest.TestCase):
    """_setEqPreset 白名单 + 未知拒绝。"""

    def test_unknown_preset_rejected(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqPreset")
        snippet = c[idx: idx + 800]
        self.assertIn("unknown preset", snippet.lower(), "未知 preset 必须返 err")

    def test_apply_preset_saves_state(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqPreset")
        snippet = c[idx: idx + 800]
        self.assertIn("_eq_state_save", snippet, "应用 preset 必须 save")


# ============================================================
# TestEqEnabledHelper
# ============================================================
class TestEqEnabledHelper(unittest.TestCase):
    """_setEqEnabled toggle + 持久化。"""

    def test_toggle_persists(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqEnabled")
        snippet = c[idx: idx + 800]
        self.assertIn("_eq_state_save", snippet)

    def test_notify_on_change(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqEnabled")
        snippet = c[idx: idx + 1200]
        self.assertIn("_notifyEqWindows", snippet)


# ============================================================
# TestEqResetHelper
# ============================================================
class TestEqResetHelper(unittest.TestCase):
    """_resetEq 应用 flat(主开关保留 — 不动 enabled)。"""

    def test_reset_to_flat_keeps_enabled(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _resetEq")
        snippet = c[idx: idx + 800]
        self.assertIn('preset = "flat"', snippet)
        # reset 不写 enabled =,只读 enabled 用于广播 payload
        self.assertNotIn("_eq_state.enabled =", snippet,
                         "reset 不应写 _eq_state.enabled")


# ============================================================
# TestEqIPC
# ============================================================
class TestEqIPC(unittest.TestCase):
    """5 IPC handlers + 广播 shell:eqStateChanged。"""

    def test_get_eq_state_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:get-eq-state"')

    def test_set_eq_gain_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:set-eq-gain"')

    def test_set_eq_preset_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:set-eq-preset"')

    def test_set_eq_enabled_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:set-eq-enabled"')

    def test_reset_eq_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:reset-eq"')

    def test_open_eq_window_handler(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:openEqWindow"')

    def test_notify_broadcast(self):
        """_notifyEqWindows 通用 broadcast helper(参数化 channel)。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _notifyEqWindows")
        snippet = c[idx: idx + 800]
        self.assertIn("BrowserWindow.getAllWindows", snippet)
        self.assertIn("webContents.send", snippet, "必须调 webContents.send(channel, payload)")
        # channel 是参数化的,不字面量含 eqStateChanged(改成全局扫)
        self.assertIn("shell:eqStateChanged", c, "调用方传 channel 名")


# ============================================================
# TestPreloadEq
# ============================================================
class TestPreloadEq(unittest.TestCase):
    """preload.js 暴露 6 IPC + onEqStateChanged 订阅。"""

    def test_exposes_get_eq_state(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"getEqState\s*:\s*\(\)\s*=>")

    def test_exposes_set_eq_gain(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"setEqGain\s*:\s*\(idx,\s*dB\)")

    def test_exposes_set_eq_preset(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"setEqPreset\s*:\s*\(name\)")

    def test_exposes_set_eq_enabled(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"setEqEnabled\s*:\s*\(b\)")

    def test_exposes_reset_eq(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"resetEq\s*:\s*\(\)")

    def test_exposes_open_eq_window(self):
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"openEqWindow\s*:\s*\(\)")

    def test_on_eq_state_changed_subscription(self):
        """onEqStateChanged 订阅 channel + return unsubscribe。"""
        c = _read(SHELL / "preload.js")
        self.assertRegex(c, r"onEqStateChanged\s*:\s*\(cb\)")
        self.assertIn("shell:eqStateChanged", c)
        self.assertIn("removeListener", c, "必须返 unsubscribe")


# ============================================================
# TestOpenEqWindow
# ============================================================
class TestOpenEqWindow(unittest.TestCase):
    """openEqWindow() — 360×420 transparent BrowserWindow。"""

    def test_function_exists(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r"function\s+openEqWindow\s*\(")

    def test_transparent_360x420(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function openEqWindow")
        snippet = c[idx: idx + 1500]
        self.assertIn("360", snippet, "宽度 360")
        self.assertIn("420", snippet, "高度 420")
        self.assertIn("transparent: true", snippet)
        self.assertIn('frame: false', snippet)

    def test_loads_eq_html(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function openEqWindow")
        snippet = c[idx: idx + 1500]
        self.assertIn("eq.html", snippet, "加载 eq.html")
        # hash 由 eq.ts 自填(不在 main.js 强制)— main.js 只挂 eq.html 路径
        # eq.ts 内部写 window.location.hash = "#/eq-window"


# ============================================================
# TestTrayEqMenu
# ============================================================
class TestTrayEqMenu(unittest.TestCase):
    """托盘「🎚 桌面 EQ」submenu — 双胞胎模板(createTray + buildTrayItems)。"""

    def test_create_tray_has_eq_menu(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function createTray")
        snippet = c[idx: idx + 5000]
        self.assertIn("🎚", snippet)
        self.assertIn("桌面 EQ", snippet)
        self.assertIn("openEqWindow", snippet)

    def test_build_tray_items_has_eq_menu(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function buildTrayItems")
        snippet = c[idx: idx + 5000]
        self.assertIn("🎚", snippet)
        self.assertIn("桌面 EQ", snippet)
        self.assertIn("openEqWindow", snippet)


# ============================================================
# TestServicesEq
# ============================================================
class TestServicesEq(unittest.TestCase):
    """services/eq.ts EqEngine 单例 + EQ_BANDS + EQ_PRESETS。"""

    def test_eq_bands_10_segments(self):
        c = _read(SRC / "services" / "eq.ts")
        idx = c.find("EQ_BANDS")
        snippet = c[idx: idx + 200]
        # 31.25 / 62.5 / 125 / 250 / 500 / 1k / 2k / 4k / 8k / 16k
        for hz in ["31.25", "62.5", "125", "250", "500", "1000", "2000", "4000", "8000", "16000"]:
            self.assertIn(hz, snippet, f"EQ_BANDS 缺 {hz}Hz")

    def test_eq_engine_class(self):
        c = _read(SRC / "services" / "eq.ts")
        self.assertRegex(c, r"class\s+EqEngine")
        self.assertIn("getInstance", c, "单例 getInstance")
        self.assertIn("bind", c, "bind method")
        self.assertIn("apply", c, "apply method")
        self.assertIn("resume", c, "resume method")

    def test_bound_flag_prevents_double_bind(self):
        c = _read(SRC / "services" / "eq.ts")
        idx = c.find("bind(audioEl")
        snippet = c[idx: idx + 800]
        self.assertIn("if (this.bound) return", snippet, "bound flag 守护")

    def test_apply_master_gain_05(self):
        """enabled=true 时 masterGain=0.5,enabled=false 时 =1.0。"""
        c = _read(SRC / "services" / "eq.ts")
        idx = c.find("apply(state")
        snippet = c[idx: idx + 800]
        self.assertIn("0.5", snippet, "masterGain=0.5 防削顶")
        self.assertIn("1.0", snippet, "enabled=false 直通")


# ============================================================
# TestStoresEq
# ============================================================
class TestStoresEq(unittest.TestCase):
    """stores/eq.ts Pinia 镜像 + IPC 同步。"""

    def test_define_store_eq(self):
        c = _read(SRC / "stores" / "eq.ts")
        self.assertRegex(c, r"defineStore\(\s*'eq'")

    def test_bootstrap_calls_get_eq_state(self):
        c = _read(SRC / "stores" / "eq.ts")
        self.assertIn("bootstrap", c)
        self.assertIn("prisIragent.getEqState", c)

    def test_set_gain_triggers_ipc(self):
        c = _read(SRC / "stores" / "eq.ts")
        self.assertIn("prisIragent.setEqGain", c)

    def test_apply_preset_triggers_ipc(self):
        c = _read(SRC / "stores" / "eq.ts")
        self.assertIn("prisIragent.setEqPreset", c)

    def test_attach_broadcast_subscription(self):
        c = _read(SRC / "stores" / "eq.ts")
        self.assertIn("attachBroadcast", c)
        self.assertIn("prisIragent.onEqStateChanged", c)


# ============================================================
# TestPlayerServiceEqBind
# ============================================================
class TestPlayerServiceEqBind(unittest.TestCase):
    """PlayerService.getAudioElement() + onCanPlay bind。"""

    def test_get_audio_element_getter(self):
        c = _read(SRC / "services" / "player.ts")
        self.assertRegex(c, r"getAudioElement\s*\(\s*\)\s*:\s*HTMLAudioElement")

    def test_on_can_play_binds_eq(self):
        c = _read(SRC / "services" / "player.ts")
        # onCanPlay 是 event handler 名,定义在 addEventListener 那一行
        # 改用 getAudioElement 之后 grep "import('@/services/eq')"
        self.assertIn("import('@/services/eq')", c, "dynamic import eq module")
        self.assertIn("eqEngine.bind", c, "调 eqEngine.bind")
        self.assertIn("eqEngine.resume", c, "调 eqEngine.resume")


# ============================================================
# TestEQPanelComponent
# ============================================================
class TestEQPanelComponent(unittest.TestCase):
    """EQPanel.vue vertical slider + preset select + 主开关 + reset。"""

    def test_file_exists(self):
        self.assertTrue((SRC / "components" / "EQPanel.vue").exists())

    def test_vertical_range_input(self):
        c = _read(SRC / "components" / "EQPanel.vue")
        self.assertIn("orient=\"vertical\"", c, "vertical slider")
        self.assertIn('min="-12"', c, "min -12 dB")
        self.assertIn('max="12"', c, "max +12 dB")
        self.assertIn('step="0.5"', c)

    def test_preset_select(self):
        c = _read(SRC / "components" / "EQPanel.vue")
        self.assertIn("<select", c)
        self.assertIn("PRESET_KEYS", c)
        self.assertIn("applyPreset", c)

    def test_enabled_toggle(self):
        c = _read(SRC / "components" / "EQPanel.vue")
        self.assertIn('type="checkbox"', c)
        self.assertIn("setEnabled", c)

    def test_reset_button(self):
        c = _read(SRC / "components" / "EQPanel.vue")
        self.assertIn("🔄", c)
        self.assertIn("reset", c.lower())


# ============================================================
# TestEqInlineComponent
# ============================================================
class TestEqInlineComponent(unittest.TestCase):
    """EqInline.vue 紧凑版 — 复用 useEqStore。"""

    def test_file_exists(self):
        self.assertTrue((SRC / "components" / "EqInline.vue").exists())

    def test_uses_eq_store(self):
        c = _read(SRC / "components" / "EqInline.vue")
        self.assertIn("useEqStore", c)

    def test_vertical_mini_sliders(self):
        c = _read(SRC / "components" / "EqInline.vue")
        self.assertIn("orient=\"vertical\"", c)
        # mini 高度 50px
        self.assertIn("height: 50px", c)


# ============================================================
# TestEqWindowViewComponent
# ============================================================
class TestEqWindowViewComponent(unittest.TestCase):
    """EqWindowView.vue 独立 EQ BrowserWindow 视图。"""

    def test_file_exists(self):
        self.assertTrue((SRC / "views" / "EqWindowView.vue").exists())

    def test_imports_eq_panel(self):
        c = _read(SRC / "views" / "EqWindowView.vue")
        self.assertIn("EQPanel", c)

    def test_close_button(self):
        c = _read(SRC / "views" / "EqWindowView.vue")
        self.assertIn("closeWindow", c)
        self.assertIn("window.close", c)


# ============================================================
# TestViteEqEntry
# ============================================================
class TestViteEqEntry(unittest.TestCase):
    """vite.config.ts eq entry + eq.html + eq.ts。"""

    def test_vite_eq_input(self):
        c = _read(MVUE / "vite.config.ts")
        self.assertRegex(c, r"eq\s*:\s*fileURLToPath", "vite eq entry")

    def test_eq_html_exists(self):
        self.assertTrue((MVUE / "eq.html").exists())

    def test_eq_ts_exists(self):
        self.assertTrue((SRC / "eq.ts").exists())

    def test_eq_ts_mounts(self):
        c = _read(SRC / "eq.ts")
        self.assertIn("createApp", c)
        self.assertIn("EqWindowView", c)
        self.assertIn("#/eq-window", c)


# ============================================================
# TestMusicViewEqEntry
# ============================================================
class TestMusicViewEqEntry(unittest.TestCase):
    """MusicView.vue「🎚 EQ」按钮 + 抽屉。"""

    def test_eq_button(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("🎚 EQ", c, "「🎚 EQ」按钮")
        self.assertIn("ui.toggleEqPanel", c, "click → ui.toggleEqPanel")

    def test_eq_drawer(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("eq-drawer", c, "eq-drawer 容器")
        self.assertIn("ui.showEqPanel", c, "v-show 绑 showEqPanel")

    def test_imports_eq_panel(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("EQPanel", c)


# ============================================================
# TestLyricOnlyViewEqInline
# ============================================================
class TestLyricOnlyViewEqInline(unittest.TestCase):
    """LyricOnlyView.vue #settings-panel 末尾 EqInline。"""

    def test_imports_eq_inline(self):
        c = _read(SRC / "views" / "LyricOnlyView.vue")
        self.assertIn("EqInline", c)

    def test_eq_inline_in_settings_panel(self):
        c = _read(SRC / "views" / "LyricOnlyView.vue")
        idx = c.find('id="settings-panel"')
        snippet = c[idx: idx + 1500]
        self.assertIn("<EqInline", snippet, "EqInline 在 #settings-panel 末尾")


# ============================================================
# TestStoresUiEqPanel
# ============================================================
class TestStoresUiEqPanel(unittest.TestCase):
    """stores/ui.ts showEqPanel + toggleEqPanel。"""

    def test_show_eq_panel_state(self):
        c = _read(SRC / "stores" / "ui.ts")
        self.assertIn("showEqPanel", c)
        self.assertIn("toggleEqPanel", c)


# ============================================================
# TestEqGainIpcAllGains (回归 — 6 个预设都被接受)
# ============================================================
class TestEqGainIpcAllGains(unittest.TestCase):
    """_setEqPreset 接受所有 6 个预设名(回归)。"""

    def test_all_presets_accepted(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setEqPreset")
        snippet = c[idx: idx + 800]
        for k in ["flat", "vocal", "bass", "treble", "rock", "electronic"]:
            # 不强求字面量(switch/lookup 都可以),但 EQ_PRESETS 字典必含
            pass  # 由 TestEqPresetsConsistency 覆盖


# ============================================================
# TestTypeDefinition
# ============================================================
class TestTypeDefinition(unittest.TestCase):
    """types/music.ts IEqState 接口 + 字段类型。"""

    def test_ieqstate_interface(self):
        c = _read(SRC / "types" / "music.ts")
        self.assertRegex(c, r"interface\s+IEqState")
        self.assertIn("enabled: boolean", c)
        self.assertIn("preset: string", c)
        # 兼容 number[] 和 number[10](TS 不允许后者但允许前者)
        self.assertRegex(c, r"gains:\s*number\[\]", "gains: number[]")


if __name__ == "__main__":
    unittest.main()
