#!/usr/bin/env python3
"""
Peekaboo-W UI Automation Module
UI element inspection and control using pywinauto
"""

import sys
from typing import List, Dict, Any, Optional

try:
    from pywinauto import Application, Desktop
    from pywinauto.controls.win32_controls import ButtonWrapper, EditWrapper
except ImportError:
    raise ImportError(f"pywinauto not installed. Install: pip install pywinauto")


class UIAutomation:
    """Windows UI automation tool"""

    def __init__(self, backend: str = "win32"):
        """Initialize with backend ('win32' or 'uia')"""
        self.backend = backend
        self.app = None
        self.window = None

    def connect(self, process_id: int = None, title: str = None, timeout: int = 5) -> bool:
        """Connect to application by process ID or title"""
        try:
            if process_id:
                self.app = Application(backend=self.backend).connect(process=process_id, timeout=timeout)
            elif title:
                self.app = Application(backend=self.backend).connect(title=title, timeout=timeout)
            else:
                print("[ERROR] Must specify process_id or title")
                return False

            self.window = self.app.top_window()
            return True
        except Exception as e:
            print(f"[ERROR] Connect failed: {e}")
            return False

    def connect_from_path(self, exe_path: str, args: str = None) -> bool:
        """Start and connect to application from executable path"""
        try:
            if args:
                self.app = Application(backend=self.backend).start(exe_path, arguments=args)
            else:
                self.app = Application(backend=self.backend).start(exe_path)
            self.window = self.app.top_window()
            return True
        except Exception as e:
            print(f"[ERROR] Start app failed: {e}")
            return False

    def get_all_elements(self) -> List[Dict[str, Any]]:
        """Get all UI elements in current window"""
        if not self.window:
            return []

        elements = []
        tree = self.window.tree_update()

        for elem in tree:
            try:
                info = {
                    "type": elem.window_text() or elem.class_name(),
                    "class_name": elem.class_name(),
                    "text": elem.window_text(),
                    "rectangle": elem.rectangle(),
                    "control_type": elem.control_type(),
                    "enabled": elem.is_enabled(),
                    "visible": elem.is_visible()
                }
                elements.append(info)
            except:
                pass

        return elements

    def print_tree(self, max_depth: int = 3) -> None:
        """Print UI element tree"""
        if not self.window:
            print("[ERROR] Not connected to any window")
            return

        def print_recursive(element, depth=0):
            if depth > max_depth:
                return
            indent = "  " * depth
            try:
                text = element.window_text()[:40] if element.window_text() else ""
                ctrl_type = element.control_type()
                class_name = element.class_name()
                print(indent + "[" + ctrl_type + "] " + class_name + ": " + text)
            except:
                pass

            try:
                for child in element.children():
                    print_recursive(child, depth + 1)
            except:
                pass

        n = "\n"
        print(n + "=== UI Element Tree ===")
        try:
            for elem in self.window.children():
                print_recursive(elem)
        except Exception as e:
            print("[ERROR] " + str(e))

    def find_element(self, title: str = None, class_name: str = None,
                     control_type: str = None) -> Optional[Any]:
        """Find element by criteria"""
        if not self.window:
            return None

        try:
            for elem in self.window.tree_update():
                match = True
                if title and title.lower() not in (elem.window_text() or "").lower():
                    match = False
                if class_name and class_name.lower() != (elem.class_name() or "").lower():
                    match = False
                if control_type and control_type.lower() != (elem.control_type() or "").lower():
                    match = False

                if match:
                    return elem
        except:
            pass

        return None

    def click_element(self, element: Any) -> bool:
        """Click on UI element"""
        try:
            element.click_input()
            return True
        except Exception as e:
            print("[ERROR] Click element failed: " + str(e))
            return False

    def double_click_element(self, element: Any) -> bool:
        """Double click on UI element"""
        try:
            element.double_click_input()
            return True
        except Exception as e:
            print("[ERROR] Double click element failed: " + str(e))
            return False

    def right_click_element(self, element: Any) -> bool:
        """Right click on UI element"""
        try:
            element.right_click_input()
            return True
        except Exception as e:
            print("[ERROR] Right click element failed: " + str(e))
            return False

    def set_focus(self) -> bool:
        """Set focus to window"""
        if self.window:
            try:
                self.window.set_focus()
                return True
            except Exception as e:
                print("[ERROR] Set focus failed: " + str(e))
        return False

    def get_all_windows(self) -> List[Dict[str, Any]]:
        """Get all visible windows"""
        windows = []
        try:
            dlg = Desktop(backend=self.backend).windows()
            for w in dlg:
                try:
                    if w.is_visible() and w.window_text():
                        windows.append({
                            "title": w.window_text(),
                            "class_name": w.class_name(),
                            "rectangle": w.rectangle(),
                            "control_type": w.control_type()
                        })
                except:
                    pass
        except Exception as e:
            print("[ERROR] Get windows failed: " + str(e))
        return windows

    def get_element_by_index(self, index: int) -> Optional[Any]:
        """Get element by tree index"""
        if not self.window:
            return None

        try:
            tree = list(self.window.tree_update())
            if 0 <= index < len(tree):
                return tree[index]
        except:
            pass
        return None

    def select_item(self, element: Any, item_text: str) -> bool:
        """Select item in list/combo box"""
        try:
            element.select(item_text)
            return True
        except Exception as e:
            print("[ERROR] Select item failed: " + str(e))
            return False

    def type_text(self, element: Any, text: str) -> bool:
        """Type text into edit box"""
        try:
            element.set_edit_text(text)
            return True
        except Exception as e:
            print("[ERROR] Type text failed: " + str(e))
            return False

    def get_text(self, element: Any) -> str:
        """Get text from element"""
        try:
            return element.window_text()
        except:
            return ""

    def is_enabled(self, element: Any) -> bool:
        """Check if element is enabled"""
        try:
            return element.is_enabled()
        except:
            return False

    def is_visible(self, element: Any) -> bool:
        """Check if element is visible"""
        try:
            return element.is_visible()
        except:
            return False


def main():
    """Command line test"""
    sep = "=" * 50
    print(sep)
    print("Peekaboo-W UIAutomation Module Test")
    print(sep)

    ui = UIAutomation()

    print("\n[List all visible windows...]")
    windows = ui.get_all_windows()
    print("  Found " + str(len(windows)) + " windows\n")

    for i, w in enumerate(windows[:10]):
        print("  [" + str(i+1) + "] " + w['title'][:50])
        print("       Class: " + w['class_name'])

    print("\n" + sep)
    print("Test completed!")
    print(sep)


if __name__ == "__main__":
    main()