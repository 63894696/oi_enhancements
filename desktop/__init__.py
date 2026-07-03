"""OI desktop 操控增强器 — 包装 Peekaboo-W 的 InputController + WindowManager + UIAutomation

给 OI agent 提供:
- click(x, y) / click_image(image_path) / double_click / right_click
- type_text(text) — 中文自动用剪贴板粘贴
- press_key / hotkey
- find_window(title) / focus_window(title) / move_window / resize_window / maximize
- find_element(title) / click_element() — 通过 pywinauto

源码:`vendor/peekaboo/input.py` + `window.py` + `ui.py`
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "peekaboo"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _try_import(module_name: str, class_name: str):
    try:
        import importlib
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)
    except (ImportError, AttributeError) as e:
        print(f"[oi_desktop] {module_name}.{class_name} 不可用: {e}")
        return None


InputController = _try_import("input", "InputController")
WindowManager = _try_import("window", "WindowManager")
UIAutomation = _try_import("ui", "UIAutomation")


# ============================================================
# Mouse / Keyboard
# ============================================================

def click(x: int, y: int, button: str = "left", double: bool = False) -> dict:
    """点击坐标"""
    if InputController is None:
        return {"status": "unavailable"}
    try:
        if double:
            ok = InputController.double_click(x, y, button=button)
        else:
            ok = InputController.click(x, y, button=button)
        return {"status": "ok" if ok else "fail", "x": x, "y": y, "button": button, "double": double}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def click_image(image_path: str, confidence: float = 0.9, timeout: float = 10) -> dict:
    """等图片出现并点击(模板匹配)— 比坐标点击更稳"""
    if InputController is None:
        return {"status": "unavailable"}
    try:
        ok = InputController.click_on_image(image_path, confidence=confidence, timeout=timeout)
        return {"status": "ok" if ok else "not_found", "image_path": image_path}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def type_text(text: str) -> dict:
    """输入文本(中文自动走剪贴板粘贴)"""
    if InputController is None:
        return {"status": "unavailable"}
    try:
        ok = InputController.type_text(text)
        return {"status": "ok" if ok else "fail", "text_len": len(text), "has_chinese": any(ord(c) > 127 for c in text)}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def hotkey(*keys) -> dict:
    """组合键,例如 hotkey('ctrl', 'c') / hotkey('alt', 'f4')"""
    if InputController is None:
        return {"status": "unavailable"}
    try:
        ok = InputController.hotkey(*keys)
        return {"status": "ok" if ok else "fail", "keys": list(keys)}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def get_mouse_position() -> dict:
    if InputController is None:
        return {"status": "unavailable"}
    try:
        x, y = InputController.get_position()
        return {"status": "ok", "x": x, "y": y}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ============================================================
# Window
# ============================================================

def focus_window(title: str) -> dict:
    """按 title 找窗口并置前"""
    if WindowManager is None:
        return {"status": "unavailable"}
    try:
        hwnd = WindowManager.find_window(title=title)
        if hwnd is None:
            return {"status": "not_found", "title": title}
        ok = WindowManager.focus_window(hwnd)
        return {"status": "ok" if ok else "fail", "hwnd": hwnd, "title": title}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def maximize_window(title: str) -> dict:
    if WindowManager is None:
        return {"status": "unavailable"}
    try:
        hwnd = WindowManager.find_window(title=title)
        if hwnd is None:
            return {"status": "not_found"}
        ok = WindowManager.maximize_window(hwnd)
        return {"status": "ok" if ok else "fail", "hwnd": hwnd}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def move_window(title: str, x: int, y: int, width: int = None, height: int = None) -> dict:
    if WindowManager is None:
        return {"status": "unavailable"}
    try:
        hwnd = WindowManager.find_window(title=title)
        if hwnd is None:
            return {"status": "not_found"}
        ok = WindowManager.move_window(hwnd, x, y, width, height)
        return {"status": "ok" if ok else "fail", "hwnd": hwnd}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ============================================================
# UI Automation(pywinauto)
# ============================================================

def inspect_window(title: str) -> dict:
    """打印窗口 UI 树(给 OI 决策用)"""
    if UIAutomation is None:
        return {"status": "unavailable"}
    try:
        uia = UIAutomation()
        if not uia.connect_by_title(title):
            return {"status": "not_found", "title": title}
        # dump 一层简单结构
        tree = uia.dump_tree(max_depth=3)
        return {"status": "ok", "title": title, "tree_snippet": tree[:2000]}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def click_element(title: str, auto_id: str = None, control_type: str = None, name: str = None) -> dict:
    """按 auto_id / control_type / name 找元素并点击"""
    if UIAutomation is None:
        return {"status": "unavailable"}
    try:
        uia = UIAutomation()
        if not uia.connect_by_title(title):
            return {"status": "not_found"}
        ok = uia.click_element(auto_id=auto_id, control_type=control_type, name=name)
        return {"status": "ok" if ok else "fail"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


if __name__ == "__main__":
    import json
    print("=== smoke ===")
    print("mouse pos:", json.dumps(get_mouse_position(), ensure_ascii=False))
    print("hotkey test:", json.dumps(hotkey("ctrl"), ensure_ascii=False))  # 只按 ctrl 看是否崩
    print("\n=== list windows (前 3) ===")
    from oi_enhancements.vision import list_windows
    r = list_windows()
    if r["status"] == "ok":
        for w in r["windows"][:3]:
            print(f"  [{w['hwnd']}] {w['title'][:50]}")
    else:
        print(f"  {r}")