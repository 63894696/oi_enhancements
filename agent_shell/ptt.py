"""Push-to-talk 按住检测(S1 骨架)"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Set

log = logging.getLogger("agent_shell.ptt")


def _parse_modifiers(spec: str) -> tuple[Set[str], Optional[str]]:
    mods: Set[str] = set()
    main: Optional[str] = None
    for token in spec.lower().replace("-", "+").split("+"):
        t = token.strip()
        if t in ("ctrl", "control", "alt", "shift", "win"):
            mods.add("ctrl" if t in ("ctrl", "control") else t)
        elif t:
            main = t
    return mods, main


class PushToTalkListener:
    """按住 PTT 组合键 → on_start; 松开主键 → on_end"""

    def __init__(self, spec: str, on_start: Callable[[], None], on_end: Callable[[], None]):
        self.required_mods, self.main_key = _parse_modifiers(spec)
        self.on_start = on_start
        self.on_end = on_end
        self._mods_down: Set[str] = set()
        self._main_down = False
        self._active = False
        self._listener = None

    def _key_name(self, key) -> Optional[str]:
        from pynput import keyboard
        if key == keyboard.Key.space:
            return "space"
        if key == keyboard.Key.shift or key == keyboard.Key.shift_r:
            return "shift"
        if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            return "ctrl"
        if key == keyboard.Key.alt_l or key == keyboard.Key.alt_gr or key == keyboard.Key.alt_r:
            return "alt"
        if getattr(key, "char", None):
            return str(key.char).lower()
        if getattr(key, "name", None):
            return str(key.name).lower()
        return None

    def _mods_ok(self) -> bool:
        return self.required_mods.issubset(self._mods_down)

    def _on_press(self, key):
        name = self._key_name(key)
        if name in ("ctrl", "shift", "alt"):
            self._mods_down.add(name)
            return
        if name == self.main_key and self._mods_ok() and not self._active:
            self._main_down = True
            self._active = True
            self.on_start()

    def _on_release(self, key):
        name = self._key_name(key)
        if name in ("ctrl", "shift", "alt"):
            self._mods_down.discard(name)
            if self._active and not self._mods_ok():
                self._active = False
                self._main_down = False
                self.on_end()
            return
        if name == self.main_key and self._active:
            self._main_down = False
            self._active = False
            self.on_end()

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except ImportError:
            log.warning("pynput 未安装,PTT 不可用")
            return False
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info("PTT 监听: %s", self.required_mods | ({self.main_key} if self.main_key else set()))
        return True

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
