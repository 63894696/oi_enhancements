"""可拖动浮动球 — 显示 Agent 状态 + 轻量交互"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple

from .display import STATE_LABELS, format_status_line, state_color

_TRANSPARENT = "#010101"
_PULSE_STATES = frozenset({"listening", "thinking", "speaking", "busy"})
_CLICK_DRAG_PX = 6


class FloatingOrb:
    """Always-on-top 圆形浮动指示器,可任意拖动放置。

    - 球体颜色 = agent 状态
    - 单击(无拖动): 展开/收起详情气泡
    - 双击: ``on_double_click``(默认切 presence)
    - 右键: ``on_right_click``(默认打断)
    """

    def __init__(
        self,
        size: int = 56,
        position: Optional[Tuple[int, int]] = None,
        profile_name: str = "base",
        on_click: Optional[Callable[[], None]] = None,
        on_double_click: Optional[Callable[[], None]] = None,
        on_right_click: Optional[Callable[[], None]] = None,
        on_moved: Optional[Callable[[int, int], None]] = None,
    ):
        self.size = max(40, min(96, size))
        self.profile_name = profile_name
        self.on_click = on_click
        self.on_double_click = on_double_click
        self.on_right_click = on_right_click
        self.on_moved = on_moved

        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._ring_id = None
        self._ball_id = None
        self._glyph_id = None
        self._detail_win: Optional[tk.Toplevel] = None
        self._detail_label: Optional[tk.Label] = None

        self._state = "offline"
        self._color = "#666666"
        self._detail_text = ""
        self._status_line = "OI Agent"
        self._expanded = False
        self._pulse_on = False
        self._pulse_step = 0

        self._drag_x = 0
        self._drag_y = 0
        self._press_x = 0
        self._press_y = 0
        self._moved = False

        self._pos = position  # (x, y) or None → 默认右下角

    def create(self) -> None:
        root = tk.Tk()
        root.title("OI Agent Orb")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=_TRANSPARENT)
        try:
            root.attributes("-transparentcolor", _TRANSPARENT)
        except tk.TclError:
            pass

        pad = 8
        w = self.size + pad * 2
        h = self.size + pad * 2
        canvas = tk.Canvas(
            root,
            width=w,
            height=h,
            bg=_TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack()

        cx = w // 2
        cy = h // 2
        r = self.size // 2
        ring = canvas.create_oval(
            cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
            outline="#ffffff", width=0, fill="",
        )
        ball = canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=self._color, outline="#ffffff", width=2,
        )
        glyph = canvas.create_text(
            cx, cy,
            text="…",
            fill="#ffffff",
            font=("Segoe UI", max(9, self.size // 5), "bold"),
        )

        for seq, handler in (
            ("<ButtonPress-1>", self._on_press),
            ("<B1-Motion>", self._on_drag),
            ("<ButtonRelease-1>", self._on_release),
            ("<Double-Button-1>", self._on_double),
            ("<Button-3>", self._on_right),
        ):
            canvas.bind(seq, handler)
            root.bind(seq, handler)

        self._root = root
        self._canvas = canvas
        self._ring_id = ring
        self._ball_id = ball
        self._glyph_id = glyph

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        if self._pos:
            x, y = self._pos
        else:
            x = sw - w - 24
            y = sh - h - 80
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.after(200, self._tick)

    def _on_press(self, event) -> None:
        self._drag_x = event.x_root
        self._drag_y = event.y_root
        self._press_x = event.x_root
        self._press_y = event.y_root
        self._moved = False

    def _on_drag(self, event) -> None:
        if self._root is None:
            return
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        if abs(event.x_root - self._press_x) > _CLICK_DRAG_PX or abs(event.y_root - self._press_y) > _CLICK_DRAG_PX:
            self._moved = True
        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")
        self._drag_x = event.x_root
        self._drag_y = event.y_root
        if self._expanded:
            self._reposition_detail()

    def _on_release(self, event) -> None:
        if self._root is None:
            return
        if self._moved:
            if self.on_moved:
                self.on_moved(self._root.winfo_x(), self._root.winfo_y())
            return
        self._toggle_detail()
        if self.on_click:
            self.on_click()

    def _on_double(self, _event) -> None:
        if self.on_double_click:
            self.on_double_click()

    def _on_right(self, _event) -> None:
        if self.on_right_click:
            self.on_right_click()

    def _toggle_detail(self) -> None:
        if self._expanded:
            self._hide_detail()
        else:
            self._show_detail()

    def _show_detail(self) -> None:
        if self._root is None or self._detail_win is not None:
            return
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#1e1e1e")
        lbl = tk.Label(
            win,
            text=self._status_line,
            fg="#f0f0f0",
            bg="#1e1e1e",
            font=("Segoe UI", 9),
            justify="left",
            padx=10,
            pady=6,
            wraplength=260,
        )
        lbl.pack()
        self._detail_win = win
        self._detail_label = lbl
        self._expanded = True
        self._reposition_detail()

    def _reposition_detail(self) -> None:
        if self._root is None or self._detail_win is None:
            return
        self._detail_win.update_idletasks()
        ow = self._root.winfo_width()
        oh = self._root.winfo_height()
        ox = self._root.winfo_x()
        oy = self._root.winfo_y()
        dw = self._detail_win.winfo_reqwidth()
        dh = self._detail_win.winfo_reqheight()
        # 气泡在球体左侧,避免遮挡
        x = max(0, ox - dw - 8)
        y = max(0, oy + (oh - dh) // 2)
        self._detail_win.geometry(f"+{x}+{y}")

    def _hide_detail(self) -> None:
        if self._detail_win is not None:
            try:
                self._detail_win.destroy()
            except Exception:
                pass
            self._detail_win = None
            self._detail_label = None
        self._expanded = False

    def update(self, profile_name: str, snap: dict) -> None:
        self.profile_name = profile_name
        self._state = snap.get("state", "offline")
        self._color = state_color(snap)
        self._detail_text = (snap.get("detail") or "").strip()
        self._status_line = format_status_line(profile_name, snap)
        glyph = _state_glyph(self._state)

        root = self._root
        if root is None:
            return

        def _apply() -> None:
            if self._canvas is not None and self._ball_id is not None:
                self._canvas.itemconfigure(self._ball_id, fill=self._color)
            if self._canvas is not None and self._glyph_id is not None:
                self._canvas.itemconfigure(self._glyph_id, text=glyph)
            if self._detail_label is not None:
                self._detail_label.configure(text=self._status_line)
                self._reposition_detail()
            self._update_pulse()

        try:
            root.after(0, _apply)
        except tk.TclError:
            pass

    def _update_pulse(self) -> None:
        if self._canvas is None or self._ring_id is None:
            return
        if self._state in _PULSE_STATES:
            if not self._pulse_on:
                self._pulse_on = True
                self._pulse_step = 0
                self._pulse_tick()
        else:
            self._pulse_on = False
            self._canvas.itemconfigure(self._ring_id, width=0)

    def _pulse_tick(self) -> None:
        if not self._pulse_on or self._root is None or self._canvas is None or self._ring_id is None:
            return
        w = 2 + (self._pulse_step % 3)
        self._canvas.itemconfigure(self._ring_id, outline=self._color, width=w)
        self._pulse_step += 1
        try:
            self._root.after(350, self._pulse_tick)
        except tk.TclError:
            pass

    def _tick(self) -> None:
        if self._root is None:
            return
        try:
            self._root.update_idletasks()
        except tk.TclError:
            return
        self._root.after(200, self._tick)

    def run(self) -> None:
        if self._root is None:
            self.create()
        assert self._root is not None
        self._root.mainloop()

    def destroy(self) -> None:
        root = self._root
        if root is None:
            return

        def _do() -> None:
            self._hide_detail()
            try:
                root.destroy()
            except Exception:
                pass
            self._root = None

        try:
            root.after(0, _do)
        except Exception:
            _do()


def _state_glyph(state: str) -> str:
    return {
        "offline": "—",
        "idle": "○",
        "listening": "听",
        "thinking": "思",
        "speaking": "说",
        "busy": "忙",
        "blocked": "×",
        "degraded": "!",
    }.get(state, STATE_LABELS.get(state, "?")[:1])
