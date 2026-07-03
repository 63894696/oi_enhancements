"""系统托盘 — pystray(可选)"""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("agent_shell.tray")

# 16x16 简易圆点图标(RGBA)
def _make_icon_image(color: str = "#22c55e"):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = _hex_to_rgb(color)
    draw.ellipse((8, 8, 56, 56), fill=(r, g, b, 255))
    return img


def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class TrayController:
    def __init__(
        self,
        title: str = "OI Agent",
        on_quit: Optional[Callable[[], None]] = None,
        profile_actions: Optional[List[Tuple[str, Callable[[], None]]]] = None,
    ):
        self.title = title
        self.on_quit = on_quit
        self.profile_actions = profile_actions or []
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._snap: dict = {}
        self._color = "#22c55e"

    def update_snapshot(self, snap: dict, color: str) -> None:
        self._snap = snap
        self._color = color
        if self._icon is not None:
            try:
                self._icon.title = self._build_title()
                img = _make_icon_image(color)
                if img is not None:
                    self._icon.icon = img
            except Exception:
                pass

    def _build_title(self) -> str:
        st = self._snap.get("state", "offline")
        prof = self._snap.get("profile", "?")
        return f"OI Agent [{prof}] — {st}"

    @staticmethod
    def _menu_action(callback: Callable[[], None]):
        """pystray 回调签名为 (icon, item); 勿用第二位置参,否则 item 会遮蔽 callback"""

        def handler(_icon, _item):
            callback()

        return handler

    def _menu(self):
        import pystray

        items = []
        for label, cb in self.profile_actions:
            items.append(pystray.MenuItem(label, self._menu_action(cb)))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("退出 Shell", self._menu_action(self._quit)))
        return pystray.Menu(*items)

    def refresh_menu(self) -> None:
        if self._icon is not None:
            try:
                self._icon.menu = self._menu()
            except Exception:
                pass

    def _quit(self) -> None:
        if self._icon is not None:
            self._icon.stop()
        if self.on_quit:
            self.on_quit()

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            log.warning("pystray/Pillow 未安装,托盘不可用(仅顶栏模式)")
            return False
        img = _make_icon_image(self._color)
        if img is None:
            log.warning("Pillow 未安装,无法生成托盘图标")
            return False

        self._icon = pystray.Icon(
            "oi_agent_shell",
            img,
            title=self.title,
            menu=self._menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, name="agent-shell-tray", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
