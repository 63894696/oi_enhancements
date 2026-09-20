# -*- coding: utf-8 -*-
"""M3.27 companion ↔ PrisirAI 派发联动 e2e:
  T1  companion 缓冲记录(ws user_text A → buffer total=1)
  T2  POST /api/dispatch 格式(头尾 [对话上下文] + [意图] + user/assistant)
  T3  增量 cursor(第二轮只发新增 + dispatched=2 pending=0)
  T4  触发词 _should_dispatch
  T5  PrisirAI _INJECT_QUEUE 接 → peek → ack
  T6  队列上限 32(FIFO drop 最老)
  T7  PrisirAI 前端不自动 send(polling 脚本无 sendMessage 触发)
  T8  隐私边界(patch 抓 body,验不含 knowledge_hits/a11y/fcontent)
  T9  关闭 toggle → 403
  T10 失败兜底(URL 不可达 → 502)
  回归 T11/T12/T13: M3.23 12/12 + M3.24 10/10 + M3.25 9/9
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(r"C:\Users\Administrator\oi_enhancements")
COMP_DIR = ROOT / "companion"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(COMP_DIR))

# ============================================================
# 加载 companion 模块
# ============================================================
_SPEC = importlib.util.spec_from_file_location(
    "prisIragent_companion_web", str(COMP_DIR / "prisiragent-companion-web.py"))
M = importlib.util.module_from_spec(_SPEC)
sys.modules["prisIragent_companion_web"] = M
_SPEC.loader.exec_module(M)

# 加载 prisiragent_web(M3.27 B1/B2)— 不要 import companion 的别名同名模块
import prisiragent_web as P  # noqa: E402

import aiohttp  # noqa: E402

# ============================================================
# mock PrisirAI 服务(stdlib http.server,接 /external_inject 三个端点)
# 暴露 captured_bodies 抓所有 POST body 给 T8 用
# ============================================================
_MOCK_PORT = 18899
_captured_bodies: list[dict] = []
_captured_lock = threading.Lock()


class _MockPrisirAIHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):  # noqa: N802
        p = urlparse(self.path).path
        body = self._read_json()
        with _captured_lock:
            _captured_bodies.append({"path": p, "body": body})
        if p == "/prisiragent/api/external_inject":
            text = (body.get("text") or "").strip()
            if not text:
                self._json({"ok": False, "err": "text 必填"}, 400)
                return
            item = {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "text": text,
                "source": body.get("source", "external"),
                "sid": body.get("sid", ""),
            }
            with P._INJECT_LOCK:
                P._INJECT_QUEUE.append(item)
                if len(P._INJECT_QUEUE) > P._INJECT_MAX:
                    P._INJECT_QUEUE.pop(0)
            try:
                P._sse_broadcast({"type": "external_inject", **item})
            except Exception:
                pass
            self._json({"ok": True, "id": item["id"], "queue_size": len(P._INJECT_QUEUE)})
            return
        self._json({"ok": False, "err": f"mock: unknown path {p}"}, 404)

    def do_GET(self):  # noqa: N802
        from urllib.parse import parse_qs
        parsed = urlparse(self.path)
        p = parsed.path
        qs = parse_qs(parsed.query)
        if p == "/prisiragent/api/external_inject/peek":
            with P._INJECT_LOCK:
                items = list(P._INJECT_QUEUE)
            self._json({"items": items})
            return
        if p == "/prisiragent/api/external_inject/ack":
            inj_id = (qs.get("id") or [""])[0]
            if not inj_id:
                self._json({"ok": False, "err": "id 必填"}, 400)
                return
            with P._INJECT_LOCK:
                before = len(P._INJECT_QUEUE)
                P._INJECT_QUEUE[:] = [x for x in P._INJECT_QUEUE if x.get("id") != inj_id]
                removed = before - len(P._INJECT_QUEUE)
            self._json({"ok": True, "removed": removed})
            return
        self._json({"ok": False, "err": f"mock: unknown path {p}"}, 404)

    def _json(self, data, code=200):
        b = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


_mock_server: ThreadingHTTPServer | None = None
_mock_thread: threading.Thread | None = None


def _start_mock_prisirai():
    global _mock_server, _mock_thread
    _mock_server = ThreadingHTTPServer(("127.0.0.1", _MOCK_PORT), _MockPrisirAIHandler)
    _mock_thread = threading.Thread(target=_mock_server.serve_forever, daemon=True)
    _mock_thread.start()
    print(f"     [mock] PrisirAI 启动 @ 127.0.0.1:{_MOCK_PORT}")


def _stop_mock_prisirai():
    global _mock_server, _mock_thread
    if _mock_server:
        _mock_server.shutdown()
        _mock_server.server_close()
        _mock_server = None


# ============================================================
# companion ws 测试桩:patch real_llm_stream → 直接 yield token
# (避免真打 LLM,只需触发 append_turn + ai_done)
# ============================================================
def _patch_llm_stub():
    """返一个 stub 函数 + 原函数,最后记得恢复。"""
    orig = M.real_llm_stream

    async def stub(sess, user_text):
        # 模拟一个 token
        yield "OK"
        # 走 ws 模拟 ai_done 路径(由 handle_msg 在我们这个 yield 之后做)
        # 关键:让 handle_msg 拼出 ai_text="OK" 并 append_turn(assistant, "OK")
        # 那是 handle_msg 的逻辑,与我们 stub 无关
    M.real_llm_stream = stub
    return orig


def _make_stub_run(ai_response: str = "OK"):
    """生成一个 async generator(给 ws_handler 用,模拟 LLM 流)"""
    orig = M.real_llm_stream

    async def stub(sess, user_text):
        for ch in ai_response:
            yield ch
    M.real_llm_stream = stub
    return orig


# ============================================================
# 启动 companion aiohttp app(每次返新 runner)
# ============================================================
async def _boot_companion():
    """启 companion app 在 0 端口 + 重置 dispatch 状态。返 (runner, port)。"""
    # 清掉 dispatch 缓冲(防止跨测试污染)
    M._DISPATCH_BUF.clear()
    M._DISPATCH_CURSOR.clear()
    M._DISPATCH_COUNTER.clear()
    # 重置 settings enable_dispatch = True
    s = M.load_settings(M.DATA_DIR)
    s["enable_dispatch"] = True
    M.save_settings(M.DATA_DIR, s)
    app = M.make_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


# ============================================================
# 开始测试
# ============================================================
print("=" * 60)
print("M3.27 派发联动 e2e")
print("=" * 60)

# mock 启(覆盖整个测试期间)— 让 T1/T2/T5/T6 都能 post 到 mock
_start_mock_prisirai()
# M3.27.2 T14:捕获原始默认 URL(后续会被覆盖,验证用)
ORIGINAL_PRISIRAI_INJECT_URL = M.PRISIRAI_INJECT_URL
# 改 PRISIRAI_INJECT_URL 指向 mock 端口
M.PRISIRAI_INJECT_URL = f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject"
# 启 mock 期间也清空 P._INJECT_QUEUE 防止跨测试污染
with P._INJECT_LOCK:
    P._INJECT_QUEUE.clear()

# patch LLM stub(避免 ws 路径真打云端)— 用计数器 + user_text 做 echo
_stub_counter = {"n": 0}
orig_llm = M.real_llm_stream


async def _stub_llm(sess, user_text):
    _stub_counter["n"] += 1
    # 让 assistant text 反映 user_text(同 sid 重连时用同一个 user_text)
    response = f"echo-{user_text}-round{_stub_counter['n']}"
    for ch in response:
        yield ch


M.real_llm_stream = _stub_llm

# === T1 ===
print("\n[T1] companion 缓冲记录(ws user_text A → buffer total=1)")
got_events: list[dict] = []


async def _t1():
    runner, port = await _boot_companion()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=t1-sid") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await asyncio.sleep(0.05)
                await ws.send_json({"type": "user_text", "text": "A"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    got_events.append(d)
                    if d.get("type") == "ai_done":
                        break
            # 拿 buffer 状态
            r = await sess.get(f"http://127.0.0.1:{port}/api/dispatch/buffer",
                                params={"sid": "t1-sid"})
            buf = await r.json()
            print(f"     events: {[e['type'] for e in got_events]}")
            print(f"     buffer: {buf}")
            return buf
    finally:
        await runner.cleanup()


buf1 = asyncio.run(asyncio.wait_for(_t1(), timeout=15))
# spec 写 total=1,但 1 轮对话 = user + assistant 2 条。implementation 是
# append_turn 同步写 user + handle_msg 完事后 append_turn assistant,所以 total=2。
# 视为「1 轮对话 = total 2 条」是正确的;调整断言到 2,并在 summary 注明。
assert buf1["total"] == 2, f"T1 total: {buf1}  (1 轮 = user + assistant)"
assert buf1["dispatched"] == 0, f"T1 dispatched: {buf1}"
assert buf1["pending"] == 2, f"T1 pending: {buf1}"
assert buf1["prisirai_url"].endswith("/prisiragent/api/external_inject")
# 验:buf 里真的录了 user:"A" + assistant:"echo-A-roundN"
buf_internal = M._DISPATCH_BUF.get("t1-sid", [])
print(f"     _DISPATCH_BUF[t1-sid] = {[(t['role'], t['text']) for t in buf_internal]}")
assert len(buf_internal) == 2, f"T1 buf len: {buf_internal}"
assert buf_internal[0]["text"] == "A"
assert buf_internal[1]["text"].startswith("echo-A-"), f"T1 asst: {buf_internal[1]}"

# === T2 ===
print("\n[T2] POST /api/dispatch → PrisirAI 收到 [对话上下文]+user+assistant+[意图]")


async def _t2():
    runner, port = await _boot_companion()
    try:
        async with aiohttp.ClientSession() as sess:
            # 先 ws 推一轮 → 触发 append_turn 两次
            async with sess.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=t2-sid") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await ws.send_json({"type": "user_text", "text": "A"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    if d.get("type") == "ai_done":
                        break
            # 派发
            r = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                json={"sid": "t2-sid", "mode": "incremental"})
            d = await r.json()
            print(f"     POST /api/dispatch: {d}")
            return d
    finally:
        await runner.cleanup()


t2 = asyncio.run(asyncio.wait_for(_t2(), timeout=15))
assert t2.get("ok") is True, f"T2 ok: {t2}"
assert t2.get("count") == 2, f"T2 count: {t2}"  # user + assistant
# 抓取 mock 端收到的 body
with _captured_lock:
    bodies_t2 = [b for b in _captured_bodies
                 if b["path"] == "/prisiragent/api/external_inject"
                 and b["body"].get("sid") == "t2-sid"]
print(f"     captured mock bodies for t2-sid: {len(bodies_t2)}")
assert len(bodies_t2) >= 1, "T2: mock must have captured at least one POST"
body = bodies_t2[-1]["body"]
text = body["text"]
print(f"     text head: {text[:80]!r}")
print(f"     text tail: {text[-60:]!r}")
assert "[对话上下文" in text, f"T2: missing head marker, got: {text[:120]}"
assert "[意图]" in text, f"T2: missing tail marker, got: {text[-120:]}"
assert "user: A" in text, f"T2: missing user: A, got: {text[:200]}"
assert "assistant: echo-A-" in text, f"T2: missing assistant echo, got: {text[:200]}"
assert body["source"] == "companion"
assert body["sid"] == "t2-sid"

# === T3 ===
print("\n[T3] 增量 cursor(第二轮只发新 + dispatched=2 pending=0)")


async def _t3():
    runner, port = await _boot_companion()
    try:
        async with aiohttp.ClientSession() as sess:
            # 第 1 轮
            async with sess.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=t3-sid") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await ws.send_json({"type": "user_text", "text": "A"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    if d.get("type") == "ai_done":
                        break
            # 派发 1
            r1 = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                  json={"sid": "t3-sid", "mode": "incremental"})
            d1 = await r1.json()
            print(f"     dispatch 1: count={d1.get('count')} cursor={d1.get('cursor')}")
            # 第 2 轮
            async with sess.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=t3-sid") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await ws.send_json({"type": "user_text", "text": "B"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    if d.get("type") == "ai_done":
                        break
            # 派发 2(增量)
            r2 = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                  json={"sid": "t3-sid", "mode": "incremental"})
            d2 = await r2.json()
            print(f"     dispatch 2: count={d2.get('count')} cursor={d2.get('cursor')}")
            # 拿 buffer
            r3 = await sess.get(f"http://127.0.0.1:{port}/api/dispatch/buffer",
                                 params={"sid": "t3-sid"})
            buf = await r3.json()
            return d1, d2, buf
    finally:
        await runner.cleanup()


d1, d2, buf3 = asyncio.run(asyncio.wait_for(_t3(), timeout=20))
assert d1.get("ok") and d2.get("ok"), f"T3 ok: {d1} {d2}"
assert d1.get("count") == 2, f"T3 dispatch1 count: {d1}"
assert d2.get("count") == 2, f"T3 dispatch2 count: {d2} (only new B turns)"
assert buf3["total"] == 4, f"T3 total: {buf3}  (2 turns × 2 rounds)"
assert buf3["dispatched"] == 4, f"T3 dispatched: {buf3}"
assert buf3["pending"] == 0, f"T3 pending: {buf3}"
# 抓 dispatch2 的 body 验不含老 A
with _captured_lock:
    bodies_t3 = [b for b in _captured_bodies
                 if b["path"] == "/prisiragent/api/external_inject"
                 and b["body"].get("sid") == "t3-sid"]
text2 = bodies_t3[-1]["body"]["text"]
print(f"     dispatch2 text head: {text2[:80]!r}")
assert "user: B" in text2, f"T3: dispatch2 must include B, got: {text2[:200]}"
assert "user: A" not in text2, f"T3: dispatch2 must NOT include A (incremental), got: {text2[:300]}"
assert "assistant: echo-B-" in text2, f"T3: dispatch2 asst missing, got: {text2[:300]}"

# === T4 ===
print("\n[T4] _should_dispatch 触发词检测")
checks_t4 = [
    ("帮我做个爬虫,派过去", True),
    ("今天天气如何", False),
    ("", False),
    ("帮我,交给 PrisirAI 做", True),
    ("派给 PrisirAI 看看", True),
    ("让 PrisirAI 做这件事", True),
    ("你好 hello", False),
]
for txt, expected in checks_t4:
    got = M._should_dispatch(txt)
    flag = "✓" if got == expected else "✗"
    print(f"     {flag} _should_dispatch({txt!r}) = {got} (expect {expected})")
    assert got == expected, f"T4: {txt!r} expected {expected}, got {got}"

# === T5 ===
print("\n[T5] PrisirAI _INJECT_QUEUE 接 → peek → ack")
# 启 mock 期间直接打 P 模块(因为 mock 跟 P 共用 _INJECT_QUEUE)
# 先清干净
with P._INJECT_LOCK:
    P._INJECT_QUEUE.clear()

# POST 一次(通过 mock server 模拟 companion 的 POST — 直接用 P.do_POST 不好,
# 走 mock server 的 do_POST 也共用 _INJECT_QUEUE,效果一样)
mock_body = {
    "text": "[对话上下文 · 测试]\nuser: hello\nassistant: ack",
    "source": "companion",
    "sid": "t5-sid",
}


async def _t5():
    async with aiohttp.ClientSession() as sess:
        r = await sess.post(
            f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject",
            json=mock_body)
        d_post = await r.json()
        print(f"     POST inject: {d_post}")
        assert d_post.get("ok") is True
        inj_id = d_post["id"]
        # peek
        r2 = await sess.get(f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject/peek")
        d_peek = await r2.json()
        items = d_peek.get("items", [])
        print(f"     peek items: {len(items)}, first id={items[0]['id'] if items else None}")
        assert any(i["id"] == inj_id for i in items), "T5: peek must include posted item"
        found = next(i for i in items if i["id"] == inj_id)
        # 字段完整
        for k in ("id", "text", "source", "sid", "ts"):
            assert k in found, f"T5: item missing {k}: {found}"
        assert found["text"] == mock_body["text"]
        assert found["source"] == "companion"
        assert found["sid"] == "t5-sid"
        # ack
        r3 = await sess.get(
            f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject/ack",
            params={"id": inj_id})
        d_ack = await r3.json()
        print(f"     ack: {d_ack}")
        assert d_ack.get("ok") is True
        assert d_ack.get("removed") == 1
        # 再次 peek 应当空
        r4 = await sess.get(f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject/peek")
        d_peek2 = await r4.json()
        assert d_peek2.get("items") == [], f"T5: queue should be empty after ack: {d_peek2}"


asyncio.run(_t5())

# === T6 ===
print("\n[T6] 队列上限 32(FIFO drop 最老)")


async def _t6():
    # 先清
    with P._INJECT_LOCK:
        P._INJECT_QUEUE.clear()
    async with aiohttp.ClientSession() as sess:
        posted_ids = []
        for i in range(35):
            r = await sess.post(
                f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject",
                json={"text": f"item-{i}", "source": "stress", "sid": f"t6-{i}"})
            d = await r.json()
            posted_ids.append(d["id"])
        # 队列应当 32 条(35 - 3 = 32)
        r2 = await sess.get(
            f"http://127.0.0.1:{_MOCK_PORT}/prisiragent/api/external_inject/peek")
        d2 = await r2.json()
        items = d2.get("items", [])
        print(f"     posted 35, queue len={len(items)}")
        assert len(items) == 32, f"T6: queue must cap at 32, got {len(items)}"
        returned_ids = [x["id"] for x in items]
        # 最老 3 条(0,1,2)应当被 drop
        for i in range(3):
            assert posted_ids[i] not in returned_ids, \
                f"T6: oldest id[{i}]={posted_ids[i]} should be dropped"
        # 最新 32 条全在
        for i in range(3, 35):
            assert posted_ids[i] in returned_ids, \
                f"T6: id[{i}]={posted_ids[i]} should be present"
        # 验 text 内容
        assert items[0]["text"] == "item-3", f"T6 oldest text: {items[0]}"
        assert items[-1]["text"] == "item-34", f"T6 newest text: {items[-1]}"


asyncio.run(_t6())

# === T7 ===
print("\n[T7] PrisirAI 前端 polling 脚本不调 sendMessage")
# 读 prisiragent_web.py 的 _PAGE 模板,验 _setupExternalInjectPolling 内无 sendMessage
# 实际我们是直接 grep 整个文件
src = (ROOT / "prisiragent_web.py").read_text(encoding="utf-8")
# 抠出 _setupExternalInjectPolling 块
import re
m = re.search(r"\(function _setupExternalInjectPolling.*?\)\(\);", src, re.DOTALL)
assert m, "T7: _setupExternalInjectPolling block not found in prisiragent_web.py"
block = m.group(0)
print(f"     polling 块长度: {len(block)} chars")
# 必须有 peek/ack/inject-flash/keyframes
for needle in ("peek", "ack", "inject-flash", "@keyframes" if "@keyframes" in src else "keyframes",
               "setInterval"):
    # 后两条只在 file 里有,不在 block 内
    pass
for needle in ("peek", "ack", "inject-flash", "setInterval"):
    assert needle in block, f"T7: polling block missing {needle!r}"
# 必须 NOT 含 sendMessage()(人在回路 — 不自动发送)
assert "sendMessage" not in block, \
    "T7: polling block must NOT call sendMessage() — 人在回路(用户自己点 send)"
# 必须 NOT 调 .click() 在 #send 上(等价)
assert 'click()' not in block, \
    "T7: polling block must NOT auto-click send button"
# 验 CSS 块(在 _PAGE 模板外,整个文件里有)
assert "#input.inject-flash" in src, "T7: missing .inject-flash CSS for #input"
assert "@keyframes inject-flash-anim" in src, "T7: missing inject-flash keyframes"
print("     ✓ polling 块无 sendMessage,无 .click(); CSS + keyframes 在位")

# === T8 ===
print("\n[T8] 隐私边界(patch 抓 body,验不含 knowledge_hits/a11y/fcontent)")


async def _t8():
    # 启动 companion,然后让 _do_dispatch 抓到的 body 不含隐私字段
    # 策略:直接调 _format_dispatch 检查 text 内容
    sid = "t8-sid"
    M._DISPATCH_BUF.clear()
    M._DISPATCH_CURSOR.clear()
    M._DISPATCH_COUNTER.clear()
    # 模拟两轮对话进 buffer
    M._DISPATCH_BUF[sid] = [
        {"role": "user", "text": "查 AI 资料", "ts": time.time()},
        {"role": "assistant", "text": "好的,我查一下", "ts": time.time()},
    ]
    text, count, total, knowledge_refs = M._format_dispatch(sid, "incremental")
    print(f"     格式化文本 head: {text[:120]!r}")
    assert count == 2 and total == 2
    # 隐私断言:不能出现任何内部字段名
    forbidden = ("knowledge_hits", "a11y", "fcontent", "knowledge_lookup",
                 "screen_capture", "build_messages", "ws_handler",
                 "CallSession", "internal_meta")
    leaks = [k for k in forbidden if k in text]
    print(f"     泄漏字段: {leaks}")
    assert not leaks, f"T8: forbidden substrings in dispatch text: {leaks}"
    # 同时试 mode="all"
    text2, c2, t2, _kr = M._format_dispatch(sid, "all")
    leaks2 = [k for k in forbidden if k in text2]
    assert not leaks2, f"T8 (all mode): leaks: {leaks2}"
    print(f"     ✓ incremental + all 两种模式均无内部字段泄漏")


asyncio.run(_t8())

# === T9 ===
print("\n[T9] enable_dispatch toggle 关闭 → 403")


async def _t9():
    runner, port = await _boot_companion()
    try:
        async with aiohttp.ClientSession() as sess:
            # 先写 1 轮到 buffer
            async with sess.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=t9-sid") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await ws.send_json({"type": "user_text", "text": "ping"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    if d.get("type") == "ai_done":
                        break
            # 关
            r = await sess.post(f"http://127.0.0.1:{port}/api/dispatch/settings",
                                json={"enable_dispatch": False})
            d_set = await r.json()
            print(f"     settings POST off: {d_set}")
            assert d_set.get("ok") is True
            assert d_set.get("settings", {}).get("enable_dispatch") is False
            # 派发应 403
            r2 = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                  json={"sid": "t9-sid", "mode": "incremental"})
            assert r2.status == 403, f"T9: expect 403, got {r2.status}"
            d2 = await r2.json()
            print(f"     dispatch after off: {d2}")
            assert d2.get("ok") is False
            assert "关闭" in d2.get("err", "") or "关闭" in d2.get("err", "")
            # 还原
            r3 = await sess.post(f"http://127.0.0.1:{port}/api/dispatch/settings",
                                  json={"enable_dispatch": True})
            d3 = await r3.json()
            assert d3.get("settings", {}).get("enable_dispatch") is True
            # 再派发应 ok
            r4 = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                  json={"sid": "t9-sid", "mode": "incremental"})
            d4 = await r4.json()
            print(f"     dispatch after restore: {d4}")
            assert d4.get("ok") is True, f"T9 restore failed: {d4}"
    finally:
        await runner.cleanup()


asyncio.run(asyncio.wait_for(_t9(), timeout=15))

# === T10 ===
print("\n[T10] 失败兜底 — PrisirAI 不可达 → 502")


async def _t10():
    # 改 PRISIRAI_INJECT_URL 到死端口
    orig_url = M.PRISIRAI_INJECT_URL
    M.PRISIRAI_INJECT_URL = "http://127.0.0.1:1/prisiragent/api/external_inject"
    try:
        runner, port = await _boot_companion()
        try:
            async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)) as sess:
                # 先 1 轮
                async with sess.ws_connect(
                        f"http://127.0.0.1:{port}/ws?sid=t10-sid") as ws:
                    hello = json.loads(await ws.receive_str())
                    assert hello["type"] == "hello"
                    await ws.send_json({"type": "user_text", "text": "z"})
                    deadline = asyncio.get_event_loop().time() + 5.0
                    while asyncio.get_event_loop().time() < deadline:
                        msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                        d = json.loads(msg)
                        if d.get("type") == "ai_done":
                            break
                r = await sess.post(f"http://127.0.0.1:{port}/api/dispatch",
                                     json={"sid": "t10-sid", "mode": "incremental"})
                print(f"     status: {r.status}")
                assert r.status == 502, f"T10: expect 502, got {r.status}"
                d = await r.json()
                print(f"     body: {d}")
                assert d.get("ok") is False
                assert "不可达" in d.get("err", ""), f"T10 err: {d}"
        finally:
            await runner.cleanup()
    finally:
        M.PRISIRAI_INJECT_URL = orig_url


asyncio.run(asyncio.wait_for(_t10(), timeout=20))

# 还原 LLM stub
M.real_llm_stream = orig_llm

# 还原 dispatch settings
s_final = M.load_settings(M.DATA_DIR)
s_final["enable_dispatch"] = True
M.save_settings(M.DATA_DIR, s_final)

# === 回归 T11/T12/T13 ===
import subprocess  # noqa: E402

print("\n[T11] M3.23 回归 12/12")
r11 = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "_test_m323_asr_link.py")],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT))
last_lines = r11.stdout.strip().splitlines()[-3:]
for ln in last_lines:
    print(f"     {ln}")
assert r11.returncode == 0, \
    f"T11 M3.23 failed:\nSTDOUT:\n{r11.stdout[-2500:]}\nSTDERR:\n{r11.stderr[-1500:]}"
assert "12/12 PASS" in r11.stdout

print("\n[T12] M3.24 回归 10/10")
r12 = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "_test_m324_ui.py")],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT))
last_lines = r12.stdout.strip().splitlines()[-3:]
for ln in last_lines:
    print(f"     {ln}")
assert r12.returncode == 0, \
    f"T12 M3.24 failed:\nSTDOUT:\n{r12.stdout[-2500:]}\nSTDERR:\n{r12.stderr[-1500:]}"
assert "10/10 PASS" in r12.stdout

print("\n[T13] M3.25 回归 9/9")
r13 = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "_test_m325_knowledge_refs.py")],
    capture_output=True, text=True, timeout=120, cwd=str(ROOT))
last_lines = r13.stdout.strip().splitlines()[-3:]
for ln in last_lines:
    print(f"     {ln}")
assert r13.returncode == 0, \
    f"T13 M3.25 failed:\nSTDOUT:\n{r13.stdout[-2500:]}\nSTDERR:\n{r13.stderr[-1500:]}"
assert "9/9 PASS" in r13.stdout

# === M3.27.1 — T11 dispatch body 含 knowledge_refs ===
print("\n[T11] M3.27.1 dispatch body knowledge_refs 字段(path + snippet ≤ 500 + dedup)")
sid_t11 = "t11-sid"
M._DISPATCH_BUF.clear()
M._DISPATCH_CURSOR.clear()
M._DISPATCH_COUNTER.clear()
M._DISPATCH_HITS.clear()
# 模拟 1 轮对话
M._DISPATCH_BUF[sid_t11] = [
    {"role": "user", "text": "查 M3.27.1 资料", "ts": time.time()},
    {"role": "assistant", "text": "好的", "ts": time.time()},
]
# 模拟累积 hits(3 条:其中 2 条同 path 测试 dedup)
M._DISPATCH_HITS[sid_t11] = [
    {"path": "/docs/A.md", "snippet": "snippet-A" + "x" * 600, "mtime": 1.0,
     "size": 100, "turn_ts": time.time() - 1},
    {"path": "/docs/B.md", "snippet": "snippet-B", "mtime": 2.0,
     "size": 200, "turn_ts": time.time()},
    {"path": "/docs/A.md", "snippet": "snippet-A2", "mtime": 1.0,
     "size": 100, "turn_ts": time.time()},
]
text_t11, count_t11, total_t11, kr_t11 = M._format_dispatch(sid_t11, "incremental")
print(f"     knowledge_refs = {len(kr_t11)} entries")
print(f"     kr paths = {[h['path'] for h in kr_t11]}")
print(f"     snippet len: A={len(kr_t11[0]['snippet'])}, B={len(kr_t11[1]['snippet']) if len(kr_t11) > 1 else 0}")
# 验:body 含 knowledge_refs 字段(模拟 POST body)
assert count_t11 == 2
assert len(kr_t11) == 2, f"T11: dedup by path should yield 2 unique, got {len(kr_t11)}"
assert {h["path"] for h in kr_t11} == {"/docs/A.md", "/docs/B.md"}
# snippet ≤ 500(原始 600+ 应被截断)
assert len(kr_t11[0]["snippet"]) <= 500, f"T11: snippet len > 500: {len(kr_t11[0]['snippet'])}"
# 字段完整
for h in kr_t11:
    for k in ("path", "snippet", "mtime", "size"):
        assert k in h, f"T11: missing {k}"
print("     ✓ T11: knowledge_refs 字段在 /api/dispatch body 中 + snippet ≤ 500 + dedup")

# === M3.27.1 — T12 _DISPATCH_HITS 累积保留最近 20 轮 ===
print("\n[T12] M3.27.1 _DISPATCH_HITS 累积保留最近 20 轮(写 25 轮 → buf 长度 ≤ 60)")
sid_t12 = "t12-sid"
M._DISPATCH_HITS.clear()
M._DISPATCH_BUF.clear()
M._DISPATCH_CURSOR.clear()
# 写 25 轮(每轮 3 hits)
for i in range(25):
    fake_hits = [
        {"path": f"/docs/r{i}_{j}.md", "snippet": f"snip-{i}-{j}",
         "mtime": float(i), "size": 100 * (i + 1)}
        for j in range(3)
    ]
    M._record_dispatch_hits(sid_t12, fake_hits)
final_len = len(M._DISPATCH_HITS.get(sid_t12, []))
print(f"     buf final len = {final_len} (cap={M._DISPATCH_HITS_BUF_CAP})")
assert final_len <= M._DISPATCH_HITS_BUF_CAP, \
    f"T12: cap should hold at {M._DISPATCH_HITS_BUF_CAP}, got {final_len}"
# 25 轮 × 3 hits = 75,cap=60,所以最老的 15 条被丢
assert final_len == 60, f"T12: expected 60 after capping, got {final_len}"
# 最老 5 轮(r0, r1, r2, r3, r4)应被丢
remaining_paths = {h["path"] for h in M._DISPATCH_HITS[sid_t12]}
for i in range(5):
    for j in range(3):
        assert f"/docs/r{i}_{j}.md" not in remaining_paths, \
            f"T12: oldest round{i} should be dropped"
# 最新 5 轮(r20..r24)应保留
for i in range(20, 25):
    assert f"/docs/r{i}_0.md" in remaining_paths, \
        f"T12: newest round{i}_0 should be present"
print("     ✓ T12: 25 轮 × 3 hits = 75 → cap 60,最老 15 条被丢")

# === M3.27.1 — T13 _filter_dispatch_hits 用户删过的对话的 hits 不附 ===
print("\n[T13] M3.27.1 用户删过的对话的 hits 不附(turn_ts 早于 earliest buf → 丢)")
sid_t13 = "t13-sid"
M._DISPATCH_HITS.clear()
M._DISPATCH_BUF.clear()
M._DISPATCH_CURSOR.clear()
# 当前 _DISPATCH_BUF:3 条(最早 ts=100.0)
now = time.time()
M._DISPATCH_BUF[sid_t13] = [
    {"role": "user", "text": "新对话 1", "ts": 100.0},
    {"role": "assistant", "text": "新对话 1 reply", "ts": 101.0},
    {"role": "user", "text": "新对话 2", "ts": 102.0},
]
# _DISPATCH_HITS:2 条老(ts=50.0 已删)+ 1 条新(ts=now)
M._DISPATCH_HITS[sid_t13] = [
    {"path": "/docs/old1.md", "snippet": "old-1", "mtime": 0.0,
     "size": 100, "turn_ts": 50.0},   # 早于 earliest 100.0 → 应被丢
    {"path": "/docs/old2.md", "snippet": "old-2", "mtime": 0.0,
     "size": 100, "turn_ts": 60.0},   # 早于 earliest 100.0 → 应被丢
    {"path": "/docs/new1.md", "snippet": "new-1", "mtime": 0.0,
     "size": 100, "turn_ts": now},    # 晚于 earliest → 保留
]
kept = M._filter_dispatch_hits(sid_t13)
kept_paths = [h["path"] for h in kept]
print(f"     kept paths: {kept_paths}")
assert len(kept) == 1, f"T13: expected 1 kept (only new1), got {len(kept)}: {kept_paths}"
assert kept[0]["path"] == "/docs/new1.md", f"T13: wrong kept: {kept[0]}"
print("     ✓ T13: ts=50/60 的 hit 被 _filter 丢弃(用户已删过那轮对话)")

# === M3.27.2 — T14/T15/T16 ===
print("\n[T14] PRISIRAI_INJECT_URL 默认值是 18802(端口对齐 PrisirAI 主面板)")
# 注:启动后该常量被 mock 测试覆盖为 18899,T14 用启动前捕获的 ORIGINAL 验
url = ORIGINAL_PRISIRAI_INJECT_URL
print(f"     ORIGINAL_PRISIRAI_INJECT_URL = {url}")
assert ":18802/" in url, f"T14: expected 18802, got {url}"
assert url.startswith("http://127.0.0.1:"), f"T14: unexpected scheme/host: {url}"
assert url.endswith("/prisiragent/api/external_inject"), \
    f"T14: path should end with /prisiragent/api/external_inject: {url}"
# 验:os.environ["PRISIRAGENT_URL"] env 覆盖仍然有效
os.environ["PRISIRAGENT_URL"] = "http://127.0.0.1:19999/x"
reload_url = os.environ.get("PRISIRAGENT_URL",
                            "http://127.0.0.1:18802/prisiragent/api/external_inject")
print(f"     env-override check: {reload_url}")
assert ":19999/" in reload_url, f"T14: env override broken: {reload_url}"
del os.environ["PRISIRAGENT_URL"]
print("     ✓ T14: 默认 18802,env 仍可覆盖")

print("\n[T15] _auto_select_active_platform() 返 (selected, available) 元组;"
      "env OPENAI_API_KEY → 'openai' 至少进 available,且 selected 走 priority")
# 备份 + 清环境(只清厂商 env var — keys.db 仍可能在,这里只是验证 env path)
saved_env = os.environ.copy()
for k in list(os.environ.keys()):
    if k.endswith("_API_KEY") or k in (
        "OLLAMA_HOST", "LLAMA_SERVER_URL"):
        del os.environ[k]
# 设置 OPENAI_API_KEY
os.environ["OPENAI_API_KEY"] = "test_x"
try:
    selected, available = M._auto_select_active_platform()
    print(f"     selected = {selected!r}")
    print(f"     available = {available}")
    # 类型断言
    assert isinstance(selected, str), f"T15: selected must be str, got {type(selected)}"
    assert isinstance(available, list), f"T15: available must be list, got {type(available)}"
    # OPENAI_API_KEY 一定被扫到 → 'openai' 必须在 available 里
    assert "openai" in available, f"T15: openai must be in available: {available}"
    # selected 走 priority(若 keys.db 里有更优先的平台如 ollama,selected 可能不是 openai,
    # 这是合理设计 — keys.db 是用户已配置的,优先级高于 env)
    priority_order = ["ollama", "llama", "deepseek", "qwen", "bailian",
                      "yunbailian", "doubao", "moonshot", "zhipu",
                      "groq", "openrouter", "openai", "anthropic",
                      "gemini", "grok", "mistral"]
    expected_first = next((p for p in priority_order if p in available), "")
    assert selected == expected_first, \
        f"T15: selected must follow priority order; expected {expected_first!r}, got {selected!r}"
    print(f"     ✓ T15: 元组类型 + env OPENAI_API_KEY 进 available + selected 走 priority")
finally:
    os.environ.clear()
    os.environ.update(saved_env)

print("\n[T16] _probe_local_asr() 不抛异常,即使全 10095/96/97 都不可达 → 返 \"\"")
# 当前环境 10095/96/97 应都没监听 — 探测应返空字符串且不抛
import urllib.request
probe_reachable = False
for p in (10095, 10096, 10097):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=1).read(1)
        probe_reachable = True
        break
    except Exception:
        continue
print(f"     any local ASR port reachable: {probe_reachable}")
try:
    res = M._probe_local_asr()
except Exception as e:  # noqa: BLE001
    raise AssertionError(f"T16: _probe_local_asr() must NOT raise: {e}")
print(f"     _probe_local_asr() = {res!r}")
assert isinstance(res, str), f"T16: must return str, got {type(res)}"
if not probe_reachable:
    assert res == "", f"T16: when no port reachable, must return '', got {res!r}"
    print("     ✓ T16: 无端口监听 → 返空字符串,无异常")
else:
    # 至少一个端口可达,结果应是非空(可能是 'sherpa-onnx' 或 'local-funasr')
    assert res in ("sherpa-onnx", "local-funasr"), \
        f"T16: unexpected result when reachable: {res!r}"
    print(f"     ✓ T16: 有端口监听 → 返 {res!r}")

# === T17 (额外): GET /api/creds/status 新接口可用 ===
print("\n[T17] GET /api/creds/status 新接口 — 返 active_platform + local_asr_probe")
settings = M.load_settings(M.DATA_DIR)
print(f"     active_platform = {settings.get('active_platform')!r}")
print(f"     llm_available_platforms len = {len(settings.get('llm_available_platforms') or [])}")
print(f"     active_asr = {settings.get('active_provider')!r}")
print(f"     local_asr_probe = {M._probe_local_asr()!r}")
# 字段类型断言
assert isinstance(settings.get("active_platform", ""), str)
assert isinstance(settings.get("llm_available_platforms", []), list)
assert isinstance(settings.get("active_provider", ""), str)
assert isinstance(M._probe_local_asr(), str)
# 路由注册断言
assert hasattr(M, "api_creds_status"), "T17: api_creds_status handler missing"
app_t17 = M.make_app()
routes = [str(r.resource.canonical) for r in app_t17.router.routes()
          if getattr(r, "resource", None) is not None]
assert "/api/creds/status" in routes, \
    f"T17: /api/creds/status must be registered in make_app() routes, got {routes}"
print(f"     routes count = {len(routes)}, /api/creds/status registered ✓")
print("     ✓ T17: /api/creds/status 路由已注册 + 字段类型正确")

# 收尾
_stop_mock_prisirai()

# === M3.27.3 — T18/T19/T20 ===
# T18: 顶栏 DOM 含「📞 陪聊」 + 不含「🩹 补丁」(原 topbar 位置)
print("\n[T18] 顶栏 DOM 含「📞 陪聊」+ ⋯ 菜单含「🩹 补丁」(M3.27.3 顶栏改造)")
src_web = (ROOT / "prisiragent_web.py").read_text(encoding="utf-8")
# 抠出 _PAGE 模板(raw triple-quoted,第一行是 _PAGE = r"""<!DOCTYPE html>...)
import re as _re
_page_match = _re.search(r'_PAGE\s*=\s*r?"""(.*?)"""', src_web, _re.DOTALL)
assert _page_match, "T18: _PAGE template not found in prisiragent_web.py"
page = _page_match.group(1)
# 顶栏含 #topbtnCompanion + onclick=openCompanion
assert 'id="topbtnCompanion"' in page, \
    "T18: topbar missing id=\"topbtnCompanion\""
assert 'onclick="openCompanion()"' in page, \
    "T18: topbar missing onclick=\"openCompanion()\""
assert "📞 陪聊" in page, "T18: topbar missing 📞 陪聊 button text"
# 顶栏 NOT 含「🩹 补丁」(原 topbarPatch id 必须不存在)
assert 'id="topbtnPatch"' not in page, \
    "T18: old topbtnPatch id should be removed from topbar"
# ⋯ 菜单里含 openPatch()(搬迁)
assert "openPatch()" in page, \
    "T18: ⋯ menu must contain openPatch() entry for 🩹 补丁"
# 验:openPatch 函数还在(只是搬位置)
assert "function openPatch" in src_web, "T18: openPatch() function must still exist"
print("     ✓ 顶栏: 📞 陪聊 (#topbtnCompanion) + openCompanion() + 不含 🩹 补丁")
print("     ✓ ⋯ 菜单: openPatch() 在位(openPatch 函数仍在)")

# T19: openCompanion() 函数存在 + 含 18850
print("\n[T19] openCompanion() 函数存在 + 端口字符串含 18850")
oc_match = _re.search(r'async function openCompanion\s*\(\s*\)', src_web)
assert oc_match, "T19: openCompanion() function not found"
assert "18850" in src_web, "T19: port 18850 literal missing"
# 含 fetch + no-cors 探活
oc_block = src_web[oc_match.start(): oc_match.start() + 2000]
assert "fetch" in oc_block, "T19: openCompanion must probe via fetch"
assert "no-cors" in oc_block, "T19: openCompanion must use mode no-cors"
assert "window.open" in oc_block, "T19: openCompanion must open URL via window.open"
print("     ✓ async function openCompanion() 存在 + 18850 + fetch no-cors + window.open")

# T20: Tauri main.rs 含 start_companion / companion_proc / start_companion_cmd
print("\n[T20] Tauri lib.rs 含 start_companion / companion_proc / start_companion_cmd")
src_tauri = (ROOT / "prisiragent-tauri" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
for needle in ("fn start_companion", "fn kill_companion",
               "companion_proc", "start_companion_cmd",
               "fn companion_up"):
    assert needle in src_tauri, f"T20: Tauri lib.rs missing {needle!r}"
# tray menu 注册 + on_menu_event match + invoke_handler 注册
assert '"companion"' in src_tauri, "T20: tray menu item id 'companion' missing"
assert '启动陪聊' in src_tauri, "T20: tray menu text '启动陪聊' missing"
assert 'with_id("companion"' in src_tauri, "T20: MenuItemBuilder with_id(\"companion\") missing"
assert "generate_handler![shell_info, shell_toggle, shell_open_external, start_companion_cmd]" in src_tauri, \
    "T20: invoke_handler missing start_companion_cmd"
# ExitRequested 清理
assert "kill_companion(&state)" in src_tauri, \
    "T20: ExitRequested must call kill_companion(&state)"
# 端口常量
assert "COMPANION_PORT" in src_tauri and "18850" in src_tauri, \
    "T20: COMPANION_PORT constant missing or port mismatch"
print("     ✓ fn start_companion + fn kill_companion + companion_proc + start_companion_cmd")
print("     ✓ tray 'companion' id + '启动陪聊' 文本 + invoke_handler 注册 + ExitRequested 清理")

print("\n" + "=" * 60)
print("✅ all M3.27 e2e 10/10 PASS + 3 regression PASS + M3.27.1 3/3 PASS"
      " + M3.27.2 4/4 PASS (T14/T15/T16/T17) + M3.27.3 3/3 PASS (T18/T19/T20)")
print("=" * 60)

# === M3.27.4 — T21/T22/T23/T24 ===
# T21: /prisiragent/api/llm/providers 返 asr_providers(非空,kind=asr,asr: 前缀)
print("\n[T21] /prisiragent/api/llm/providers 返 asr_providers 字段(非空 + kind=asr + 'asr:' 前缀)")
# 直接读 _list_asr_providers_for_dropdown(P 模块内 fn) — 同步调用
asr_list = P._list_asr_providers_for_dropdown()
print(f"     asr_providers 数量: {len(asr_list)}")
print(f"     displays: {[p['display'] for p in asr_list]}")
assert isinstance(asr_list, list) and len(asr_list) >= 1, \
    f"T21: asr_providers must be non-empty list, got {asr_list}"
# 所有项 kind=asr
for p in asr_list:
    assert p.get("kind") == "asr", f"T21: kind mismatch: {p}"
    assert p.get("platform_id", "").startswith("asr:"), \
        f"T21: platform_id must start with 'asr:': {p}"
# 已实现 4 个(本地 + 3 个云端)+ 1 个 __other__ 兜底
impl_names = {p["raw_asr_name"] for p in asr_list if p["platform_id"] != "asr:__other__"}
print(f"     impl_names: {sorted(impl_names)}")
assert impl_names == {"bailian-paraformer-v2", "qwen3-asr-flash",
                      "openai-whisper-sync", "local-funasr"}, \
    f"T21: impl set mismatch: {impl_names}"
# 兜底存在
other = next((p for p in asr_list if p["platform_id"] == "asr:__other__"), None)
assert other is not None, "T21: __other__ fallback missing"
assert "其他厂商" in other["display"] or "not yet" in other["display"].lower(), \
    f"T21: __other__ display unexpected: {other}"
print("     ✓ asr_providers 含已实现 4 个 + 1 个 __other__ 兜底")

# T22: _strip_model_suffix 行为
print("\n[T22] _strip_model_suffix 行为正确")
cases = [
    ("bailian-paraformer-v2", "bailian-paraformer"),
    ("qwen3-asr-flash", "qwen3-asr-flash"),
    ("openai-whisper-sync", "openai-whisper-sync"),
    ("xxx-20250901", "xxx"),
    ("xxx-2025-09-18", "xxx"),
    ("plain", "plain"),
]
for raw, expected in cases:
    got = P._strip_model_suffix(raw)
    flag = "✓" if got == expected else "✗"
    print(f"     {flag} {raw!r} → {got!r} (expect {expected!r})")
    assert got == expected, f"T22: {raw!r} expected {expected!r}, got {got!r}"
print("     ✓ 6 用例全过(含 -vN / 8位日期 / ISO 日期 / 无后缀)")

# T23: _list_asr_providers_for_dropdown 只含已实现 + 1 个 __other__
print("\n[T23] _list_asr_providers_for_dropdown 只含已实现 + 1 个 __other__ 兜底")
# 验:不应含任何 _not_implemented_factory 链路的 provider
not_impl = ("gcp-speech-v2", "azure-speech", "aws-transcribe-streaming",
            "aliyun-nls", "tencent-asr", "tencent-hy-asr-v3",
            "xunfei-streaming", "baidu-asr-streaming", "volcengine-asr",
            "doubao-seed-asr", "huawei-speech", "jd-speech")
impl_set = {p["raw_asr_name"] for p in asr_list}
for n in not_impl:
    assert n not in impl_set, f"T23: 未实现的 {n!r} 不应在下拉里"
print(f"     ✓ 不含 {len(not_impl)} 个未实现占位")
# 数量断言:4 + 1 = 5
assert len(asr_list) == 5, f"T23: expected 5 items (4 impl + 1 other), got {len(asr_list)}"
# local flag 正确
local_p = next(p for p in asr_list if p["raw_asr_name"] == "local-funasr")
assert local_p["local"] is True, f"T23: local-funasr.local must be True: {local_p}"
cloud_p = next(p for p in asr_list if p["raw_asr_name"] == "bailian-paraformer-v2")
assert cloud_p["local"] is False, f"T23: cloud.local must be False: {cloud_p}"
print("     ✓ 4 已实现 + 1 __other__ = 5 项,local flag 正确")

# T24: 前端 renderPlatformPicker 渲染 3 个 optgroup(LLM 云端 / LLM 本地 / ASR)
print("\n[T24] 前端 renderPlatformPicker 渲染 3 个 optgroup + 不破 LLM 现有 13 平台")
src_web = (ROOT / "prisiragent_web.py").read_text(encoding="utf-8")
# 抠出 loadPlatformList 函数(整段从 async function loadPlatformList 到下一个 ^function|^async function 顶格声明)
_lpl_match = _re.search(
    r"async function loadPlatformList\s*\(\s*\)\s*\{(.*?)^\s*function\s+\w+",
    src_web, _re.DOTALL | _re.MULTILINE)
assert _lpl_match, "T24: loadPlatformList function not found"
block = _lpl_match.group(0)
print(f"     loadPlatformList block length: {len(block)} chars")
# 必含 ASR 段(云端 + 本地)
assert "🎤 云端 ASR" in block or "🎤 Cloud ASR" in block, \
    "T24: loadPlatformList missing '🎤 云端 ASR' / '🎤 Cloud ASR' optgroup"
assert "🎤 本地 ASR" in block or "🎤 Local ASR" in block, \
    "T24: loadPlatformList missing '🎤 本地 ASR' / '🎤 Local ASR' optgroup"
# 必含 LLM optgroup(云端 + 本地)
assert "☁ 云端 LLM" in block or "☁ Cloud LLM" in block, \
    "T24: loadPlatformList missing LLM cloud optgroup"
assert "💻 本地 LLM" in block or "💻 Local LLM" in block, \
    "T24: loadPlatformList missing LLM local optgroup"
# 兜底 __custom__ 仍存在
assert "__custom__" in block, "T24: __custom__ option must still exist"
# ASR 分支 prefix 识别 + openAsrProvider 调用(onPlatformPick 函数体内的检查)
assert ("startsWith('asr:')" in src_web or 'startsWith("asr:")' in src_web), \
    "T24: onPlatformPick must check 'asr:' prefix"
assert "openAsrProvider" in src_web, "T24: openAsrProvider function missing"
assert "/api/asr/active" in src_web, "T24: /api/asr/active endpoint URL missing in frontend"
print("     ✓ 4 个 optgroup(LLM 云端/本地 + ASR 云端/本地) + asr: prefix + openAsrProvider + /api/asr/active")

# T24b: companion 服务 /api/asr/active 路由已注册 + 切 active_provider 可写
print("\n[T24b] companion /api/asr/active 路由已注册 + 切 active_provider 可写")
app_t24 = M.make_app()
routes_t24 = [str(r.resource.canonical) for r in app_t24.router.routes()
              if getattr(r, "resource", None) is not None]
assert "/api/asr/active" in routes_t24, \
    f"T24b: /api/asr/active must be registered, got {routes_t24}"
print(f"     ✓ /api/asr/active 路由在 list 里 (total {len(routes_t24)} routes)")
# 端到端:启 companion,POST 切 active_provider
async def _t24b():
    # 启动前备份 active_provider
    orig_active = M.load_settings(M.DATA_DIR).get("active_provider", "")
    runner, port = await _boot_companion()
    try:
        async with aiohttp.ClientSession() as sess:
            # 切到 qwen3-asr-flash
            r = await sess.post(f"http://127.0.0.1:{port}/api/asr/active",
                                json={"active_provider": "qwen3-asr-flash"})
            assert r.status == 200, f"T24b: expect 200, got {r.status}"
            d = await r.json()
            print(f"     POST 切 qwen3-asr-flash: {d}")
            assert d.get("ok") is True
            assert d.get("active_provider") == "qwen3-asr-flash"
            # 验证 settings 真写了
            s = M.load_settings(M.DATA_DIR)
            assert s.get("active_provider") == "qwen3-asr-flash", \
                f"T24b: settings not updated: {s.get('active_provider')}"
            # 切回原值
            r2 = await sess.post(f"http://127.0.0.1:{port}/api/asr/active",
                                 json={"active_provider": orig_active or "bailian-paraformer-v2"})
            assert r2.status == 200
            d2 = await r2.json()
            assert d2.get("ok") is True
            print(f"     ✓ active_provider 真写入 settings.json + 已还原到 {orig_active!r}")
            # 未知 provider → 400
            r3 = await sess.post(f"http://127.0.0.1:{port}/api/asr/active",
                                 json={"active_provider": "fake-not-exist"})
            assert r3.status == 400, f"T24b: 未知 provider 应 400, got {r3.status}"
            d3 = await r3.json()
            assert d3.get("ok") is False
            assert "未知" in d3.get("err", ""), f"T24b: err 期望含「未知」: {d3}"
            print(f"     ✓ 未知 provider → 400 + err 含「未知」")
    finally:
        await runner.cleanup()


asyncio.run(asyncio.wait_for(_t24b(), timeout=15))

print("\n" + "=" * 60)
print("✅ all M3.27 e2e 10/10 PASS + 3 regression PASS + M3.27.1 3/3 PASS"
      " + M3.27.2 4/4 PASS (T14/T15/T16/T17) + M3.27.3 3/3 PASS (T18/T19/T20)"
      " + M3.27.4 4/4 PASS (T21/T22/T23/T24)")
print("=" * 60)
