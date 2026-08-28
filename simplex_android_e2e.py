"""simplex_android_e2e.py — Agent-First OS 阶段 2 实测:agent(协议)↔ SimpleX 安卓端 E2E

架构 §4.1 双路径合一:agent 走协议直连(路径 A),安卓端走官方 GUI 客户端(路径 B,
adb UI 自动化)。验证双向 E2E 加密消息。单一常驻进程(libsimplex 单例)。

关键实现点(踩坑后定稿):
  - 必须用**完整 simplex:/invitation#... 链接**(connFullLink),不是 https 短链
    (短链会被 Android 默认浏览器截走 + VPS web 端 SSL 报错)。
  - 安卓端走 SimpleX "通过链接连接"页的粘贴字段。该字段是 Compose 粘贴区,
    input text 可注入;注入后点"连接"确认。
  - 全程一个 agent 进程持有 libsimplex,不能分散到多个进程(否则链接在死的 runtime 上)。

用法: python simplex_android_e2e.py
前置: MuMu 已启动(adb 127.0.0.1:5555),SimpleX 已装且建好 profile。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as st  # noqa: E402

ADB = r"C:/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:5555"
STATE = Path.home() / ".local" / "share" / "aureon" / "simplex" / "android_e2e"
STATE.mkdir(parents=True, exist_ok=True)

AGENT_MSG = "安卓你好,这是 agent 经 SimpleX 协议直连发来的 E2E 加密消息 (阶段2实测)."
ANDROID_TAG = "AGENT_PROBE"


# ────────────────────────────────────────────────────────────────────── #
# adb 助手
# ────────────────────────────────────────────────────────────────────── #

def adb(*args: str, timeout: int = 30) -> str:
    r = subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def ui_dump() -> str:
    return adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=30)


def tap(x: int, y: int) -> None:
    adb("shell", "input", "tap", str(x), str(y))


def key(code: str) -> None:
    adb("shell", "input", "keyevent", code)


def input_text(text: str) -> None:
    adb("shell", "input", "text", text.replace(" ", "%s"))


def bounds_of(dump: str, text: str) -> tuple[int, int] | None:
    for m in re.finditer(r'<node[^>]*text="' + re.escape(text) + r'"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', dump):
        x1, y1, x2, y2 = map(int, m.groups())
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def click_text(dump: str, *labels: str) -> bool:
    for lab in labels:
        b = bounds_of(dump, lab)
        if b:
            tap(*b)
            return True
    return False


# ────────────────────────────────────────────────────────────────────── #
# agent 协议侧
# ────────────────────────────────────────────────────────────────────── #

def agent_make_full_link() -> str:
    """生成一次性邀请,返回完整 simplex:/invitation#... 链接。"""
    rt = st._runtime()
    api = rt._client.api
    from simplex_chat.types import CC

    async def mk() -> str:
        user = await api.api_get_active_user()
        resp = await api.send_chat_cmd(CC.APIAddContact_cmd_string({"userId": user["userId"], "incognito": False}))
        cl = resp.get("connLinkInvitation", {})
        return cl.get("connFullLink") or cl.get("connShortLink") or ""

    return asyncio.run_coroutine_threadsafe(mk(), rt._loop).result(timeout=60)


# ────────────────────────────────────────────────────────────────────── #
# 主流程
# ────────────────────────────────────────────────────────────────────── #

def main() -> int:
    print("== 1. agent 协议侧初始化 ==")
    r = st.call_tool("simplex_setup", {"display_name": "agent-l4"})
    print("   setup ok:", r["ok"])

    print("== 2. 生成一次性邀请(完整 simplex: 链接)==")
    full_link = agent_make_full_link()
    if not full_link.startswith("simplex:"):
        print("   FAIL: 非完整链接:", full_link[:80]); return 1
    print("   full link len:", len(full_link))
    (STATE / "full_link.txt").write_text(full_link, encoding="utf-8")

    print("== 3. 安卓端:回到主界面 ==")
    adb("shell", "am", "force-stop", "chat.simplex.app")
    time.sleep(1)
    adb("shell", "monkey", "-p", "chat.simplex.app", "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    d = ui_dump()
    # 关掉可能的锁屏/提示
    if click_text(d, "取消", "好的", "稍后"):
        time.sleep(1); d = ui_dump()

    print("== 4. 安卓端:打开'粘贴链接/扫描' ==")
    if not click_text(d, "粘贴链接/扫描"):
        print("   WARN: 未找到'粘贴链接/扫描',尝试主界面搜索框路径")
        # 备选:点主界面"新建一次性链接"旁的搜索框
        click_text(d, "搜索或粘贴 SimpleX 链接") or tap(215, 774)
        time.sleep(1)
        d = ui_dump()
        click_text(d, "粘贴链接/扫描")
    time.sleep(2)
    d = ui_dump()

    print("== 5. 安卓端:在'通过链接连接'页粘贴链接 ==")
    # 找'粘贴你收到的链接'输入区(大粘贴区,中心约 (800,470))
    if "粘贴你收到的链接" in d or "通过链接连接" in d:
        tap(800, 470)  # 聚焦粘贴区
        time.sleep(1)
    input_text(full_link)
    time.sleep(2)
    d = ui_dump()
    if "192.220.14.165" in d or "simplex" in d.lower():
        print("   ✓ 链接已进入粘贴区")
    else:
        print("   WARN: 未确认链接填入,继续")

    print("== 6. 安卓端:点'连接' ==")
    d = ui_dump()
    if not click_text(d, "连接", "Connect", "连接联系人"):
        key("66")  # 回车兜底
    time.sleep(3)
    d = ui_dump()
    click_text(d, "连接", "确定", "Connect", "好的")  # 可能的二次确认
    time.sleep(2)

    print("== 7. 等待握手(contactConnected)==")
    deadline = time.time() + 90
    contact = None
    while time.time() < deadline:
        lc = st.call_tool("simplex_list_contacts", {})
        act = [c for c in lc.get("output", []) if c.get("active")]
        if act:
            contact = act[0]; break
        time.sleep(3)
    if not contact:
        print("   ✗ 超时未连接")
        print("   当前 UI:", re.findall(r'text="([^"]{2,})"', ui_dump())[:14])
        return 1
    print(f"   ✓ 已连接: {contact}")

    print("== 8. agent → 安卓 发消息 ==")
    r = st.call_tool("simplex_send_message", {"contact": str(contact["contact_id"]), "text": AGENT_MSG})
    print("   send ok:", r["ok"])
    if not r["ok"]:
        print("   ", r); return 1

    print("== 9. 安卓端:打开会话验证 ==")
    time.sleep(5)
    d = ui_dump()
    if click_text(d, "agent-l4"):
        time.sleep(2); d = ui_dump()
    got = "协议直连" in d or "阶段2实测" in d or "E2E 加密消息" in d
    print("   安卓收到 agent 消息?", "✓" if got else "?(UI 未确认)")

    print("== 10. 安卓 → agent 回消息 ==")
    reply = f"{ANDROID_TAG}_{int(time.time())}"
    d = ui_dump()
    if not (click_text(d, "发消息", "Send message", "消息")):
        tap(800, 850)  # 底部输入区
    time.sleep(1)
    input_text(reply)
    time.sleep(1)
    key("66")
    print(f"   安卓已发: {reply}")

    print("== 11. agent 侧收安卓回复 ==")
    deadline = time.time() + 60
    got_reply = None
    while time.time() < deadline:
        rr = st.call_tool("simplex_read_messages", {"pop": True})
        for it in rr.get("output", []):
            if ANDROID_TAG in (it.get("text") or ""):
                got_reply = it; break
        if got_reply:
            break
        time.sleep(3)
    if got_reply:
        print(f"   ✓✓ 双向成功: agent 收到 '{got_reply['text']}' (from {got_reply.get('contact_name')})")
        print("\n=== agent(协议) ↔ 安卓(SimpleX 客户端) E2E 加密通信 双向验证通过 ===")
        return 0
    print("   ✗ 超时未收到安卓回复")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        time.sleep(1)
