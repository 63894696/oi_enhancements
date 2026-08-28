"""tray_app.py — Agent-First OS 本地常驻:T3 最小骨架

职责:
  - Windows 系统托盘(常驻图标 + 右键菜单)。
  - 全局快捷键 Win+Space 弹输入框(借浏览器打开 l4_web/?token=),无新壳无新依赖。
  - 不做对话(对话归 L4 做);tray 只做"入口 + 启停 + 状态"。

复用 l4_web 的:
  - L4_HOST / L4_PORT 常量
  - _ACCESS_TOKEN / _TOKEN_FILE 同源读取
  - 不重复造鉴权

阶段:W1 交付目标 = 装得上,Win+Space 弹得开,右键能看到菜单项,
        未接 L3 daemon / 未接 SecureDM / 未接沙盒。
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

try:
    import pystray
    from pystray import Menu, MenuItem
except ImportError:
    print("[tray_app] 缺少 pystray,先: pip install pystray Pillow", file=sys.stderr)
    raise

try:
    import keyboard  # type: ignore
except ImportError:
    print("[tray_app] 缺少 keyboard,先: pip install keyboard", file=sys.stderr)
    raise

# ── 与 l4_web.py 对齐的同一组配置 ──────────────────────────────────────
# (直接复用其常量,留 "" 表示"l4_web 未启动/失败")
L4_HOST = os.environ.get("L4_HOST", "127.0.0.1")
L4_PORT = int(os.environ.get("L4_PORT") or 18800)
L4_URL = f"http://{L4_HOST}:{L4_PORT}"

_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"
_HOTKEY = "windows+space"  # Win+Space(全局)


# ── Token / 健康检测 ──────────────────────────────────────────────────
def _read_token() -> str:
    env = os.environ.get("L4_TOKEN")
    if env is not None:
        return env.strip()
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def _l4_health(timeout: float = 2.0) -> bool:
    """GET /api/health;l4_web 不需 token 的探活端点。"""
    try:
        with urllib.request.urlopen(f"{L4_URL}/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _l4_open_url(prefill: str = "") -> str:
    """构造打开 L4 对话窗的 URL(带 token)。预填只在快捷键路径里用。"""
    token = _read_token()
    q = {"token": token} if token else {}
    if prefill:
        q["prefill"] = prefill
    return f"{L4_URL}/?" + urllib.parse.urlencode(q) if q else f"{L4_URL}/"


# ── 输入框浮层(极简,借系统默认浏览器)────────────────────────────────
def on_hotkey() -> None:
    """Win+Space 触发:打开 L4 对话窗(若已开焦点带回)。"""
    # W1 最小骨架:不弹独立输入框,直接开 L4 web。
    # 用户在浏览器输入框打字 = 现有 l4_web 的体验;后续可换成
    # Tauri WebView2 浮层。
    url = _l4_open_url()
    if not _l4_health():
        # 健康检测失败:通知 + 仍打开(用户能看到 401/错误比空白好)
        _notify("L4 未就绪", f"{L4_URL} 无响应(daemon 是否启动?)")
    webbrowser.open(url)


# ── 右键菜单动作 ──────────────────────────────────────────────────────
def open_l4(_icon=None, _item=None) -> None:
    webbrowser.open(_l4_open_url())


def show_health(_icon=None, _item=None) -> None:
    ok = _l4_health()
    tok = _read_token()
    msg = (
        f"L4: {L4_URL}\n"
        f"daemon: {'OK' if ok else 'DOWN'}\n"
        f"token:  {'已加载('+str(len(tok))+'字)' if tok else '未配置'}\n\n"
        f"快捷键: {_HOTKEY} → 打开 L4"
    )
    # Win10/11 toast 在通知中心易被吞;改用 ctypes MessageBoxW 确认可见。
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "OIagent Tray 状态", 0x40)
    except Exception as e:  # noqa: BLE001
        _notify("OIagent Tray 状态", msg)  # 兜底
        print(f"[tray] MessageBox 失败: {e}", file=sys.stderr)


def quit_app(icon: Optional[pystray.Icon], _item) -> None:
    """优雅退出:关闭托盘 + 卸快捷键。"""
    try:
        keyboard.remove_hotkey(_HOTKEY)
    except Exception:  # noqa: BLE001
        pass
    if icon is not None:
        icon.stop()


# ── 通知 ─────────────────────────────────────────────────────────────
def _notify(title: str, msg: str) -> None:
    """pystray.notify 在 Windows 上走系统 toast。"""
    try:
        pystray.Icon("OIagentTray").notify(title, msg)
    except Exception:  # noqa: BLE001
        # 兜底:打 stderr 即可(避免桌面 session 0 弹不到 UI)
        print(f"[tray][notify] {title}: {msg}", file=sys.stderr)


# ── 入口 ─────────────────────────────────────────────────────────────
def main() -> int:
    # 1. 注册快捷键(后台线程 keyboard.start 后保持)
    keyboard.add_hotkey(_HOTKEY, on_hotkey, suppress=False)
    print(f"[tray] 全局快捷键已注册: {_HOTKEY} → 打开 L4 {L4_URL}")

    # 2. 启动 L4 健康状态轮询线程(每 60s 一次)
    def _poll():
        last = None
        while True:
            cur = _l4_health()
            if cur != last:
                _notify(
                    "L4 状态变化",
                    f"{'就绪' if cur else '失联'} → {L4_URL}",
                )
                last = cur
            threading.Event().wait(60.0)

    threading.Thread(target=_poll, daemon=True, name="l4-health-poll").start()

    # 3. 托盘图标:动态生成 16x16 紫色方块(Pillow 已在,免去找图标资源)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle((2, 2, 14, 14), fill=(110, 60, 180, 255))
    except Exception:
        img = None  # pystray 在 Windows 上允许无图标的占位

    # 4. 菜单
    menu = Menu(
        MenuItem("打开 L4 对话窗", open_l4, default=True),  # default = 双击生效
        MenuItem("查看 L4 状态", show_health),
        Menu.SEPARATOR,
        MenuItem(f"快捷键:{_HOTKEY} 打开 L4", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("退出托盘", quit_app),
    )

    icon = pystray.Icon("OIagentTray", img, "OIagent Tray", menu)
    print(f"[tray] 托盘启动;右键菜单 / 双击 / {_HOTKEY} 三入口")
    icon.run()  # 阻塞,直到 quit_app
    return 0


if __name__ == "__main__":
    sys.exit(main())
