"""全局热键 — pynput GlobalHotKeys"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

log = logging.getLogger("agent_shell.hotkeys")


def hotkey_to_pynput(spec: str) -> str:
    """``ctrl+shift+space`` → ``<ctrl>+<shift>+<space>``"""
    parts = []
    for token in spec.lower().replace("-", "+").split("+"):
        t = token.strip()
        if not t:
            continue
        special = {
            "space": "space", "esc": "esc", "escape": "esc",
            "enter": "enter", "tab": "tab", "backspace": "backspace",
        }
        if t in special:
            parts.append(f"<{special[t]}>")
        elif len(t) == 1:
            parts.append(t)
        else:
            parts.append(f"<{t}>")
    return "+".join(parts)


class HotkeyManager:
    def __init__(self):
        self._listener = None
        self._bindings: Dict[str, Callable[[], None]] = {}

    def register(self, spec: str, callback: Callable[[], None]) -> None:
        self._bindings[hotkey_to_pynput(spec)] = callback

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except ImportError:
            log.warning("pynput 未安装,全局热键不可用")
            return False
        if not self._bindings:
            return False
        try:
            self._listener = keyboard.GlobalHotKeys(self._bindings)
            self._listener.daemon = True
            self._listener.start()
            log.info("热键已注册: %s", list(self._bindings.keys()))
            return True
        except Exception as e:
            log.warning("热键注册失败: %s", e)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
