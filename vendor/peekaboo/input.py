#!/usr/bin/env python3
"""
Peekaboo-W Input Controller Module
Mouse and keyboard control for Windows automation
"""

import sys
import time
from typing import Optional, Tuple, List

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
except ImportError:
    raise ImportError(f"pyautogui not installed. Install: pip install pyautogui")


class InputController:
    """Mouse and keyboard input controller"""

    BUTTON_MAP = {
        'left': 'left',
        'right': 'right',
        'middle': 'middle',
        'primary': 'left',
        'secondary': 'right'
    }

    @staticmethod
    def click(x: int = None, y: int = None, button: str = 'left', clicks: int = 1) -> bool:
        """Click at position (current if None) with specified button"""
        try:
            if x is not None and y is not None:
                pyautogui.click(x, y, clicks=clicks, button=button)
            else:
                pyautogui.click(clicks=clicks, button=button)
            return True
        except Exception as e:
            print(f"[ERROR] Click failed: {e}")
            return False

    @staticmethod
    def double_click(x: int = None, y: int = None, button: str = 'left') -> bool:
        """Double click at position"""
        return InputController.click(x, y, button, clicks=2)

    @staticmethod
    def right_click(x: int = None, y: int = None) -> bool:
        """Right click at position"""
        return InputController.click(x, y, 'right')

    @staticmethod
    def middle_click(x: int = None, y: int = None) -> bool:
        """Middle click at position"""
        return InputController.click(x, y, 'middle')

    @staticmethod
    def move_to(x: int, y: int, duration: float = 0) -> bool:
        """Move mouse to position"""
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return True
        except Exception as e:
            print(f"[ERROR] MoveTo failed: {e}")
            return False

    @staticmethod
    def move_relative(dx: int, dy: int, duration: float = 0) -> bool:
        """Move mouse relative to current position"""
        try:
            pyautogui.move(dx, dy, duration=duration)
            return True
        except Exception as e:
            print(f"[ERROR] Move relative failed: {e}")
            return False

    @staticmethod
    def scroll(amount: int, x: int = None, y: int = None) -> bool:
        """Scroll wheel (positive=up, negative=down)"""
        try:
            if x is not None and y is not None:
                pyautogui.scroll(amount, x=x, y=y)
            else:
                pyautogui.scroll(amount)
            return True
        except Exception as e:
            print(f"[ERROR] Scroll failed: {e}")
            return False

    @staticmethod
    def horizontal_scroll(amount: int, x: int = None, y: int = None) -> bool:
        """Horizontal scroll (positive=right, negative=left)"""
        try:
            pyautogui.hscroll(amount)
            return True
        except Exception as e:
            print(f"[ERROR] Horizontal scroll failed: {e}")
            return False

    @staticmethod
    def drag(start_x: int, start_y: int, end_x: int, end_y: int,
             duration: float = 0.5, button: str = 'left') -> bool:
        """Drag from start to end position"""
        try:
            pyautogui.moveTo(start_x, start_y)
            pyautogui.mouseDown(button=button)
            time.sleep(0.05)
            pyautogui.moveTo(end_x, end_y, duration=duration)
            pyautogui.mouseUp(button=button)
            return True
        except Exception as e:
            print(f"[ERROR] Drag failed: {e}")
            return False

    @staticmethod
    def type_text(text: str, interval: float = 0.05) -> bool:
        """Type text - uses clipboard paste for Chinese, regular typing for ASCII"""
        try:
            # Check if text contains non-ASCII characters (Chinese, etc.)
            has_chinese = any(ord(c) > 127 for c in text)

            if has_chinese:
                # Use clipboard paste for Chinese text
                return InputController.paste_text(text)
            else:
                # Use regular typing for ASCII text
                pyautogui.write(text, interval=interval)
                return True
        except Exception as e:
            print(f"[ERROR] Type text failed: {e}")
            return False

    @staticmethod
    def paste_text(text: str, interval: float = 0.1) -> bool:
        """Paste text using clipboard (best for Chinese)"""
        try:
            import pyperclip

            # Save current clipboard
            old_clipboard = pyperclip.paste()

            # Copy new text to clipboard
            pyperclip.copy(text)
            time.sleep(0.05)

            # Paste
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(interval)

            # Restore clipboard
            if old_clipboard:
                pyperclip.copy(old_clipboard)

            return True
        except ImportError:
            print("[ERROR] Install pyperclip: pip install pyperclip")
            return False
        except Exception as e:
            print(f"[ERROR] Paste text failed: {e}")
            return False

    @staticmethod
    def type_text_direct(text: str, interval: float = 0.05) -> bool:
        """Type text using keyboard directly (for ASCII only)"""
        try:
            pyautogui.write(text, interval=interval)
            return True
        except Exception as e:
            print(f"[ERROR] Type text direct failed: {e}")
            return False

    @staticmethod
    def press_key(key: str) -> bool:
        """Press a single key"""
        try:
            pyautogui.press(key)
            return True
        except Exception as e:
            print(f"[ERROR] Press key failed: {e}")
            return False

    @staticmethod
    def press_keys(keys: List[str], interval: float = 0.05) -> bool:
        """Press a sequence of keys"""
        try:
            for key in keys:
                pyautogui.press(key)
                time.sleep(interval)
            return True
        except Exception as e:
            print(f"[ERROR] Press keys failed: {e}")
            return False

    @staticmethod
    def hotkey(*keys) -> bool:
        """Press hotkey combination (e.g., 'ctrl', 'c', 'alt', 'f4')"""
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception as e:
            print(f"[ERROR] Hotkey failed: {e}")
            return False

    @staticmethod
    def hold_key(key: str, duration: float = 1) -> bool:
        """Hold key for duration then release"""
        try:
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)
            return True
        except Exception as e:
            print(f"[ERROR] Hold key failed: {e}")
            return False

    @staticmethod
    def key_down(key: str) -> bool:
        """Press and hold key down"""
        try:
            pyautogui.keyDown(key)
            return True
        except Exception as e:
            print(f"[ERROR] Key down failed: {e}")
            return False

    @staticmethod
    def key_up(key: str) -> bool:
        """Release key"""
        try:
            pyautogui.keyUp(key)
            return True
        except Exception as e:
            print(f"[ERROR] Key up failed: {e}")
            return False

    @staticmethod
    def get_position() -> Tuple[int, int]:
        """Get current mouse position"""
        try:
            pos = pyautogui.position()
            return (pos.x, pos.y)
        except Exception as e:
            print(f"[ERROR] Get position failed: {e}")
            return (0, 0)

    @staticmethod
    def screenshot() -> str:
        """Get screenshot as base64 (for AI analysis)"""
        try:
            import base64
            from io import BytesIO
            from PIL import Image

            img = pyautogui.screenshot()
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode()
        except Exception as e:
            print(f"[ERROR] Screenshot failed: {e}")
            return ""

    @staticmethod
    def locate_on_screen(image_path: str, confidence: float = 0.9) -> Optional[Tuple[int, int, int, int]]:
        """Locate image on screen, return (left, top, width, height) or None"""
        try:
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location:
                return (location.left, location.top, location.width, location.height)
            return None
        except Exception as e:
            print(f"[ERROR] Locate on screen failed: {e}")
            return None

    @staticmethod
    def click_on_image(image_path: str, confidence: float = 0.9, timeout: float = 10) -> bool:
        """Click on image when it appears, with timeout"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            location = InputController.locate_on_screen(image_path, confidence)
            if location:
                x = location[0] + location[2] // 2
                y = location[1] + location[3] // 2
                return InputController.click(x, y)
            time.sleep(0.5)
        print(f"[ERROR] Image not found within {timeout}s")
        return False


KEY_NAMES = [
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
    'enter', 'esc', 'space', 'tab', 'backspace', 'delete',
    'home', 'end', 'pageup', 'pagedown',
    'up', 'down', 'left', 'right',
    'ctrl', 'alt', 'shift', 'win', 'cmd',
    'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12'
]


def main():
    """Command line test"""
    sep = "=" * 50
    print(sep)
    print("Peekaboo-W InputController Module Test")
    print(sep)

    print("\n[Getting current mouse position...]")
    x, y = InputController.get_position()
    print(f"  Position: ({x}, {y})")

    print("\n[Testing move to (100, 100)...]")
    InputController.move_to(100, 100, duration=0.5)
    x, y = InputController.get_position()
    print(f"  New position: ({x}, {y})")

    print("\n[Testing screenshot...]")
    b64 = InputController.screenshot()
    print(f"  Base64 length: {len(b64)} chars")

    print("\n[Testing paste_text (for Chinese)...)]")
    InputController.paste_text("测试中文")
    print("  Pasted '测试中文'")

    print("\n[Common hotkey examples:]")
    print("  InputController.hotkey('ctrl', 'c')  # Copy")
    print("  InputController.hotkey('ctrl', 'v')  # Paste")
    print("  InputController.hotkey('alt', 'f4')  # Close window")
    print("  InputController.hotkey('win', 'd')   # Show desktop")

    print("\n" + sep)
    print("Test completed!")
    print(sep)


if __name__ == "__main__":
    main()