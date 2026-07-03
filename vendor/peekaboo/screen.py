#!/usr/bin/env python3
"""
Peekaboo-W ScreenCapture Module
Capture screen/window, return Base64 or save to file
"""

import os
import sys
import base64
import json
from datetime import datetime
from io import BytesIO
from typing import Optional, List, Dict, Any

# Import dependencies
try:
    import mss
    import mss.tools
except ImportError:
    raise ImportError(f"mss not installed. Install: pip install mss")

try:
    import numpy as np
    import cv2
except ImportError:
    raise ImportError(f"opencv-python not installed. Install: pip install opencv-python")

try:
    from PIL import Image
except ImportError:
    raise ImportError(f"Pillow not installed. Install: pip install Pillow")


class ScreenCapture:
    """Windows screen capture tool"""
    
    def __init__(self):
        self.sct = mss.mss()
    
    def __del__(self):
        """Ensure resource cleanup"""
        try:
            self.sct.close()
        except:
            pass
    
    def list_monitors(self) -> List[Dict[str, Any]]:
        """List all monitors"""
        monitors = []
        for i, mon in enumerate(self.sct.monitors):
            if i == 0:
                continue
            monitors.append({
                "index": i - 1,
                "x": mon["left"],
                "y": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
                "name": f"Monitor {i-1}" if i > 1 else "Primary"
            })
        return monitors
    
    def capture_screen(self, monitor_index: int = 0, save_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture specified monitor screen"""
        monitors = self.list_monitors()
        
        if monitor_index >= len(monitors):
            monitor_index = 0
        
        monitor = self.sct.monitors[monitor_index + 1]
        screenshot = self.sct.grab(monitor)
        
        result = {
            "success": True,
            "monitor_index": monitor_index,
            "bounds": {
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"]
            },
            "timestamp": datetime.now().isoformat()
        }
        
        if save_path:
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=save_path)
            result["saved_path"] = os.path.abspath(save_path)
            result["format"] = "png"
        else:
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            result["base64"] = base64.b64encode(buffer.getvalue()).decode()
            result["format"] = "base64"
        
        return result
    
    def capture_to_numpy(self, monitor_index: int = 0) -> np.ndarray:
        """Capture screen and return numpy array (for OpenCV)"""
        monitors = self.list_monitors()
        
        if monitor_index >= len(monitors):
            monitor_index = 0
        
        monitor = self.sct.monitors[monitor_index + 1]
        screenshot = self.sct.grab(monitor)
        img = np.array(screenshot)
        return img
    
    def capture_window(self, hwnd: int, save_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture specified window"""
        try:
            import win32gui
            
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            
            if width <= 0 or height <= 0:
                return {"success": False, "error": "Invalid window size"}
            
            monitor = {"left": left, "top": top, "right": right, "bottom": bottom, "width": width, "height": height}
            screenshot = self.sct.grab(monitor)
            
            result = {
                "success": True,
                "hwnd": hwnd,
                "bounds": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "width": width,
                    "height": height
                },
                "timestamp": datetime.now().isoformat()
            }
            
            if save_path:
                mss.tools.to_png(screenshot.rgb, screenshot.size, output=save_path)
                result["saved_path"] = os.path.abspath(save_path)
                result["format"] = "png"
            else:
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                result["base64"] = base64.b64encode(buffer.getvalue()).decode()
                result["format"] = "base64"
            
            return result
            
        except ImportError:
            return {"success": False, "error": "win32gui not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def save_screenshot(self, monitor_index: int = 0, filepath: str = None) -> str:
        """Save screenshot to file"""
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"screenshot_{timestamp}.png"
        
        result = self.capture_screen(monitor_index, save_path=filepath)
        
        if result["success"]:
            return result["saved_path"]
        else:
            raise Exception(result.get("error", "Unknown error"))
    
    def get_thumbnail(self, monitor_index: int = 0, max_width: int = 400) -> str:
        """Get thumbnail (Base64)"""
        monitors = self.list_monitors()
        
        if monitor_index >= len(monitors):
            monitor_index = 0
        
        monitor = self.sct.monitors[monitor_index + 1]
        screenshot = self.sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img.thumbnail((max_width, new_height), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode()


def main():
    """Command line test"""
    print("=" * 50)
    print("Peekaboo-W ScreenCapture Module Test")
    print("=" * 50)
    
    sc = ScreenCapture()
    
    print("\n[Monitor List]:")
    monitors = sc.list_monitors()
    for m in monitors:
        print(f"  [{m['index']}] {m['name']}: {m['width']}x{m['height']} @ ({m['x']}, {m['y']})")
    
    print("\n[Capturing primary screen...]")
    result = sc.capture_screen(0)
    
    if result["success"]:
        print(f"  [OK] Capture successful!")
        print(f"  [Size] {result['bounds']['width']}x{result['bounds']['height']}")
        
        save_path = "C:\\Users\\Administrator\\Peekaboo-W\\tests\\test_screenshot.png"
        result2 = sc.capture_screen(0, save_path=save_path)
        print(f"  [Saved] {result2['saved_path']}")
    else:
        print(f"  [FAIL] {result.get('error')}")
    
    print("\n[Generating thumbnail...]")
    thumb = sc.get_thumbnail(0, max_width=200)
    print(f"  [Base64 length] {len(thumb)} chars")
    
    print("\n" + "=" * 50)
    print("Test completed!")
    print("=" * 50)


if __name__ == "__main__":
    main()