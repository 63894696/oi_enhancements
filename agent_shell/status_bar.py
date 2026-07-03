"""10px 顶栏状态条 — tkinter always-on-top"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


class StatusBar:
    """屏幕顶部细条,显示当前 profile + agent 状态"""

    def __init__(
        self,
        height: int = 10,
        on_click: Optional[Callable[[], None]] = None,
    ):
        self.height = max(8, height)
        self.on_click = on_click
        self._root: Optional[tk.Tk] = None
        self._bar: Optional[tk.Frame] = None
        self._label: Optional[tk.Label] = None
        self._text = ""
        self._color = "#666666"

    def create(self) -> None:
        self._root = tk.Tk()
        self._root.title("OI Agent Shell")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg="#1a1a1a")

        sw = self._root.winfo_screenwidth()
        self._root.geometry(f"{sw}x{self.height}+0+0")

        self._bar = tk.Frame(self._root, bg=self._color, height=self.height)
        self._bar.pack(fill=tk.BOTH, expand=True)

        self._label = tk.Label(
            self._bar,
            text="OI Agent Shell 启动中…",
            fg="#ffffff",
            bg=self._color,
            font=("Segoe UI", 7),
            anchor="w",
            padx=6,
        )
        self._label.pack(fill=tk.BOTH, expand=True)

        if self.on_click:
            for w in (self._root, self._bar, self._label):
                w.bind("<Button-1>", lambda _e: self.on_click())

        self._root.after(200, self._tick)

    def update(self, text: str, color: str) -> None:
        self._text = text
        self._color = color
        root = self._root
        if root is None:
            return

        def _apply() -> None:
            if self._label is not None:
                self._label.configure(text=text, bg=color)
            if self._bar is not None:
                self._bar.configure(bg=color)
            root.configure(bg=color)

        try:
            root.after(0, _apply)
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

        def _do():
            try:
                root.destroy()
            except Exception:
                pass
            self._root = None

        try:
            root.after(0, _do)
        except Exception:
            _do()
