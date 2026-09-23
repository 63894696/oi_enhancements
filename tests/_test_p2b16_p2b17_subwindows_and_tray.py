# -*- coding: utf-8 -*-
r"""
_test_p2b16_p2b17_subwindows_and_tray.py — P2.5+16 + P2.5+17(2026-09-22)合并静态扫

P2.5+16 子窗口分离:
  - 4 子窗口(语伴/音乐/📅日历/🔀工作流)各自独立 BrowserWindow
  - _createChildWindow(spec) helper + childWindows Map
  - close 仅 hide(常驻后台),下次同 label 秒开
  - 各自 _CHILD_SPEC(尺寸/title)
  - closeAllChildWindows / destroyAllChildWindows
  - before-quit 钩 destroyAllChildWindows

P2.5+17 tray icon 子菜单:
  - 3 组 submenu:主控 / 多窗口 / 系统
  - 开发者模式(若安装)独立顶层菜单项,不进任何 submenu
  - 多窗口 submenu 末尾「关闭所有子窗口」聚合
  - 系统 submenu 含开机自启 checkbox + 退出

通过条件:30+ 静态扫绿 + node --check OK + py_compile OK
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _exists(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


# ----------------------------------------------------------------------
# P2.5+16 子窗口分离
# ----------------------------------------------------------------------
def test_child_windows_map():
    """① childWindows Map + quitting flag。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    assert "let quitting = false" in text, "main.js 缺 quitting flag"
    assert "const childWindows = new Map()" in text, "main.js 缺 childWindows Map"
    print("✓ childWindows Map + quitting flag 就位")


def test_common_web_preferences():
    """② _commonWebPreferences helper — 沙箱红线(contextIsolation / nodeIntegration / sandbox)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # 提取 helper 段落
    m = re.search(r"function _commonWebPreferences\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m, "main.js 缺 _commonWebPreferences helper"
    body = m.group(1)
    assert "contextIsolation: true" in body, "_commonWebPreferences 缺 contextIsolation"
    assert "nodeIntegration: false" in body, "_commonWebPreferences 缺 nodeIntegration"
    assert "sandbox: true" in body, "_commonWebPreferences 缺 sandbox"
    assert "preload" in body, "_commonWebPreferences 缺 preload"
    print("✓ _commonWebPreferences 沙箱红线三件套齐全")


def test_create_child_window():
    """③ _createChildWindow(spec) helper — 重用 / ready-to-show / 兜底 3.5s / setWindowOpenHandler / close→hide / closed→delete。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # helper 存在
    assert "function _createChildWindow(spec)" in text, "main.js 缺 _createChildWindow helper"
    # 重用逻辑
    m = re.search(r"function _createChildWindow\(spec\)\s*\{(.*?)\nfunction closeAllChildWindows", text, re.DOTALL)
    assert m, "_createChildWindow 跟 closeAllChildWindows 不连续"
    body = m.group(1)
    assert "existing.isDestroyed()" in body, "缺 existing 检查"
    assert "existing.show()" in body and "existing.focus()" in body, "缺重用 show+focus"
    # ready-to-show
    assert 'once("ready-to-show"' in body, "缺 ready-to-show 触发"
    assert "setTimeout(" in body and "3500" in body, "缺 3.5s 兜底亮相"
    # setWindowOpenHandler
    assert "setWindowOpenHandler" in body, "缺 setWindowOpenHandler"
    # close → hide
    assert 'w.on("close"' in body, "缺 close handler"
    assert "if (!quitting)" in body and "w.hide()" in body, "缺 quit 守卫 + hide"
    # closed → delete from Map
    assert 'w.on("closed"' in body, "缺 closed handler"
    assert "childWindows.delete(spec.label)" in body, "缺 childWindows.delete"
    # loadURL + set
    assert "w.loadURL(spec.url)" in body, "缺 loadURL"
    assert "childWindows.set(spec.label, w)" in body, "缺 childWindows.set"
    print("✓ _createChildWindow 7 个关键路径齐全")


def test_close_destroy_child_windows():
    """④ closeAllChildWindows / destroyAllChildWindows。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # close
    assert "function closeAllChildWindows()" in text, "缺 closeAllChildWindows"
    m1 = re.search(r"function closeAllChildWindows\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m1, "closeAllChildWindows 函数体不完整"
    assert "for (const w of childWindows.values())" in m1.group(1), "缺遍历 childWindows"
    assert "w.hide()" in m1.group(1), "closeAllChildWindows 应调 hide"
    # destroy
    assert "function destroyAllChildWindows()" in text, "缺 destroyAllChildWindows"
    m2 = re.search(r"function destroyAllChildWindows\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert m2, "destroyAllChildWindows 函数体不完整"
    assert "w.destroy()" in m2.group(1), "destroyAllChildWindows 应调 destroy"
    assert "childWindows.clear()" in m2.group(1), "destroyAllChildWindows 应 clear Map"
    print("✓ closeAll / destroyAll 子窗口 API 齐全")


def test_before_quit_hook():
    """⑤ before-quit 钩 destroyAllChildWindows。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r'app\.on\("before-quit"[^)]*\)\s*=>\s*\{([^}]*)\}', text, re.DOTALL)
    assert m, "缺 before-quit handler"
    body = m.group(1)
    assert "quitting = true" in body, "before-quit 未置 quitting"
    assert "destroyAllChildWindows()" in body, "before-quit 未调 destroyAllChildWindows"
    print("✓ before-quit 钩置 quitting + destroyAll")


def test_child_spec():
    """⑥ _CHILD_SPEC 4 子窗口(语伴/音乐/📅日历/🔀工作流)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r"const _CHILD_SPEC\s*=\s*\{(.*?)\n\};", text, re.DOTALL)
    assert m, "缺 _CHILD_SPEC 字典"
    body = m.group(1)
    for label in ("companion", "music", "calendar", "workflow"):
        assert f"{label}:" in body, f"_CHILD_SPEC 缺 {label}"
    # 中文 title
    for title in ("语伴", "音乐", "📅 日历", "🔀 工作流"):
        assert title in body, f"_CHILD_SPEC 缺 title '{title}'"
    # 尺寸字段
    assert "width:" in body and "height:" in body, "_CHILD_SPEC 缺尺寸"
    assert "minWidth:" in body, "_CHILD_SPEC 缺 minWidth"
    print("✓ _CHILD_SPEC 4 子窗口齐全")


def test_open_in_shell_branches():
    """⑦ openInShell 分支:label 缺/=main → 主窗口;非主 → _createChildWindow。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r"function openInShell\(([^)]*)\)\s*\{(.*?)\nconst _CHILD_SPEC", text, re.DOTALL)
    assert m, "openInShell 跟 _CHILD_SPEC 不连续"
    body = m.group(2)
    # 主窗口分支
    assert 'label === "main"' in body, "openInShell 缺 label==='main' 分支"
    assert "win.show()" in body and "win.focus()" in body, "openInShell 主窗分支缺 show+focus"
    assert "win.loadURL(url)" in body, "openInShell 主窗分支缺 loadURL"
    # 子窗口分支
    assert "_createChildWindow(" in body, "openInShell 缺子窗分支"
    assert "..._CHILD_SPEC[label]" in body, "openInShell 子窗未传 _CHILD_SPEC"
    print("✓ openInShell 双分支(main + 子窗)齐全")


def test_four_open_window_functions():
    """⑧ 4 个 openXxxWindow 函数(语伴/音乐/📅/🔀)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    for fn_name, port_call, label in [
        ("openCompanionWindow", "readCompanionPort", "companion"),
        ("openMusicWindow", "readMusicPort", "music"),
        ("openCalendarWindow", "readCalendarPort", "calendar"),
        ("openWorkflowWindow", None, "workflow"),
    ]:
        m = re.search(rf"function {fn_name}\(\)\s*\{{(.*?)\n\}}", text, re.DOTALL)
        assert m, f"缺 {fn_name}"
        body = m.group(1)
        if port_call:
            assert port_call in body, f"{fn_name} 缺 {port_call}"
        assert "openInShell(" in body, f"{fn_name} 未调 openInShell"
        assert f'"{label}"' in body, f"{fn_name} 未传 label='{label}'"
    # workflow 用 URL fragment
    m4 = re.search(r"function openWorkflowWindow\(\)\s*\{(.*?)\n\}", text, re.DOTALL)
    assert "#wfmodal" in m4.group(1), "openWorkflowWindow 未走 #wfmodal 锚点"
    print("✓ 4 openXxxWindow 函数齐全(语伴/音乐/📅/🔀)")


# ----------------------------------------------------------------------
# P2.5+17 tray icon 子菜单
# ----------------------------------------------------------------------
def test_create_tray_submenu_three_groups():
    """⑨ createTray() 3 组 submenu(主控 / 多窗口 / 系统)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # 三组 submenu 变量定义
    assert "const mainCtrlSubmenu" in text, "createTray 缺 mainCtrlSubmenu"
    assert "const multiWindowSubmenu" in text, "createTray 缺 multiWindowSubmenu"
    assert "const systemSubmenu" in text, "createTray 缺 systemSubmenu"
    assert "const trayItems" in text, "createTray 缺 trayItems"
    # 顶层 submenu 注册
    for top in ("主控", "多窗口", "系统"):
        assert f'label: "{top}"' in text, f"托盘顶层 submenu 缺 '{top}'"
    # submenu 字段嵌套
    for grp in ("mainCtrlSubmenu", "multiWindowSubmenu", "systemSubmenu"):
        assert f"submenu: {grp}" in text, f"顶层未 submenu:{grp}"
    print("✓ tray 3 组 submenu(主控/多窗口/系统)就位")


def test_main_ctrl_submenu():
    """⑩ 主控 submenu 含「打开 PrisirAI」+「隐藏 PrisirAI」。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r"const mainCtrlSubmenu\s*=\s*\[(.*?)\];", text, re.DOTALL)
    assert m, "mainCtrlSubmenu 不连续"
    body = m.group(1)
    assert "打开 PrisirAI" in body, "主控 submenu 缺「打开 PrisirAI」"
    assert "隐藏 PrisirAI" in body, "主控 submenu 缺「隐藏 PrisirAI」"
    # click 回调
    assert "win.show()" in body, "主控 submenu click 缺 win.show"
    assert "win.hide()" in body, "主控 submenu click 缺 win.hide"
    print("✓ 主控 submenu 含打开 + 隐藏")


def test_multi_window_submenu():
    """⑪ 多窗口 submenu 含 4 子项 + 「关闭所有子窗口」聚合。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r"const multiWindowSubmenu\s*=\s*\[(.*?)\];", text, re.DOTALL)
    assert m, "multiWindowSubmenu 不连续"
    body = m.group(1)
    for label in ("语伴", "音乐", "📅 日历", "🔀 工作流"):
        assert label in body, f"多窗口 submenu 缺 '{label}'"
    # 4 个 click 对应 4 个 open 函数
    for fn in ("openCompanionWindow", "openMusicWindow", "openCalendarWindow", "openWorkflowWindow"):
        assert fn in body, f"多窗口 submenu 缺 click={fn}"
    # separator + 关闭所有
    assert '{ type: "separator" }' in body, "多窗口 submenu 缺 separator"
    assert "关闭所有子窗口" in body, "多窗口 submenu 缺「关闭所有子窗口」"
    assert "closeAllChildWindows" in body, "多窗口 submenu 关闭按钮未调 closeAllChildWindows"
    print("✓ 多窗口 submenu 含 4 子项 + 关闭所有子窗口")


def test_system_submenu():
    """⑫ 系统 submenu 含开机自启 checkbox + 退出。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    m = re.search(r"const systemSubmenu\s*=\s*\[(.*?)\];", text, re.DOTALL)
    assert m, "systemSubmenu 不连续"
    body = m.group(1)
    assert "开机自启" in body, "系统 submenu 缺「开机自启」"
    assert 'type: "checkbox"' in body, "开机自启未用 checkbox 类型"
    assert "app.getLoginItemSettings().openAtLogin" in body, "开机自启未读 openAtLogin"
    assert "app.setLoginItemSettings" in body, "开机自启未写 setLoginItemSettings"
    assert "退出" in body, "系统 submenu 缺「退出」"
    assert "quitting = true" in body and "app.quit()" in body, "退出未置 quitting+app.quit"
    print("✓ 系统 submenu 含开机自启 + 退出")


def test_dev_mode_top_level():
    """⑬ 开发者模式独立顶层菜单项(if devModeAvailable),不进任何 submenu。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # 用同缩进的独立 } 锚闭合(2 空格),不被内联 push({}) 误停
    m = re.search(r"if \(devModeAvailable\(\)\)\s*\{(.*?)^  \}", text, re.DOTALL | re.MULTILINE)
    assert m, "createTray 缺开发者模式守卫"
    body = m.group(1)
    assert "开发者模式" in body, "开发者模式菜单缺中文 label"
    assert "trayItems.push" in body, "开发者模式未 push 到 trayItems"
    assert "打开开发者终端" in body or "git-portable" in body, "开发者模式缺子项"
    # 子项不进任何 submenu(独立顶层)
    # 验证:开发者模式 submenu 紧跟 trayItems.push,作为独立项
    assert '{ label: "开发者模式", submenu:' in body, "开发者模式应是独立顶层 submenu"
    print("✓ 开发者模式独立顶层菜单项就位")


def test_tray_build():
    """⑭ tray.setContextMenu(Menu.buildFromTemplate(trayItems)) 收口。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    assert "tray.setContextMenu(Menu.buildFromTemplate(trayItems))" in text, \
        "createTray 缺 setContextMenu 收口"
    # click 仍走 toggleWindow
    assert 'tray.on("click", toggleWindow)' in text, "createTray 缺 click toggleWindow"
    print("✓ tray.setContextMenu 收口正确")


# ----------------------------------------------------------------------
# 工具检查
# ----------------------------------------------------------------------
def test_node_check():
    """⑮ node --check main.js 语法 OK。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    r = subprocess.run(["node", "--check", str(p)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, f"node --check fail: {r.stderr}"
    print("✓ node --check main.js OK")


def test_no_legacy_inline_calls():
    """⑯ 原 P2.5+13 的 3 个内联 openXxxWindow 旧实现不再残留(防止重复定义)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # 不应再有「if (port <= 0) openInShell(WEB_URL, "main")」这种 P2.5+13 内联模式
    # 仅在 openMusicWindow 里允许一次(兜底)
    legacy_inline_count = text.count('openInShell(WEB_URL, "main")')
    assert legacy_inline_count <= 1, \
        f"openInShell(WEB_URL, 'main') 出现 {legacy_inline_count} 次,预期 ≤ 1(music 兜底)"
    # 不应再调用老的 openXxxWindow 闭包(无参 + 直接打开)
    # 4 个函数应都是 require('./port_config').readXxxPort() 形式
    for fn in ("openCompanionWindow", "openCalendarWindow"):
        m = re.search(rf"function {fn}\(\)\s*\{{(.*?)\n\}}", text, re.DOTALL)
        if m:
            assert "port_config" in m.group(1) and "read" in m.group(1), \
                f"{fn} 未走 port_config.readXxxPort"
    print("✓ 4 个 open 函数走 port_config,无残留内联")


def test_no_broken_port_readers():
    """⑰ port_config.js 提供 readCompanionPort / readMusicPort / readCalendarPort。"""
    p = REPO_ROOT / "prisiragent-shell" / "port_config.js"
    text = _read(p)
    for fn in ("readCompanionPort", "readMusicPort", "readCalendarPort"):
        assert f"const {fn}" in text or f"function {fn}" in text, \
            f"port_config.js 缺 {fn}"
        # 暴露给 module.exports
        assert fn in text.split("module.exports")[1], \
            f"port_config.js {fn} 未 module.exports 暴露"
    print("✓ port_config 暴露 3 个 readXxxPort")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main() -> int:
    tests = [
        # P2.5+16 子窗口分离(8 项)
        test_child_windows_map,
        test_common_web_preferences,
        test_create_child_window,
        test_close_destroy_child_windows,
        test_before_quit_hook,
        test_child_spec,
        test_open_in_shell_branches,
        test_four_open_window_functions,
        # P2.5+17 tray icon 子菜单(6 项)
        test_create_tray_submenu_three_groups,
        test_main_ctrl_submenu,
        test_multi_window_submenu,
        test_system_submenu,
        test_dev_mode_top_level,
        test_tray_build,
        # 工具检查(3 项)
        test_node_check,
        test_no_legacy_inline_calls,
        test_no_broken_port_readers,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {t.__name__} (exception): {type(e).__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\n{'='*60}\nP2.5+16 + P2.5+17 子窗口分离 + tray 子菜单 — {passed} / {total} 项绿, 失败 {failed} 项\n{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
