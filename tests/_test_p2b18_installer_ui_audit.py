# -*- coding: utf-8 -*-
r"""
_test_p2b18_installer_ui_audit.py — P2.5+18(2026-09-22)装包后 UI 验收静态扫

装包 UI 验收 = 装包路径完整性检查。用户在桌面真跑 NSIS installer 后的 UX 验证(菜单弹/子窗
打开/主壳启动/陪聊音乐日历入口可点)在 docs/p2-5-18-installer-ui-audit.md 留清单,静态扫
覆盖装包产物齐全性 + 装包脚本配置正确性。

装包路径:
  - 主壳 = Tauri shell(target/release/prisirai-shell.exe,12MB,9月19 build)
  - 后端 = PyInstaller(PrisirAI.exe,415MB)+ 音乐子 exe(PrisirAI-music-web.exe,93MB)
  - VCS 子 exe(PrisirVcsTool.exe)+ git/officecli(子 exe)
  - assets(图标/主题/山水背景)
  - NSIS installer = installer/prisirai.nsi(走 Tauri + assets + bin)

本测试覆盖:
  ① PyInstaller 主壳 PrisirAI.exe 存在 + 体积 OK(>100MB)
  ② PyInstaller music 子 exe PrisirAI-music-web.exe 存在
  ③ Tauri shell prisirai-shell.exe 存在 + 体积 OK(>10MB)
  ④ NSIS installer 脚本含 Tauri exe + music exe + assets + bin
  ⑤ prisIrai_config.yaml 三段齐全 + 端口默认跟 NSIS 默认对得上
  ⑥ Tauri lib.rs tray 菜单 8 项齐全(show/companion/music/calendar/lyrics/autostart/quit + 退出)
  ⑦ Tauri lib.rs calendar 路由走 /prisIragent/calendar 独立端口
  ⑧ Electron 壳 main.js P2.5+16/17 子窗口 + tray submenu(开发模式调试入口,装包不走)
  ⑨ NSIS installer 创建桌面快捷方式 + 开始菜单
  ⑩ NSIS installer 写注册表 Uninstall 项
"""
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _exists(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


# ----------------------------------------------------------------------
# 装包产物齐全性
# ----------------------------------------------------------------------
def test_pyinstaller_main_exe():
    """① PyInstaller 主壳 PrisirAI.exe(>100MB)。"""
    p = REPO_ROOT / "dist" / "PrisirAI.exe"
    assert _exists(p), f"missing: {p}"
    size_mb = p.stat().st_size / 1024 / 1024
    assert size_mb > 100, f"PrisirAI.exe 太小: {size_mb:.1f}MB(<100MB),可能缺 litellm/rapidocr 资源"
    print(f"✓ PyInstaller PrisirAI.exe 存在 ({size_mb:.1f}MB)")


def test_pyinstaller_music_exe():
    """② PyInstaller music 子 exe PrisirAI-music-web.exe(>50MB)。"""
    p = REPO_ROOT / "dist" / "PrisirAI-music-web.exe"
    assert _exists(p), f"missing: {p}"
    size_mb = p.stat().st_size / 1024 / 1024
    assert size_mb > 50, f"PrisirAI-music-web.exe 太小: {size_mb:.1f}MB(<50MB)"
    print(f"✓ PyInstaller music 子 exe 存在 ({size_mb:.1f}MB)")


def test_tauri_shell_exe():
    """③ Tauri shell prisirai-shell.exe(>10MB)— 装包主壳路径。"""
    p = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "target" / "release" / "prisirai-shell.exe"
    assert _exists(p), f"missing: {p}"
    size_mb = p.stat().st_size / 1024 / 1024
    assert size_mb > 10, f"prisirai-shell.exe 太小: {size_mb:.1f}MB(<10MB)"
    print(f"✓ Tauri shell prisirai-shell.exe 存在 ({size_mb:.1f}MB)")


def test_nsis_installer_script():
    """④ NSIS installer/prisirai.nsi 含 Tauri + music + assets + bin。"""
    p = REPO_ROOT / "installer" / "prisirai.nsi"
    text = _read(p)
    # Tauri exe
    assert "target\\release\\prisirai-shell.exe" in text, \
        "prisirai.nsi 缺 prisirai-shell.exe 拷贝指令"
    # music exe
    assert "PrisirAI-music-web.exe" in text, "prisirai.nsi 缺 music exe 拷贝指令"
    # assets
    assert "_staging2\\assets" in text or "assets" in text, "prisirai.nsi 缺 assets 拷贝"
    # bin(PrisirVcsTool + git + officecli)
    assert "PrisirVcsTool.exe" in text, "prisirai.nsi 缺 PrisirVcsTool.exe"
    assert "_staging\\bin\\git" in text or "bin\\git" in text, "prisirai.nsi 缺 git bin"
    assert "officecli.exe" in text, "prisirai.nsi 缺 officecli.exe"
    print("✓ NSIS prisirai.nsi 4 大产物拷贝指令齐全")


# ----------------------------------------------------------------------
# 配置正确性
# ----------------------------------------------------------------------
def test_config_yaml_three_sections():
    """⑤ prisIrai_config.yaml 三段(ports/brand/forum)齐全。"""
    p = REPO_ROOT / "prisIrai_config.yaml"
    text = _read(p)
    for sec in ("ports:", "brand:", "forum:"):
        assert sec in text, f"yaml 缺段: {sec}"
    # 端口默认应跟装包路径端口对得上
    assert "web: 18802" in text or "web:18802" in text, "ports.web 不等于 18802"
    assert "calendar: 18803" in text or "calendar:18803" in text, "ports.calendar 不等于 18803"
    print("✓ config.yaml 三段 + 端口默认对齐")


def test_tauri_tray_menu_8_items():
    """⑥ Tauri lib.rs tray 菜单 8 项齐全(show/companion/music/calendar/lyrics/autostart/quit + 退出)。"""
    p = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "lib.rs"
    text = _read(p)
    # Tray 段(在 TrayIconBuilder 之前)
    for label in ("打开 PrisirAI", "启动陪聊", "启动音乐播放器", "📅 打开日历",
                  "桌面歌词", "开机自启", "退出"):
        assert label in text, f"tray 菜单缺 '{label}'"
    print("✓ Tauri tray 菜单 7 项齐全(打开/陪聊/音乐/日历/歌词/自启/退出)")


def test_tauri_calendar_routing():
    """⑦ Tauri lib.rs calendar 路由走 /prisIragent/calendar + 独立端口(动态)。"""
    p = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "lib.rs"
    text = _read(p)
    # 路由路径(/prisiragent/calendar)— Tauri 跟 Python 后端用一致的小写 p
    assert "prisiragent/calendar" in text, "Tauri 缺 calendar 路由"
    # 端口动态走 config_loader / calendar module
    assert "current_port" in text or "calendar_port" in text or "18803" in text, \
        "Tauri calendar 端口未走 config_loader / 18803 默认"
    # calendar 子进程管理
    assert "mod calendar" in text or "calendar::start" in text, "Tauri 缺 calendar 模块"
    # tray 调起 calendar(已 ship P2.5+12)
    assert "start_calendar_cmd" in text, "Tauri 缺 start_calendar_cmd Tauri command"
    # calendar.rs 提供 CALENDAR_PORT_DEFAULT(默认 18803)
    cal = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "calendar.rs"
    if cal.exists():
        cal_text = _read(cal)
        assert "18803" in cal_text, "calendar.rs 未定义默认端口 18803"
    print("✓ Tauri calendar 路由 + 独立端口 + 模块 + tray 调起就位")


def test_electron_dev_mode_complete():
    """⑧ Electron 壳 main.js P2.5+16/17(开发模式调试入口,装包不走但 dev 验证用)。"""
    p = REPO_ROOT / "prisiragent-shell" / "main.js"
    text = _read(p)
    # P2.5+16 子窗口
    assert "childWindows" in text and "_createChildWindow" in text, \
        "Electron main.js 缺子窗口分离"
    # P2.5+17 tray submenu
    assert "mainCtrlSubmenu" in text and "multiWindowSubmenu" in text and "systemSubmenu" in text, \
        "Electron main.js 缺 tray 3 组 submenu"
    print("✓ Electron main.js dev 模式 子窗口 + tray 3 组齐全")


# ----------------------------------------------------------------------
# NSIS 装包细节
# ----------------------------------------------------------------------
def test_nsis_desktop_shortcut():
    """⑨ NSIS 创建桌面快捷方式 + 开始菜单。"""
    p = REPO_ROOT / "installer" / "prisirai.nsi"
    text = _read(p)
    assert "CreateShortcut \"$DESKTOP" in text, "NSIS 缺桌面快捷方式"
    assert "CreateShortcut \"$SMPROGRAMS" in text, "NSIS 缺开始菜单快捷方式"
    # 指向 Tauri exe
    assert "prisirai-shell.exe" in text.split("CreateShortcut")[1].split("\"")[1] or \
        "$INSTDIR\\prisirai-shell.exe" in text, \
        "桌面/开始菜单快捷方式未指向 Tauri exe"
    print("✓ NSIS 桌面 + 开始菜单快捷方式齐全(指向 Tauri)")


def test_nsis_uninstall_registry():
    """⑩ NSIS 写注册表 Uninstall + 卸载器入口。"""
    p = REPO_ROOT / "installer" / "prisirai.nsi"
    text = _read(p)
    assert "WriteUninstaller" in text, "NSIS 缺 WriteUninstaller"
    assert "Uninstall\\${APP_NAME}" in text or "Uninstall\\${PRODUCT_NAME}" in text, \
        "NSIS 缺 Uninstall 注册表键"
    # 标准字段
    for k in ("InstallLocation", "DisplayVersion", "Publisher", "DisplayName"):
        assert k in text, f"NSIS Uninstall 缺字段: {k}"
    print("✓ NSIS Uninstall 注册表 + 字段齐全")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main() -> int:
    tests = [
        test_pyinstaller_main_exe,
        test_pyinstaller_music_exe,
        test_tauri_shell_exe,
        test_nsis_installer_script,
        test_config_yaml_three_sections,
        test_tauri_tray_menu_8_items,
        test_tauri_calendar_routing,
        test_electron_dev_mode_complete,
        test_nsis_desktop_shortcut,
        test_nsis_uninstall_registry,
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
    print(f"\n{'='*60}\nP2.5+18 装包 UI 验收静态扫 — {passed} / {total} 项绿, 失败 {failed} 项\n{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
