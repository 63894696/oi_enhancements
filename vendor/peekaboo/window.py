#!/usr/bin/env python3
"""
Peekaboo-W Window Manager Module
List, manage, and control Windows windows
"""

import sys
import time
from typing import List, Dict, Any, Optional

try:
    import win32gui
    import win32con
    import win32process
except ImportError:
    raise ImportError(f"pywin32 not installed. Install: pip install pywin32")


class WindowManager:
    """Windows window management tool"""

    @staticmethod
    def list_all_windows() -> List[Dict[str, Any]]:
        """List all visible windows"""
        windows = []

        def callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    try:
                        class_name = win32gui.GetClassName(hwnd)
                        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]

                        windows.append({
                            "hwnd": hwnd,
                            "title": title,
                            "class_name": class_name,
                            "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
                            "width": right - left,
                            "height": bottom - top,
                            "process_id": process_id
                        })
                    except:
                        pass

        win32gui.EnumWindows(callback, None)
        return windows

    @staticmethod
    def list_windows_by_title(title_pattern: str) -> List[Dict[str, Any]]:
        """List windows matching title pattern"""
        all_windows = WindowManager.list_all_windows()
        return [w for w in all_windows if title_pattern.lower() in w["title"].lower()]

    @staticmethod
    def list_windows_by_class(class_pattern: str) -> List[Dict[str, Any]]:
        """List windows matching class name pattern"""
        all_windows = WindowManager.list_all_windows()
        return [w for w in all_windows if class_pattern.lower() in w["class_name"].lower()]

    @staticmethod
    def get_window_info(hwnd: int) -> Dict[str, Any]:
        """Get detailed window info"""
        try:
            info = {
                "hwnd": hwnd,
                "title": win32gui.GetWindowText(hwnd),
                "class_name": win32gui.GetClassName(hwnd),
                "rect": {},
                "width": 0,
                "height": 0,
                "process_id": None,
                "visible": win32gui.IsWindowVisible(hwnd),
                "enabled": win32gui.IsWindowEnabled(hwnd),
                "minimized": win32gui.IsIconic(hwnd),
                "maximized": win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_MAXIMIZE
            }

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            info["rect"] = {"left": left, "top": top, "right": right, "bottom": bottom}
            info["width"] = right - left
            info["height"] = bottom - top
            info["process_id"] = win32process.GetWindowThreadProcessId(hwnd)[1]

            return info
        except Exception as e:
            return {"hwnd": hwnd, "error": str(e)}

    @staticmethod
    def focus_window(hwnd: int) -> bool:
        """Bring window to foreground"""
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as e:
            print(f"[ERROR] Focus window failed: {e}")
            return False

    @staticmethod
    def set_foreground_window(hwnd: int) -> bool:
        """Bring window to foreground (alias for focus_window)"""
        return WindowManager.focus_window(hwnd)

    @staticmethod
    def move_window(hwnd: int, x: int, y: int, width: int = None, height: int = None) -> bool:
        """Move and/or resize window"""
        try:
            if width is None or height is None:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if width is None:
                    width = right - left
                if height is None:
                    height = bottom - top

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, width, height, 0)
            return True
        except Exception as e:
            print(f"[ERROR] Move window failed: {e}")
            return False

    @staticmethod
    def resize_window(hwnd: int, width: int, height: int) -> bool:
        """Resize window to specific dimensions"""
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, left, top, width, height, 0)
            return True
        except Exception as e:
            print(f"[ERROR] Resize window failed: {e}")
            return False

    @staticmethod
    def minimize_window(hwnd: int) -> bool:
        """Minimize window"""
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        except Exception as e:
            print(f"[ERROR] Minimize window failed: {e}")
            return False

    @staticmethod
    def maximize_window(hwnd: int) -> bool:
        """Maximize window using keyboard shortcut (bypasses Qt interception)"""
        try:
            # Restore if minimized first
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)
            
            # Focus the window
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            
            # Use Win+Up keyboard shortcut to maximize (bypasses Qt interception)
            import pyautogui
            pyautogui.hotkey('win', 'up')
            return True
        except Exception as e:
            print(f"[ERROR] Maximize window failed: {e}")
            return False

    @staticmethod
    def restore_window(hwnd: int) -> bool:
        """Restore window to normal size"""
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return True
        except Exception as e:
            print(f"[ERROR] Restore window failed: {e}")
            return False

    @staticmethod
    def hide_window(hwnd: int) -> bool:
        """Hide window (not minimize)"""
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            return True
        except Exception as e:
            print(f"[ERROR] Hide window failed: {e}")
            return False

    @staticmethod
    def show_window(hwnd: int) -> bool:
        """Show hidden window"""
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            return True
        except Exception as e:
            print(f"[ERROR] Show window failed: {e}")
            return False

    @staticmethod
    def close_window(hwnd: int) -> bool:
        """Close window by sending WM_CLOSE"""
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return True
        except Exception as e:
            print(f"[ERROR] Close window failed: {e}")
            return False

    @staticmethod
    def find_window(title: str = None, class_name: str = None) -> Optional[int]:
        """Find window by title or class name"""
        try:
            if title:
                hwnd = win32gui.FindWindow(class_name, title)
                return hwnd if hwnd else None
            elif class_name:
                hwnd = win32gui.FindWindow(class_name, None)
                return hwnd if hwnd else None
            return None
        except Exception as e:
            print(f"[ERROR] Find window failed: {e}")
            return None

    @staticmethod
    def get_foreground_window() -> Optional[Dict[str, Any]]:
        """Get currently focused window info"""
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                return WindowManager.get_window_info(hwnd)
            return None
        except Exception as e:
            print(f"[ERROR] Get foreground window failed: {e}")
            return None

    @staticmethod
    def get_cursor_position() -> Dict[str, int]:
        """Get current mouse cursor position"""
        try:
            import win32api
            x, y = win32api.GetCursorPos()
            return {"x": x, "y": y}
        except:
            try:
                import pyautogui
                pos = pyautogui.position()
                return {"x": pos.x, "y": pos.y}
            except:
                return {"x": 0, "y": 0, "error": "Cannot get cursor position"}


def main():
    """Command line test"""
    print("=" * 50)
    print("Peekaboo-W WindowManager Module Test")
    print("=" * 50)

    wm = WindowManager()

    print("\n[Listing all windows...]")
    windows = wm.list_all_windows()
    print(f"  Found {len(windows)} windows\n")

    for i, w in enumerate(windows[:10]):
        print(f"  [{i+1}] {w['title'][:40]}")
        print(f"       Class: {w['class_name']}")
        print(f"       Size: {w['width']}x{w['height']}")
        print()

    print("[Getting foreground window...]")
    fg = wm.get_foreground_window()
    if fg and 'title' in fg:
        print(f"  Title: {fg['title']}")
        print(f"  HWND: {fg['hwnd']}")
    else:
        print(f"  [Error] {fg.get('error', 'Unknown error') if fg else 'No foreground window'}")

    print("\n[Getting cursor position...]")
    pos = wm.get_cursor_position()
    print(f"  X: {pos['x']}, Y: {pos['y']}")

    print("\n" + "=" * 50)
    print("Test completed!")
    print("=" * 50)


if __name__ == "__main__":
    main()