"""stagehand 风格 a11y + screenshot 联合 extract — OI 看屏增强

参考 github.com/browserbase/stagehand v3.5.0:
  extract({screenshot: true}) 同时输出 viewport 图 + a11y tree,让 vision LLM 双输入交叉定位

OI 看屏模块当前只有 mss 截图,对 Shadow DOM / iframe / 复杂 web 页面识别失败率高。
这个增强器补 a11y tree(Windows UI Automation Tree),让 LLM 同时拿到:
  - 截图(视觉)
  - UI 树(结构 + [Name] [ControlType] [selected] [checked] 标记)

源码:`vendor/peekaboo/screen.py` + `vendor/peekaboo/window.py`(截屏 + 窗口管理)
新增依赖:`uiautomation`(Windows 原生)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# vision 复用
_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "peekaboo"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _try_import_vision():
    try:
        import importlib.util
        for name in ["screen", "window"]:
            spec = importlib.util.spec_from_file_location(
                f"peekaboo_{name}", _VENDOR / f"{name}.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            globals()[name.capitalize()] = mod.ScreenCapture if name == "screen" else mod.WindowManager
    except Exception as e:
        print(f"[a11y_extract] vendor load fail: {e}")


_try_import_vision()

try:
    import uiautomation as auto
    _HAVE_UIA = True
except ImportError as e:
    _HAVE_UIA = False
    print(f"[a11y_extract] uiautomation 不可用: {e}")


# ============================================================
# UI Automation Tree walker
# ============================================================

def _walk_a11y(control, depth: int = 0, max_depth: int = 5) -> str:
    """递归 walk UI Automation Tree,生成可读文本"""
    if control is None or depth > max_depth:
        return ""
    lines = []
    indent = "  " * depth

    # 关键属性
    try:
        name = control.Name or ""
        control_type = control.ControlTypeName if hasattr(control, 'ControlTypeName') else ""
        # 自动加状态标记
        flags = []
        if hasattr(control, 'IsSelected') and control.IsSelected:
            flags.append("[selected]")
        if hasattr(control, 'IsChecked') and control.IsChecked:
            flags.append("[checked]")
        if hasattr(control, 'IsEnabled') and not control.IsEnabled:
            flags.append("[disabled]")
        flag_str = "".join(flags)
        # 短输出:name 长就截断
        name_short = name[:60] + "..." if len(name) > 60 else name
        if name_short or control_type:
            lines.append(f"{indent}<{control_type}> {name_short} {flag_str}".rstrip())
    except Exception:
        pass

    # 递归子节点
    try:
        for child in control.GetChildren():
            lines.append(_walk_a11y(child, depth + 1, max_depth))
    except Exception:
        pass

    return "\n".join(s for s in lines if s)


def extract_a11y(window_title: str = None, max_depth: int = 5) -> dict:
    """从指定窗口提取 UI Automation Tree

    Args:
        window_title: 窗口名(如 'Notepad' / 'team-web'),None = 当前焦点窗口
        max_depth: 递归深度上限

    Returns:
        {"status": "ok", "title": ..., "a11y": "...", "element_count": N}
    """
    if not _HAVE_UIA:
        return {"status": "unavailable", "reason": "uiautomation not installed"}
    try:
        # 找目标窗口
        if window_title:
            win = auto.WindowControl(searchDepth=2, Name=window_title)
        else:
            win = auto.GetFocusedControl().GetTopLevelControl()
        if win is None or not win.Exists():
            return {"status": "not_found", "title": window_title or "(focused)"}
        # walk
        tree_text = _walk_a11y(win, depth=0, max_depth=max_depth)
        element_count = tree_text.count("\n") + (1 if tree_text else 0)
        return {
            "status": "ok",
            "title": getattr(win, 'Name', '') or window_title,
            "a11y": tree_text,
            "element_count": element_count,
            "max_depth": max_depth,
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ============================================================
# 联合 extract — a11y + screenshot(stagehand v3.5.0 风格)
# ============================================================

def extract_with_a11y(
    window_title: str = None,
    screenshot_max_width: int = 1024,
    a11y_max_depth: int = 5,
    save_screenshot_to: Optional[str] = None,
) -> dict:
    """同时输出截图 + a11y tree(stagehand extract({screenshot: true}) 风格)

    适合发给 vision LLM 双输入交叉定位(对 Shadow DOM / iframe 显著更稳)
    """
    result = {
        "status": "ok",
        "window_title": window_title,
        "screenshot": None,
        "a11y": None,
        "format": "stagehand-v3-compat",
    }
    # 1. a11y
    a11y = extract_a11y(window_title=window_title, max_depth=a11y_max_depth)
    result["a11y"] = a11y.get("a11y")
    result["a11y_count"] = a11y.get("element_count", 0)
    if a11y.get("status") != "ok":
        result["a11y_status"] = a11y["status"]
        result["a11y_error"] = a11y.get("reason", "")

    # 2. screenshot
    if "ScreenCapture" in globals() and ScreenCapture is not None:
        try:
            sc = ScreenCapture()
            try:
                if save_screenshot_to:
                    cap = sc.capture_screen(monitor_index=0, save_path=save_screenshot_to)
                else:
                    cap = sc.get_thumbnail(monitor_index=0, max_width=screenshot_max_width)
                result["screenshot"] = {
                    "saved_path": cap.get("saved_path"),
                    "base64": cap.get("base64"),
                    "bounds": cap.get("bounds"),
                }
            finally:
                sc.sct.close()
        except Exception as e:
            result["screenshot_status"] = "error"
            result["screenshot_error"] = str(e)
    else:
        result["screenshot_status"] = "unavailable"

    return result


if __name__ == "__main__":
    import json
    print("=== extract_a11y(None) 当前焦点窗口 ===")
    r = extract_a11y()
    if r["status"] == "ok":
        print(f"  title: {r['title']}")
        print(f"  elements: {r['element_count']}")
        print(f"  a11y 前 500 chars:\n{r['a11y'][:500]}")
    else:
        print(f"  status: {r}")

    print("\n=== extract_with_a11y('team-web') ===")
    r = extract_with_a11y(window_title="team-web", screenshot_max_width=320)
    if r.get("a11y_status") == "ok" or r.get("a11y"):
        print(f"  a11y 元素数: {r.get('a11y_count')}")
        print(f"  a11y 前 300 chars:\n{(r.get('a11y') or '')[:300]}")
    if r.get("screenshot", {}).get("base64"):
        print(f"  screenshot base64: {len(r['screenshot']['base64'])} bytes")
    else:
        print(f"  screenshot status: {r.get('screenshot_status', 'ok')}")