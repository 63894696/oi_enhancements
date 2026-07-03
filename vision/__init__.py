"""OI vision 增强器 — 包装 Peekaboo-W 的 ScreenCapture + AI vision decision

给 OI agent 提供:
- capture_screen() — 截屏 + base64
- capture_window(title) — 按窗口名截屏
- list_windows() — 列出所有可见窗口
- find_window(title) — 按 title 找 hwnd
- get_foreground_window() — 当前焦点窗口

源码:`oi_enhancements/vendor/peekaboo/screen.py` + `window.py`
"""
from __future__ import annotations

import base64
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional

# vendor 路径
_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "peekaboo"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _try_import(module_name: str, class_name: str):
    """延迟 import,失败返回 None 而不是崩"""
    try:
        import importlib
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)
    except (ImportError, AttributeError) as e:
        print(f"[oi_vision] {module_name}.{class_name} 不可用: {e}")
        return None


ScreenCapture = _try_import("screen", "ScreenCapture")
WindowManager = _try_import("window", "WindowManager")


# ============================================================
# 统一 API(OI tool 风格:dict 返回 + status 字段)
# ============================================================

def list_windows(visible_only: bool = True) -> dict:
    """列出所有可见窗口,用于 OI 找目标"""
    if WindowManager is None:
        return {"status": "unavailable", "reason": "WindowManager not loadable", "windows": []}
    try:
        wins = WindowManager.list_all_windows()
        return {"status": "ok", "count": len(wins), "windows": wins[:50]}  # 限制 50 个避免过大
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def find_window(title: str) -> dict:
    """按 title 找窗口 hwnd"""
    if WindowManager is None:
        return {"status": "unavailable", "reason": "WindowManager not loadable"}
    try:
        hwnd = WindowManager.find_window(title=title)
        if hwnd is None:
            return {"status": "not_found", "title": title}
        info = WindowManager.get_window_info(hwnd)
        return {"status": "ok", "hwnd": hwnd, "info": info}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def get_foreground_window() -> dict:
    """当前焦点窗口"""
    if WindowManager is None:
        return {"status": "unavailable"}
    try:
        fg = WindowManager.get_foreground_window()
        return {"status": "ok" if fg else "none", "info": fg}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def capture_screen(monitor_index: int = 0, max_width: int = 1280, return_base64: bool = True) -> dict:
    """截屏,默认缩到 max_width 1280(适合发给 LLM 看)

    Args:
        monitor_index: 0=主屏,1+=副屏
        max_width: 缩放后最大宽度(LLM vision token 控制)
        return_base64: True 返回 base64 PNG 字符串,False 返回 numpy

    Returns:
        dict 含 status / base64 / size / monitor info
    """
    if ScreenCapture is None:
        return {"status": "unavailable", "reason": "ScreenCapture not loadable"}
    try:
        sc = ScreenCapture()
        try:
            # 直接调 ScreenCapture.get_thumbnail(它内部已经缩了)
            if max_width and max_width < 99999:
                b64 = sc.get_thumbnail(monitor_index=monitor_index, max_width=max_width)
                return {
                    "status": "ok",
                    "format": "base64_png",
                    "width": max_width,
                    "base64": b64,
                    "bytes": len(b64),
                    "monitor_index": monitor_index,
                }
            else:
                result = sc.capture_screen(monitor_index=monitor_index)
                return {
                    "status": "ok",
                    "format": result.get("format"),
                    "bounds": result.get("bounds"),
                    "base64": result.get("base64"),
                    "saved_path": result.get("saved_path"),
                }
        finally:
            sc.sct.close()  # 显式关闭 mss
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def capture_window_by_title(title: str, save_path: Optional[str] = None) -> dict:
    """按 title 截指定窗口"""
    if ScreenCapture is None or WindowManager is None:
        return {"status": "unavailable"}
    try:
        hwnd = WindowManager.find_window(title=title)
        if hwnd is None:
            return {"status": "not_found", "title": title}
        sc = ScreenCapture()
        try:
            result = sc.capture_window(hwnd, save_path=save_path)
            return result
        finally:
            sc.sct.close()
    except Exception as e:
        return {"status": "error", "reason": str(e)}


if __name__ == "__main__":
    # smoke
    import json
    print("=== list_windows ===")
    print(json.dumps(list_windows(), indent=2, ensure_ascii=False)[:500])
    print("\n=== get_foreground_window ===")
    print(json.dumps(get_foreground_window(), indent=2, ensure_ascii=False)[:500])
    print("\n=== capture_screen (320px thumbnail) ===")
    r = capture_screen(max_width=320)
    if r["status"] == "ok":
        print(f"  bytes: {r['bytes']}, width: {r['width']}, has base64: {bool(r['base64'])}")
    else:
        print(f"  status: {r}")