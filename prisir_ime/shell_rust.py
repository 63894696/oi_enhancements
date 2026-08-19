# -*- coding: utf-8 -*-
"""Prisir IME 实机验证壳 — Rust 引擎驱动
外挂式输入法:全局键盘钩子捕获输入 → 光标位置候选窗 → SendInput 上屏。
引擎用 prisir_ime.dll(Rust,逻辑同源 Python lingxi_ime),用于实机验证。
自包含:光标定位/候选窗/上屏内联,不依赖 lingxi_ime backend。

用法:
  python shell_rust.py [--db path] [--no-index]
  右Ctrl 切换激活;激活态打字出候选;数字/空格选词;回车上屏原文;Esc 取消
"""
import ctypes, json, os, sys, time, threading
from ctypes import wintypes
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = r"C:\Users\Administrator\voice_input\lingxi_ime\backend\ciku.db"
DLL = os.path.join(HERE, "target", "release", "prisir_ime.dll")
LOG = os.path.join(HERE, "shell_debug.log")

# ---- Windows 常量 ----
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_BACK, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x0D, 0x1B, 0x20
VK_0, VK_9, VK_RCONTROL = 0x30, 0x39, 0xA3


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---------- Rust 引擎封装(ctypes) ----------
class RustIMEEngine:
    def __init__(self, db_path, build_index=True):
        lib = ctypes.CDLL(DLL)
        lib.prisir_ime_load.restype = ctypes.c_void_p
        lib.prisir_ime_load.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.prisir_ime_query.restype = ctypes.c_void_p
        lib.prisir_ime_query.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.prisir_ime_smart_sentence.restype = ctypes.c_void_p
        lib.prisir_ime_smart_sentence.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.prisir_ime_learn.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.prisir_ime_free_string.argtypes = [ctypes.c_void_p]
        self.lib = lib
        t0 = time.perf_counter()
        self.h = lib.prisir_ime_load(db_path.encode(), 1 if build_index else 0)
        self.load_ms = (time.perf_counter() - t0) * 1000
        if not self.h:
            raise RuntimeError(f"prisir_ime_load 失败: {db_path}")
        log(f"[engine] loaded index={build_index} in {self.load_ms:.0f}ms")

    def _str(self, ptr):
        if not ptr:
            return None
        s = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        self.lib.prisir_ime_free_string(ptr)
        return s

    def query(self, inp):
        ptr = self.lib.prisir_ime_query(self.h, inp.encode())
        return json.loads(self._str(ptr) or "[]")

    def smart_sentence(self, inp):
        return self._str(self.lib.prisir_ime_smart_sentence(self.h, inp.encode())) or ""

    def learn(self, inp, sel):
        self.lib.prisir_ime_learn(self.h, inp.encode(), sel.encode())


# ---------- 光标定位(GetGUIThreadInfo) ----------
class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", ctypes.c_long * 4)]


def caret_position():
    u = ctypes.windll.user32
    gui = GUITHREADINFO()
    gui.cbSize = ctypes.sizeof(GUITHREADINFO)
    u.GetGUIThreadInfo(0, ctypes.byref(gui))
    if gui.hwndCaret:
        left, top, right, bottom = gui.rcCaret
        pt = wintypes.POINT(left, bottom)
        u.ClientToScreen(gui.hwndCaret, ctypes.byref(pt))
        return pt.x, pt.y
    return 0, 0


# ---------- SendInput Unicode 上屏 ----------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INP(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("_input", _INP)]


def send_unicode(text):
    u = ctypes.windll.user32
    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
    arr = []
    for ch in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            i = INPUT()
            i.type = 1
            i._input.ki.wVk = 0
            i._input.ki.wScan = ord(ch)
            i._input.ki.dwFlags = flags
            i._input.ki.time = 0
            i._input.ki.dwExtraInfo = None
            arr.append(i)
    n = len(arr)
    u.SendInput(n, (INPUT * n)(*arr), ctypes.sizeof(INPUT))


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


# ---------- 输入法主程序 ----------
class PrisirShellIME:
    def __init__(self, db_path, build_index=True):
        self.engine = RustIMEEngine(db_path, build_index)
        self._root = None
        self._input = ""
        self._cands = []
        self._sel = 0
        self._active = False
        self._hook = None
        self._hook_proc = None
        self._win = None
        self._labels = []
        self._smart = ""

    def start(self):
        self._root = tk.Tk()
        self._root.withdraw()
        self._tray()
        if not self._install_hook():
            print("[ERROR] 键盘钩子安装失败")
            return
        threading.Thread(target=self._toggle_hotkey, daemon=True).start()
        print(f"[OK] Prisir IME 测试壳已启动 (引擎加载 {self.engine.load_ms:.0f}ms)")
        print("     右Ctrl 切换激活;打字出候选;数字/空格选词;回车上屏原文;Esc 取消")
        self._root.mainloop()

    def _tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), "#0e1420")
            d = ImageDraw.Draw(img)
            d.ellipse([8, 8, 56, 56], fill="#3d7bff")
            d.text((22, 18), "P", fill="#0e1420")
            menu = pystray.Menu(pystray.MenuItem("退出", self._quit))
            self._trayicon = pystray.Icon("prisir_ime_shell", img, "Prisir IME 测试壳", menu)
            threading.Thread(target=self._trayicon.run, daemon=True).start()
        except Exception as e:
            log(f"[WARN] tray: {e}")

    def _quit(self, *_):
        try:
            if self._trayicon:
                self._trayicon.stop()
        except Exception:
            pass
        self._root.after(0, self._root.quit)

    def _toggle_hotkey(self):
        u = ctypes.windll.user32
        was = False
        while True:
            try:
                down = (u.GetAsyncKeyState(VK_RCONTROL) & 0x8000) != 0
                if down and not was:
                    was = True
                    self._root.after(0, self._toggle)
                elif not down and was:
                    was = False
                time.sleep(0.03)
            except Exception:
                time.sleep(0.1)

    def _toggle(self):
        self._active = not self._active
        log(f"[toggle] active={self._active}")
        if not self._active:
            self._hide()
            if self._input:
                self._commit()

    def _install_hook(self):
        u = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.restype = ctypes.c_long
        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
        hmod = k32.GetModuleHandleW(None)

        def proc(nCode, wParam, lParam):
            if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN) and self._active:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self._on_key(kb.vkCode):
                    return 1
            return u.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._hook_proc = HOOKPROC(proc)
        self._hook = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, hmod, 0)
        if not self._hook:
            log(f"[ERROR] SetWindowsHookExW: {k32.GetLastError()}")
            return False
        return True

    def _on_key(self, vk):
        # Tk GUI 操作须回主线程,但钩子需即时返回是否吞键 → 主线程调度 + 立即吞
        if 0x41 <= vk <= 0x5A:
            ch = chr(vk).lower()
            self._root.after(0, self._append, ch)
            return True
        if VK_0 + 1 <= vk <= VK_9:
            idx = vk - VK_0 - 1
            self._root.after(0, self._pick, idx)
            return True
        if vk == VK_SPACE:
            self._root.after(0, self._pick, 0)
            return True
        if vk == VK_RETURN:
            self._root.after(0, self._commit_raw)
            return True
        if vk == VK_BACK:
            self._root.after(0, self._backspace)
            return True
        if vk == VK_ESCAPE:
            self._root.after(0, self._cancel)
            return True
        return False

    # ---- 主线程状态操作 ----
    def _append(self, ch):
        self._input += ch
        self._refresh()

    def _backspace(self):
        if self._input:
            self._input = self._input[:-1]
            self._refresh()

    def _cancel(self):
        self._input, self._cands = "", []
        self._hide()

    def _commit_raw(self):
        if self._input:
            send_unicode(self._input)
        self._cancel()

    def _pick(self, idx):
        if 0 <= idx < len(self._cands):
            word = self._cands[idx]
            self.engine.learn(self._input, word)
            send_unicode(word)
            self._cancel()

    def _commit(self):
        if self._cands:
            self._pick(self._sel)
        else:
            self._commit_raw()

    def _refresh(self):
        if not self._input:
            self._cands = []
            self._hide()
            return
        t0 = time.perf_counter()
        rows = self.engine.query(self._input)
        qms = (time.perf_counter() - t0) * 1000
        # 整句首选:输入含 >=2 音节时给出(用 Rust smart_sentence,空串=无路径)
        self._smart = self.engine.smart_sentence(self._input) if len(self._input) >= 3 else ""
        self._cands = [r["word"] for r in rows[:9]]
        # 整句首选置顶(若与候选不同)
        if self._smart and self._smart not in self._cands:
            self._cands = [self._smart] + self._cands[:8]
        self._sel = 0
        log(f"[query] {self._input!r} -> {len(self._cands)} cands {qms:.1f}ms smart={self._smart!r}")
        self._show()

    def _show(self):
        if not self._input or not self._cands:
            self._hide()
            return
        x, y = caret_position()
        if x == 0 and y == 0:
            x, y = 200, 200
        if self._win is None:
            self._win = tk.Toplevel(self._root)
            self._win.overrideredirect(True)
            self._win.attributes("-topmost", True)
            self._win.configure(bg="#0e1420")
            self._py = tk.Label(self._win, text="", font=("Microsoft YaHei UI", 10),
                                bg="#0e1420", fg="#5aa2ff", anchor="w", padx=8, pady=1)
            self._py.pack(side=tk.TOP, fill=tk.X)
            row = tk.Frame(self._win, bg="#0e1420")
            row.pack(side=tk.TOP, fill=tk.X)
            self._labels = []
            for i in range(9):
                lbl = tk.Label(row, text="", font=("Microsoft YaHei UI", 13),
                               bg="#0e1420", fg="#9aa4ae", anchor="w", padx=6, pady=3)
                lbl.pack(side=tk.LEFT)
                self._labels.append(lbl)
        self._py.config(text=self._input + ("  ⇒ " + self._smart if self._smart else ""))
        for i, lbl in enumerate(self._labels):
            if i < len(self._cands):
                cur = i == self._sel
                lbl.config(text=(f"{i+1}.{self._cands[i]}" if cur else f"{i+1} {self._cands[i]}"),
                           fg="#ffd27a" if cur else "#9aa4ae")
            else:
                lbl.config(text="")
        self._win.geometry(f"+{x}+{y + 22}")
        self._win.deiconify()

    def _hide(self):
        if self._win:
            self._win.withdraw()


def main():
    db = DEFAULT_DB
    build_index = True
    args = sys.argv[1:]
    if "--no-index" in args:
        build_index = False
    if "--db" in args:
        db = args[args.index("--db") + 1]
    PrisirShellIME(db, build_index).start()


if __name__ == "__main__":
    main()
