#!/usr/bin/env python3
"""
Peekaboo-W AI Integration Module
AI-powered screen understanding and task automation
"""

import sys
import os
import json
import base64
from typing import Dict, Any, Optional, List, Callable

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.screen import ScreenCapture
from src.window import WindowManager
from src.input import InputController
from src.ui import UIAutomation


class AIAgent:
    """AI agent for screen understanding and task automation"""

    PROVIDER_ENDPOINTS = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        "claude": "https://api.anthropic.com/v1/messages",
        "openai": "https://api.openai.com/v1/chat/completions",
        "minimax": "https://api.minimax.chat/v1/chat/completions_pro",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions"
    }

    def __init__(self, model_provider: str = "gemini", api_key: str = None):
        self.model_provider = model_provider
        env_keys = {
            "gemini": "GEMINI_API_KEY",
            "claude": "CLAUDE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "openrouter": "OPENROUTER_API_KEY"
        }
        self.api_key = api_key or os.environ.get(env_keys.get(model_provider, f"{model_provider.upper()}_API_KEY"))
        self.screen = ScreenCapture()
        self.window = WindowManager()
        self.input = InputController()
        self.ui = UIAutomation()

    def capture_screen_base64(self) -> str:
        result = self.screen.capture_screen(0)
        if result.get("success"):
            return result.get("base64", "")
        return ""

    def capture_window_base64(self, hwnd: int) -> str:
        result = self.screen.capture_window(hwnd)
        if result.get("success"):
            return result.get("base64", "")
        return ""

    def get_foreground_window_base64(self) -> str:
        fg = self.window.get_foreground_window()
        if fg and "hwnd" in fg:
            return self.capture_window_base64(fg["hwnd"])
        return ""

    def analyze_screen(self, prompt: str = None, image_base64: str = None) -> Dict[str, Any]:
        if image_base64 is None:
            image_base64 = self.capture_screen_base64()

        if not image_base64:
            return {"success": False, "error": "Failed to capture screen"}

        if not prompt:
            prompt = "Describe what you see on this screen."

        if self.model_provider == "gemini":
            return self._analyze_gemini(prompt, image_base64)
        elif self.model_provider == "claude":
            return self._analyze_claude(prompt, image_base64)
        elif self.model_provider == "openai":
            return self._analyze_openai(prompt, image_base64)
        elif self.model_provider == "minimax":
            return self._analyze_minimax(prompt, image_base64)
        elif self.model_provider == "openrouter":
            return self._analyze_openrouter(prompt, image_base64)
        else:
            return {"success": False, "error": f"Unknown provider: {self.model_provider}"}

    def _analyze_gemini(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "GEMINI_API_KEY not set"}
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests library required"}

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/png", "data": image_base64}}
                    ]
                }]
            }
            response = requests.post(url, json=payload, timeout=30)
            result = response.json()

            if "error" in result:
                return {"success": False, "error": result["error"].get("message", "Unknown error")}

            text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return {"success": True, "text": text, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_claude(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "CLAUDE_API_KEY not set"}
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests library required"}

        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": "claude-3-sonnet-20240229",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_base64}}
                    ]
                }]
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            result = response.json()

            if "error" in result:
                return {"success": False, "error": result["error"].get("message", "Unknown error")}

            text = result.get("content", [{}])[0].get("text", "")
            return {"success": True, "text": text, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_openai(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "OPENAI_API_KEY not set"}
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests library required"}

        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json"
            }
            payload = {
                "model": "gpt-4o",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]
                }],
                "max_tokens": 1024
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            result = response.json()

            if "error" in result:
                return {"success": False, "error": result["error"].get("message", "Unknown error")}

            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "text": text, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_minimax(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "MINIMAX_API_KEY not set"}
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests library required"}

        try:
            url = "https://api.minimax.chat/v1/chat/completions_pro"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json"
            }
            payload = {
                "model": "MiniMax-VL-01",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]
                }],
                "max_tokens": 1024
            }
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            result = response.json()

            if "error" in result:
                return {"success": False, "error": result["error"].get("message", "Unknown error")}

            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "text": text, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_openrouter(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "OPENROUTER_API_KEY not set"}
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests library required"}

        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
                "HTTP-Referer": "https://Peekaboo-W.local",
                "X-Title": "Peekaboo-W"
            }
            payload = {
                "model": "qwen/qwen3-vl-8b-instruct",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]
                }],
                "max_tokens": 1024
            }
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            result = response.json()

            if "error" in result:
                return {"success": False, "error": result["error"].get("message", "Unknown error")}

            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "text": text, "raw": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def describe_screen(self) -> str:
        result = self.analyze_screen("Describe this screen briefly in Chinese.")
        if result.get("success"):
            return result.get("text", "")
        return f"[Error] {result.get('error', 'Unknown error')}"

    def find_and_click(self, target: str, max_attempts: int = 3) -> bool:
        for attempt in range(max_attempts):
            image = self.capture_screen_base64()
            if not image:
                continue

            prompt = f'''You are looking at a computer screen. I need you to find: "{target}"
Return ONLY JSON: {{"found": true/false, "x": number, "y": number, "description": "what you found"}}
If found, provide CENTER coordinates (x, y).'''

            result = self._analyze_with_json(prompt, image)

            if result.get("found"):
                x, y = result.get("x", 0), result.get("y", 0)
                print(f"Found '{target}' at ({x}, {y}), clicking...")
                return self.input.click(int(x), int(y))

            print(f"Attempt {attempt + 1}: '{target}' not found, retrying...")
            import time
            time.sleep(1)
        return False

    def _analyze_with_json(self, prompt: str, image_base64: str) -> Dict[str, Any]:
        if self.model_provider == "gemini":
            result = self._analyze_gemini(prompt, image_base64)
        elif self.model_provider == "claude":
            result = self._analyze_claude(prompt, image_base64)
        elif self.model_provider == "openai":
            result = self._analyze_openai(prompt, image_base64)
        elif self.model_provider == "minimax":
            result = self._analyze_minimax(prompt, image_base64)
        elif self.model_provider == "openrouter":
            result = self._analyze_openrouter(prompt, image_base64)
        else:
            return {"found": False}

        if result.get("success"):
            text = result.get("text", "")
            try:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(text[start:end])
            except:
                pass
        return {"found": False}

    def execute_task(self, task: str) -> Dict[str, Any]:
        image = self.capture_screen_base64()
        if not image:
            return {"success": False, "error": "Failed to capture screen"}

        prompt = f'''You are a Windows automation assistant. User wants: "{task}"
Return ONLY JSON: {{"action": "click/type/hotkey/scroll", "x": number, "y": number, "text": "text", "keys": ["keys"], "scroll_amount": number}}
If not possible: {{"action": "not_possible", "reason": "why"}}
'''

        result = self._analyze_with_json(prompt, image)

        if result.get("action") == "not_possible":
            return {"success": False, "reason": result.get("reason")}

        action = result.get("action", "")
        success = False

        if action == "click":
            success = self.input.click(int(result.get("x", 0)), int(result.get("y", 0)))
        elif action == "type":
            success = self.input.type_text(result.get("text", ""))
        elif action == "hotkey":
            keys = result.get("keys", [])
            if keys:
                success = self.input.hotkey(*keys)
        elif action == "scroll":
            success = self.input.scroll(result.get("scroll_amount", 0))

        return {"success": success, "action": action, "result": result}


def main():
    sep = "=" * 50
    print(sep)
    print("Peekaboo-W AI Module Test")
    print(sep)
    agent = AIAgent()
    print("[1] Module initialized successfully")
    print("[2] Available commands:")
    print("    agent.describe_screen()         # Describe current screen")
    print("    agent.analyze_screen(prompt)    # Analyze with custom prompt")
    print("    agent.find_and_click('button')  # Find and click element")
    print("    agent.execute_task('click OK')   # Execute natural language task")
    print(sep)
    print("Test completed!")
    print(sep)


if __name__ == "__main__":
    main()