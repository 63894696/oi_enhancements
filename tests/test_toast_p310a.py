"""
test_toast_p310a.py — P3.10a(2026-10-04)桌面弹卡 toast 测试。

覆盖:
  - TestShowToastHelper — main.js showToast() 函数存在 + 队列 + 节流 + 偏好过滤
  - TestToastStateIO — _toast_state_load/save + 默认 level/max_queue/throttle_ms
  - TestSetToastLevelHelper — _setToastLevel helper + 持久化 + rebuildTrayMenu
  - TestToastIPC — ipcMain.handle("shell:show-toast" / get-toastLevel / set-toastLevel)
  - TestPreloadShowToast — preload.js 暴露 showToast/getToastLevel/setToastLevel
  - TestTrayToastLevelRadio — 托盘「⚙ 设置」「🔔 通知偏好」3 radio + group:toastLevel + 双模板双改
  - TestConfigLoaderToast — config_loader.js DEFAULTS 加 toast 段 + 4 快捷 getter
  - TestConfigYamlToast — prisIrai_config.yaml 加 toast: 段
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHELL = ROOT / "prisiragent-shell"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ============================================================
# showToast helper
# ============================================================
class TestShowToastHelper(unittest.TestCase):
    """P3.10a(2026-10-04):showToast() 函数 + 偏好过滤 + 节流 + 队列。"""

    def test_function_exists(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r"function\s+showToast\s*\(", "showToast 必须定义")

    def test_prefers_off_skips(self):
        """_toastState.level === "off" 时跳过。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertIn('"off"', snippet, "showToast 必须识别 off 偏好")
        self.assertIn('skipped', snippet, "showToast 必须返 skipped")

    def test_errors_only_filter(self):
        """_toastState.level === "errors" 时非 error 跳过。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertIn('"errors"', snippet, "showToast 必须识别 errors 偏好")
        self.assertRegex(snippet, r'level\s*!==\s*"error"', "errors-only 过滤逻辑")

    def test_info_throttle_5s(self):
        """L1 info 同 (level, title) 5s 内只一次。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertIn("_toastState.throttle_ms", snippet, "节流读 _toastState.throttle_ms")
        self.assertIn("_toastLastShown", snippet, "必须有 _toastLastShown 字典")

    def test_queue_max_3(self):
        """FIFO 队列上限 max_queue(默认 3)。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertIn("_toastQueue", snippet, "必须有 _toastQueue 数组")
        self.assertRegex(snippet, r"_toastQueue\.shift\(\)", "missing + shift 必存在")

    def test_uses_native_icon(self):
        """默认图标 = icon.png(项目已有,无需新增)。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertIn("icon.png", snippet, "showToast 必须用 icon.png 默认图标")

    def test_new_notification_called(self):
        """调 Electron Notification 构造函数。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function showToast")
        snippet = c[idx: idx + 2500]
        self.assertRegex(snippet, r"new\s+Notification\s*\(", "showToast 必须用 Electron Notification")


# ============================================================
# toast 状态持久化
# ============================================================
class TestToastStateIO(unittest.TestCase):
    """P3.10a(2026-10-04):_toast_state_path/load/save + userData/toast-state.json。"""

    def test_state_path_userData(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r"toast-state\.json", "toast-state.json 必须存在")

    def test_state_load_default_all(self):
        """_toast_state_load 默认 level=all。"""
        c = _read(SHELL / "main.js")
        # 找带 level: "all" 的 defaults 段
        idx = c.find("function _toast_state_load")
        snippet = c[idx: idx + 1000]
        self.assertIn('level: "all"', snippet, "默认 level=all")

    def test_state_load_default_max_queue_3(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _toast_state_load")
        snippet = c[idx: idx + 1000]
        self.assertIn("max_queue: 3", snippet, "默认 max_queue=3")

    def test_state_load_default_throttle_5000(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _toast_state_load")
        snippet = c[idx: idx + 1000]
        self.assertIn("throttle_ms: 5000", snippet, "默认 throttle_ms=5000")

    def test_state_load_invalid_level_fallback(self):
        """非法 level 兜底 all。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _toast_state_load")
        snippet = c[idx: idx + 1000]
        self.assertRegex(snippet, r'\[.all.,\s*.errors.*,\s*.off.\]', "白名单 [all, errors, off]")


# ============================================================
# _setToastLevel helper
# ============================================================
class TestSetToastLevelHelper(unittest.TestCase):
    """P3.10a(2026-10-04):_setToastLevel helper + rebuildTrayMenu 同步。"""

    def test_function_exists(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r"function\s+_setToastLevel\s*\(", "_setToastLevel 必须定义")

    def test_persists_state(self):
        c = _read(SHELL / "main.js")
        idx = c.find("function _setToastLevel")
        snippet = c[idx: idx + 800]
        self.assertIn("_toast_state_save", snippet, "setToastLevel 必持久化")

    def test_rebuilds_tray_menu(self):
        """P2.5+26 经验:radio 状态变化后必 rebuildTrayMenu。"""
        c = _read(SHELL / "main.js")
        idx = c.find("function _setToastLevel")
        snippet = c[idx: idx + 800]
        self.assertIn("rebuildTrayMenu", snippet, "setToastLevel 必调 rebuildTrayMenu")


# ============================================================
# IPC handlers
# ============================================================
class TestToastIPC(unittest.TestCase):
    """P3.10a(2026-10-04):3 个 ipcMain.handle 注册。"""

    def test_show_toast_registered(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:show-toast"', "show-toast 必注册")

    def test_get_toast_level_registered(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:get-toast-level"', "get-toast-level 必注册")

    def test_set_toast_level_registered(self):
        c = _read(SHELL / "main.js")
        self.assertRegex(c, r'ipcMain\.handle\(\s*"shell:set-toast-level"', "set-toast-level 必注册")


# ============================================================
# preload.js 暴露
# ============================================================
class TestPreloadShowToast(unittest.TestCase):
    """P3.10a(2026-10-04):preload.js 暴露 3 个方法。"""

    def test_show_toast_exposed(self):
        c = _read(SHELL / "preload.js")
        self.assertIn("showToast:", c, "preload 必暴露 showToast")

    def test_get_toast_level_exposed(self):
        c = _read(SHELL / "preload.js")
        self.assertIn("getToastLevel:", c, "preload 必暴露 getToastLevel")

    def test_set_toast_level_exposed(self):
        c = _read(SHELL / "preload.js")
        self.assertIn("setToastLevel:", c, "preload 必暴露 setToastLevel")

    def test_invoke_correct_channels(self):
        c = _read(SHELL / "preload.js")
        # showToast → shell:show-toast
        self.assertRegex(c, r'showToast.*shell:show-toast', "showToast 调 shell:show-toast IPC")
        # getToastLevel → shell:get-toast-level
        self.assertRegex(c, r'getToastLevel.*shell:get-toast-level', "getToastLevel 调 shell:get-toast-level")
        # setToastLevel → shell:set-toast-level
        self.assertRegex(c, r'setToastLevel.*shell:set-toast-level', "setToastLevel 调 shell:set-toast-level")


# ============================================================
# 托盘「🔔 通知偏好」radio
# ============================================================
class TestTrayToastLevelRadio(unittest.TestCase):
    """P3.10a(2026-10-04):托盘「⚙ 设置」「🔔 通知偏好」3 radio + 双模板双改。"""

    def test_settings_submenu_exists(self):
        c = _read(SHELL / "main.js")
        self.assertIn("⚙ 设置", c, "托盘必含「⚙ 设置」顶层菜单")

    def test_settings_submenu_in_both_templates(self):
        """createTray 与 buildTrayItems 都加 settingsSubmenu(双胞胎模板,跟 P3.4 同模式)。"""
        c = _read(SHELL / "main.js")
        # 找两个 settingsSubmenu 出现点
        n = c.count("settingsSubmenu")
        self.assertGreaterEqual(n, 2, "settingsSubmenu 必出现 ≥2 次(双模板)")

    def test_three_radios_in_settings_submenu(self):
        """3 个 radio:全部 / 仅错误 / 关闭,group:toastLevel。"""
        c = _read(SHELL / "main.js")
        # 找包含 settingsSubmenu 的 1000 字符段
        idx = c.find("settingsSubmenu")
        snippet = c[idx: idx + 1500]
        self.assertIn("全部", snippet, "必含「全部」")
        self.assertIn("仅错误", snippet, "必含「仅错误」")
        self.assertIn("关闭", snippet, "必含「关闭」")
        self.assertIn('group: "toastLevel"', snippet, "radio 必含 group:toastLevel 互斥")

    def test_radio_clicks_call_helper(self):
        """radio click 必调 _setToastLevel。"""
        c = _read(SHELL / "main.js")
        idx = c.find("settingsSubmenu")
        snippet = c[idx: idx + 1500]
        # 3 个 click 必调 _setToastLevel
        n_calls = snippet.count("_setToastLevel")
        self.assertGreaterEqual(n_calls, 3, "3 个 radio click 必各调 1 次 _setToastLevel")


# ============================================================
# config_loader.js 加 toast 段
# ============================================================
class TestConfigLoaderToast(unittest.TestCase):
    """P3.10a(2026-10-04):config_loader.js DEFAULTS + 4 快捷 getter。"""

    def test_defaults_has_toast_level(self):
        c = _read(SHELL / "config_loader.js")
        self.assertIn('"toast.level"', c, "DEFAULTS 必含 toast.level")
        self.assertIn('"toast.max_queue"', c, "DEFAULTS 必含 toast.max_queue")
        self.assertIn('"toast.throttle_ms"', c, "DEFAULTS 必含 toast.throttle_ms")

    def test_toast_level_default_all(self):
        c = _read(SHELL / "config_loader.js")
        m = re.search(r'"toast\.level":\s*"all"', c)
        self.assertIsNotNone(m, "toast.level 默认 = 'all'")

    def test_shortcut_getters_exist(self):
        c = _read(SHELL / "config_loader.js")
        for fn in ["toastLevel:", "toastMaxQueue:", "toastThrottleMs:", "toastDefaultTimeoutMs:"]:
            self.assertIn(fn, c, f"config_loader 必暴露 {fn}()")


# ============================================================
# prisIrai_config.yaml 加 toast: 段
# ============================================================
class TestConfigYamlToast(unittest.TestCase):
    """P3.10a(2026-10-04):prisIrai_config.yaml 加 toast: 段。"""

    def test_yaml_has_toast_section(self):
        y = _read(ROOT / "prisIrai_config.yaml")
        self.assertRegex(y, r"(?m)^toast:\s*$", "yaml 必含 toast: 段")

    def test_yaml_has_level_field(self):
        y = _read(ROOT / "prisIrai_config.yaml")
        self.assertIn("level: \"all\"", y, "yaml 必含 level: \"all\"")

    def test_yaml_has_max_queue(self):
        y = _read(ROOT / "prisIrai_config.yaml")
        self.assertIn("max_queue: 3", y, "yaml 必含 max_queue: 3")

    def test_yaml_has_throttle_ms(self):
        y = _read(ROOT / "prisIrai_config.yaml")
        self.assertIn("throttle_ms: 5000", y, "yaml 必含 throttle_ms: 5000")


if __name__ == "__main__":
    unittest.main(verbosity=2)