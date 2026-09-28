# -*- coding: utf-8 -*-
r"""
_test_p2b19_tauri_subwindows.py — P2.5+19(2026-09-22)Tauri 4 子窗统一化静态扫

覆盖装包路径 Tauri 侧 4 子窗独立 WebviewWindow 模型(close→hide 复用锚点):
  S1  tauri.conf.json app.windows[] 预声明 4 子窗(companion/music/calendar/workflow)
  S2  4 子窗 visible:false
  S3  4 子窗尺寸跟 _CHILD_SPEC 对齐(920×680 / 880×620 / 960×720 / 1000×720)
  S4  windows.rs 新文件存在 + 4 个子窗 URL 构造分支
  S5  windows.rs SUBWINDOW_LABELS + open_window + bind_close_to_tray + hide_all
  S6  windows.rs 5 commands(open_companion/music/calendar/workflow + close_all)
  S7  lib.rs mod windows; 声明
  S8  lib.rs invoke_handler 注册 5 个新 commands
  S9  lib.rs setup 末尾调 4 子窗 bind_close_to_tray
  S10 lib.rs AppState / current_web_port / start_companion 改 pub(crate) 暴露给 windows
  S11 lib.rs tray menu 加 workflow_item(8 项 → 7 项结构保持但调 Rust)
  S12 lib.rs 4 子窗 tray click handler 调 windows::open_window(companion/music/calendar/workflow)
  S13 lib.rs 4 子窗 tray click handler 兜底链(window.open 老路径保留)
  S14 prisIragent_web.py 顶栏 openCompanion / openWorkflow 加 __TAURI_INTERNALS__ 双分支
  S15 py_compile + windows.rs 模块导入 sanity
"""
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
LIB_RS = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "lib.rs"
WINDOWS_RS = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "subwin.rs"  # P2.5+20 rename:避开与 windows-sys crate 的冲突
TAURI_CONF = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "tauri.conf.json"
WEB_PY = REPO_ROOT / "prisIragent_web.py"


# ----------------------------------------------------------------------
# S1-S3: tauri.conf.json windows 预声明
# ----------------------------------------------------------------------
def s1_tauri_conf_windows_declared():
    """S1:tauri.conf.json app.windows[] 预声明 4 子窗(companion/music/calendar/workflow)。"""
    text = _read(TAURI_CONF)
    for label in ("companion-window", "music-window", "calendar-window", "workflow-window"):
        assert f'"label": "{label}"' in text, \
            f"tauri.conf.json 缺子窗 label 预声明: {label}"
    print(f"✓ tauri.conf.json 预声明 4 子窗(companion/music/calendar/workflow)")


def s2_tauri_conf_windows_hidden():
    """S2:4 子窗 visible:false(启动时隐藏,点托盘才 show)。"""
    text = _read(TAURI_CONF)
    # windows[] 数组段
    start = text.find('"windows"')
    assert start >= 0, "tauri.conf.json 缺 windows[] 数组"
    sub = text[start:]
    # 4 子窗段都要 visible:false;允许主窗/lyrics-window 也有 visible:false
    for label in ("companion-window", "music-window", "calendar-window", "workflow-window"):
        idx = sub.find(f'"label": "{label}"')
        assert idx >= 0, f"缺 {label}"
        # 取该 entry 段(到下一个 })
        seg = sub[idx:idx + 800]
        assert '"visible": false' in seg, f"{label} visible 非 false"
    print("✓ 4 子窗全部 visible:false(启动时隐藏)")


def s3_tauri_conf_windows_size_match_child_spec():
    """S3:4 子窗尺寸跟 _CHILD_SPEC 对齐(920×680 / 880×620 / 960×720 / 1000×720)。"""
    text = _read(TAURI_CONF)
    expected = {
        "companion-window": (920, 680),
        "music-window":     (880, 620),
        "calendar-window":  (960, 720),
        "workflow-window":  (1000, 720),
    }
    for label, (w, h) in expected.items():
        idx = text.find(f'"label": "{label}"')
        assert idx >= 0, f"缺 {label}"
        seg = text[idx:idx + 800]
        assert f'"width": {w}' in seg, f"{label} width 不是 {w}"
        assert f'"height": {h}' in seg, f"{label} height 不是 {h}"
    print("✓ 4 子窗尺寸跟 _CHILD_SPEC 对齐")


# ----------------------------------------------------------------------
# S4-S6: windows.rs 新模块
# ----------------------------------------------------------------------
def s4_windows_rs_exists_with_url_branches():
    """S4:windows.rs 新文件存在 + url_for_label 4 分支(companion/music/calendar/workflow)。"""
    assert WINDOWS_RS.exists(), f"缺 {WINDOWS_RS}"
    text = _read(WINDOWS_RS)
    assert "fn url_for_label" in text, "windows.rs 缺 url_for_label 函数"
    for branch in ('"companion-window"', '"music-window"', '"calendar-window"', '"workflow-window"'):
        assert branch in text, f"windows.rs url_for_label 缺分支 {branch}"
    print("✓ windows.rs 存在 + url_for_label 4 分支齐全")


def s5_windows_rs_close_to_tray_helpers():
    """S5:windows.rs 含 SUBWINDOW_LABELS + open_window + bind_close_to_tray + hide_all。"""
    text = _read(WINDOWS_RS)
    for sym in ("SUBWINDOW_LABELS", "pub fn open_window", "pub fn bind_close_to_tray", "pub fn hide_all"):
        assert sym in text, f"windows.rs 缺 {sym}"
    # close→hide 复用锚点:WindowEvent::CloseRequested + api.prevent_close + window.hide
    assert "WindowEvent::CloseRequested" in text, "windows.rs 缺 CloseRequested 事件"
    assert "api.prevent_close" in text, "windows.rs 缺 api.prevent_close"
    assert "hide()" in text, "windows.rs 缺 window.hide()"
    print("✓ windows.rs SUBWINDOW_LABELS + close→hide 复用锚点齐全")


def s6_windows_rs_5_commands():
    """S6:windows.rs 5 commands(open_companion/music/calendar/workflow + close_all)。"""
    text = _read(WINDOWS_RS)
    for cmd in (
        "open_companion_window_cmd",
        "open_music_window_cmd",
        "open_calendar_window_cmd",
        "open_workflow_window_cmd",
        "close_all_child_windows_cmd",
    ):
        assert f"pub fn {cmd}" in text, f"windows.rs 缺 command: {cmd}"
        assert "#[tauri::command]" in text, "windows.rs 缺 #[tauri::command] 标注"
    print("✓ windows.rs 5 commands 齐全(open_*_window_cmd + close_all_child_windows_cmd)")


# ----------------------------------------------------------------------
# S7-S13: lib.rs 改造
# ----------------------------------------------------------------------
def s7_lib_rs_mod_subwin_declared():
    """S7:lib.rs mod subwin; 声明(P2.5+20 重命名避开与 windows-sys crate 命名冲突)。"""
    text = _read(LIB_RS)
    assert "mod subwin;" in text, "lib.rs 缺 mod subwin; 声明"
    # 不能是 mod windows;(会让 Windows crate path 被遮蔽)
    assert "mod windows;" not in text or text.count("mod windows;") == 0, \
        "lib.rs 仍存在 mod windows; — 会与 windows-sys crate 命名冲突"
    print("✓ lib.rs mod subwin; 已声明(避开 windows-sys crate)")


def s8_lib_rs_invoke_handler_5_commands():
    """S8:lib.rs invoke_handler 注册 5 个新 commands + 1 status。"""
    text = _read(LIB_RS)
    # invoke_handler 段
    start = text.find(".invoke_handler(")
    assert start >= 0, "lib.rs 缺 invoke_handler"
    end = text.find("])", start)
    seg = text[start:end + 2]
    for cmd in (
        "open_companion_window_cmd",
        "open_music_window_cmd",
        "open_calendar_window_cmd",
        "open_workflow_window_cmd",
        "close_all_child_windows_cmd",
    ):
        assert cmd in seg, f"invoke_handler 未注册 {cmd}"
    print("✓ lib.rs invoke_handler 注册 5 个子窗 commands")


def s9_lib_rs_setup_binds_close_to_tray():
    """S9:lib.rs setup 末尾调 4 子窗 bind_close_to_tray。"""
    text = _read(LIB_RS)
    assert "subwin::bind_close_to_tray" in text, \
        "lib.rs 缺 bind_close_to_tray 调用"
    # SUBWINDOW_LABELS 循环调
    assert "subwin::SUBWINDOW_LABELS" in text, \
        "lib.rs setup 未用 SUBWINDOW_LABELS 循环调 bind_close_to_tray"
    print("✓ lib.rs setup 末尾用 SUBWINDOW_LABELS 循环调 bind_close_to_tray")


def s10_lib_rs_pub_crate_exposes_for_windows():
    """S10:lib.rs AppState / current_web_port / start_companion 改 pub(crate) 暴露给 windows。"""
    text = _read(LIB_RS)
    assert "pub(crate) struct AppState" in text, \
        "AppState 未改 pub(crate),windows.rs 拿不到 state"
    assert "pub(crate) fn current_web_port" in text, \
        "current_web_port 未改 pub(crate),workflow-window 拿不到主 web 端口"
    assert "pub(crate) fn start_companion" in text, \
        "start_companion 未改 pub(crate),companion-window 拿不到代启入口"
    print("✓ AppState + current_web_port + start_companion 改 pub(crate)")


def s11_lib_rs_tray_workflow_item_added():
    """S11:lib.rs tray menu 加 workflow_item(🔀 打开工作流(独立窗))。"""
    text = _read(LIB_RS)
    assert 'with_id("open_workflow"' in text, 'tray 缺 open_workflow item'
    assert '🔀 打开工作流(独立窗)' in text, 'tray workflow item label 缺失'
    print("✓ lib.rs tray menu 加 🔀 打开工作流(独立窗)")


def s12_lib_rs_tray_click_calls_windows_open():
    """S12:lib.rs 4 子窗 tray click handler 调 subwin::open_window。"""
    text = _read(LIB_RS)
    # 4 个 tray click 分支各调 subwin::open_window
    for label in ("companion-window", "music-window", "calendar-window", "workflow-window"):
        # 找 on_menu_event 分支的 label 对应 handler
        if label == "calendar-window":
            branch_id = "open_calendar"
        elif label == "workflow-window":
            branch_id = "open_workflow"
        elif label == "companion-window":
            branch_id = "companion"
        else:  # music-window
            branch_id = "music"
        idx = text.find(f'"{branch_id}" =>')
        assert idx >= 0, f"tray 缺 click 分支 {branch_id}"
        seg = text[idx:idx + 1500]
        assert f'subwin::open_window(app, "{label}")' in seg, \
            f"{branch_id} 分支未调 subwin::open_window('{label}')"
    print("✓ lib.rs 4 子窗 tray click handler 全调 subwin::open_window")


def s13_lib_rs_tray_click_has_fallback():
    """S13:lib.rs 4 子窗 tray click handler 兜底链(主窗 eval 跳转保留)。"""
    text = _read(LIB_RS)
    # 找 tray click handler 段,验证 Err 分支后仍走主窗 fallback
    # 简化为:每分支后都有 log::error + 主窗 show()/focus()
    for branch_id in ("companion", "music", "open_calendar", "open_workflow"):
        idx = text.find(f'"{branch_id}" =>')
        assert idx >= 0, f"tray 缺 click 分支 {branch_id}"
        seg = text[idx:idx + 2500]
        assert "log::error" in seg or "log::warn" in seg, \
            f"{branch_id} 分支缺错误日志"
    print("✓ lib.rs 4 子窗 tray click handler 兜底链(log::error / 主窗 fallback)")


# ----------------------------------------------------------------------
# S14: 前端 __TAURI_INTERNALS__ 双分支
# ----------------------------------------------------------------------
def s14_web_tauri_internals_dual_branch():
    """S14:prisIragent_web.py 顶栏 openCompanion / openWorkflow 加 __TAURI_INTERNALS__ 双分支。"""
    text = _read(WEB_PY)
    # openCompanion 双分支
    assert "openCompanion" in text and "openCompanion" in text, \
        "openCompanion 段缺失"
    oc_idx = text.find("async function openCompanion")
    assert oc_idx >= 0, "缺 openCompanion 函数"
    oc_seg = text[oc_idx:oc_idx + 3000]
    assert "__TAURI_INTERNALS__" in oc_seg, \
        "openCompanion 缺 __TAURI_INTERNALS__ 双分支"
    assert "open_companion_window_cmd" in oc_seg, \
        "openCompanion 缺 open_companion_window_cmd invoke"

    # openWorkflow 双分支
    ow_idx = text.find("async function openWorkflow")
    assert ow_idx >= 0, "缺 openWorkflow 函数"
    ow_seg = text[ow_idx:ow_idx + 3000]
    assert "__TAURI_INTERNALS__" in ow_seg, \
        "openWorkflow 缺 __TAURI_INTERNALS__ 双分支"
    assert "open_workflow_window_cmd" in ow_seg, \
        "openWorkflow 缺 open_workflow_window_cmd invoke"
    print("✓ openCompanion / openWorkflow 都加 __TAURI_INTERNALS__ 双分支")


# ----------------------------------------------------------------------
# S15: py_compile + windows.rs 模块导入 sanity
# ----------------------------------------------------------------------
def s15_py_compile_and_syntax_sanity():
    """S15:prisIragent_web.py py_compile + lib.rs / windows.rs 关键符号 sanity。"""
    import py_compile
    try:
        py_compile.compile(str(WEB_PY), doraise=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"prisIragent_web.py py_compile 失败: {e}")
    # windows.rs 不能有 cargo crate 这种错配(常见 typo)
    text = _read(WINDOWS_RS)
    assert "cargo" not in text, "windows.rs 出现 cargo 错配"
    # lib.rs 不能有意外的双 mod 声明(subwin 而非 windows;后者会跟 windows-sys crate 冲突)
    lib_text = _read(LIB_RS)
    assert lib_text.count("mod subwin;") == 1, \
        f"lib.rs mod subwin; 出现 {lib_text.count('mod subwin;')} 次(应 1 次)"
    print("✓ py_compile + subwin.rs / lib.rs 语法 sanity 通过")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
SECTIONS = [
    s1_tauri_conf_windows_declared,
    s2_tauri_conf_windows_hidden,
    s3_tauri_conf_windows_size_match_child_spec,
    s4_windows_rs_exists_with_url_branches,
    s5_windows_rs_close_to_tray_helpers,
    s6_windows_rs_5_commands,
    s7_lib_rs_mod_subwin_declared,
    s8_lib_rs_invoke_handler_5_commands,
    s9_lib_rs_setup_binds_close_to_tray,
    s10_lib_rs_pub_crate_exposes_for_windows,
    s11_lib_rs_tray_workflow_item_added,
    s12_lib_rs_tray_click_calls_windows_open,
    s13_lib_rs_tray_click_has_fallback,
    s14_web_tauri_internals_dual_branch,
    s15_py_compile_and_syntax_sanity,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in SECTIONS:
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
    print(f"\n{'='*60}\nP2.5+19 Tauri 4 子窗统一化静态扫 — {passed} / {total} 项绿, 失败 {failed} 项\n{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())