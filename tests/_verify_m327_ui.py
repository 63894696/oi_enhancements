# -*- coding: utf-8 -*-
"""M3.27 浏览器视觉验证 — Puppeteer + mock 两端 server,截图「派发到 PrisirAI #input 填充」视觉路径。

设计:
  - mock companion server (aiohttp @ 18850):
    * 服 companion/static/index.html + /static/*
    * ws: 接 user_text → mock 1 轮 AI 回答(也调 append_turn 风格写派发 buffer)
    * POST /api/dispatch → 调 urllib 真打 mock PrisirAI 18800
    * GET /api/dispatch/buffer → 返 buffer 状态
    * GET/POST /api/dispatch/settings → toggle

  - mock PrisirAI server (stdlib http.server @ 18800):
    * 服 minimal HTML(模仿 PrisirAI 主页面:含 #input + #send,无 .inject-flash)
    * POST /prisiragent/api/external_inject → push 进 _INJECT_QUEUE
    * GET /prisiragent/api/external_inject/peek → 返 queue
    * GET /prisiragent/api/external_inject/ack → 从 queue 移除

  - mock PrisirAI 页面内置 polling IIFE(模仿 prisiragent_web.py 的
    _setupExternalInjectPolling),发现新 id 就把 text 填 #input + 加 .inject-flash
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web, WSMsgType

# ============================================================
# 路径常量
# ============================================================
OI_ROOT = Path(r"C:\Users\Administrator\oi_enhancements")
STATIC_DIR = OI_ROOT / "companion" / "static"
SCREENSHOT_DIR = OI_ROOT / "tests" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

COMPANION_PORT = 18850
PRISIRAI_PORT = 18800

# ============================================================
# mock PrisirAI — stdlib http.server(模仿 prisiragent_web.py 的 BaseHTTPRequestHandler)
# ============================================================
_INJECT_QUEUE: list = []  # [{id, ts, text, source, sid}]
_INJECT_LOCK = threading.Lock()
_INJECT_MAX = 32

# minimal 主页(模仿 PrisirAI:含 #input textarea + #send button + .inject-flash CSS)
PRISIRAI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>PrisirAI Mock</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 720px; margin: 24px auto; padding: 0 16px; color: #222; background: #fafafa; }
  header { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #2d3748; color: #fff; border-radius: 8px; margin-bottom: 16px; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  .badge { background: #4a5568; color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
  textarea#input { width: 100%; min-height: 140px; padding: 12px; font-size: 14px; border: 2px solid #cbd5e0; border-radius: 6px; box-sizing: border-box; font-family: inherit; resize: vertical; }
  textarea#input:focus { outline: none; border-color: #4299e1; }
  .toolbar { margin-top: 12px; display: flex; gap: 8px; }
  button#send { padding: 8px 20px; background: #4299e1; color: #fff; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; }
  button#send:hover { background: #3182ce; }
  .meta { margin-top: 16px; font-size: 12px; color: #718096; }
  /* M3.27 inject-flash CSS(模仿 prisiragent_web.py:4136) */
  textarea#input.inject-flash { animation: inject-flash-anim 1.5s ease-out; }
  @keyframes inject-flash-anim {
    0%   { background-color: #ebf8ff; border-color: #4299e1; }
    70%  { background-color: #ebf8ff; border-color: #4299e1; }
    100% { background-color: #fff; border-color: #cbd5e0; }
  }
  .toast { position: fixed; top: 24px; right: 24px; background: #2d3748; color: #fff; padding: 12px 18px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity .25s; pointer-events: none; z-index: 1000; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>PrisirAI <span style="font-weight:400;font-size:12px;opacity:.8">mock</span></h1>
  <span class="badge">M3.27 验证</span>
</header>
<main>
  <textarea id="input" placeholder="在这里输入(等派发注入)…"></textarea>
  <div class="toolbar">
    <button id="send" onclick="sendMessage()">发送</button>
    <span class="meta" id="meta"></span>
  </div>
</main>
<div class="toast" id="toast"></div>
<script>
  // 记录 send 是否被自动触发(验证项 7)
  window.sendMessageCalled = false;
  window.__externalInjectStarted = false;
  window.__injectSeen = new Set();
  function sendMessage() {
    window.sendMessageCalled = true;
    var ta = document.getElementById('input');
    document.getElementById('meta').textContent = '已发送(' + (ta.value || '').length + ' 字符)';
  }
  function toast(msg) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function(){ t.classList.remove('show'); }, 2500);
  }
  // M3.27 注入 polling IIFE(模仿 prisiragent_web.py:_setupExternalInjectPolling)
  (function _setupExternalInjectPolling(){
    if (window.__externalInjectStarted) return;
    window.__externalInjectStarted = true;
    function _apply(item) {
      try {
        var ta = document.getElementById('input');
        if (!ta) return;
        var prefix = (ta.value && ta.value.length > 0) ? '\\n\\n' : '';
        ta.value = (ta.value || '') + prefix + (item.text || '');
        ta.scrollTop = ta.scrollHeight;
        ta.classList.add('inject-flash');
        setTimeout(function(){ ta.classList.remove('inject-flash'); }, 1500);
        toast('📥 来自 ' + (item.source || '外部') + ' 的内容已填入输入框(可编辑,确认后手动发送)');
        fetch('/prisiragent/api/external_inject/ack?id=' + encodeURIComponent(item.id || ''))
          .catch(function(){});
      } catch (err) { console.error('external_inject handler', err); }
    }
    setInterval(function(){
      fetch('/prisiragent/api/external_inject/peek')
        .then(function(r){ return r.json(); })
        .then(function(d){
          (d.items || []).forEach(function(item){
            if (!item || !item.id || window.__injectSeen.has(item.id)) return;
            window.__injectSeen.add(item.id);
            _apply(item);
          });
        })
        .catch(function(){});
    }, 900);
  })();
</script>
</body>
</html>
"""


class PrisirAIHandler(BaseHTTPRequestHandler):
    """模仿 prisiragent_web.py 的 BaseHTTPRequestHandler 协议。"""

    def log_message(self, fmt, *args):
        # 静默(避免污染 stdout)
        pass

    def _send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            body = PRISIRAI_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/prisiragent/api/external_inject/peek":
            with _INJECT_LOCK:
                items = list(_INJECT_QUEUE)
            self._send_json({"items": items})
            return
        if path == "/prisiragent/api/external_inject/ack":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            inj_id = (qs.get("id") or [""])[0]
            if not inj_id:
                self._send_json({"ok": False, "err": "id 必填"}, 400)
                return
            with _INJECT_LOCK:
                before = len(_INJECT_QUEUE)
                _INJECT_QUEUE[:] = [x for x in _INJECT_QUEUE if x["id"] != inj_id]
                removed = before - len(_INJECT_QUEUE)
            self._send_json({"ok": True, "removed": removed})
            return
        self._send_json({"ok": False, "err": f"unknown GET {path}"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/prisiragent/api/external_inject":
            body = self._read_body()
            text = (body.get("text") or "").strip()
            if not text:
                self._send_json({"ok": False, "err": "text 必填"}, 400)
                return
            item = {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "text": text,
                "source": body.get("source", "external"),
                "sid": body.get("sid", ""),
            }
            with _INJECT_LOCK:
                _INJECT_QUEUE.append(item)
                if len(_INJECT_QUEUE) > _INJECT_MAX:
                    _INJECT_QUEUE.pop(0)
            print(f"[mock PrisirAI] received inject id={item['id']} len={len(text)} src={item['source']}", flush=True)
            self._send_json({"ok": True, "id": item["id"], "queue_size": len(_INJECT_QUEUE)})
            return
        self._send_json({"ok": False, "err": f"unknown POST {path}"}, 404)


def start_prisirai_mock():
    """stdlib http.server 跑在 18800(后台线程)。"""
    srv = ThreadingHTTPServer(("127.0.0.1", PRISIRAI_PORT), PrisirAIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="mock-prisirai")
    t.start()
    print(f"[mock PrisirAI] listening http://127.0.0.1:{PRISIRAI_PORT}/", flush=True)
    return srv


# ============================================================
# mock companion — aiohttp @ 18850(模仿 prisiragent-companion-web.py)
# ============================================================

# 派发缓存 + cursor(模仿真服务端的 _DISPATCH_BUF / _DISPATCH_CURSOR / _DISPATCH_COUNTER)
_MOCK_DISPATCH_BUF: dict[str, list[dict]] = {}
_MOCK_DISPATCH_CURSOR: dict[str, int] = {}
_MOCK_DISPATCH_COUNTER: dict[str, int] = {}
_MOCK_SETTINGS = {"enable_dispatch": True}


async def handle_index(req):
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_static(req):
    p = req.match_info["path"]
    full = (STATIC_DIR / p).resolve()
    if not str(full).startswith(str(STATIC_DIR.resolve())):
        return web.Response(status=403)
    if not full.exists():
        return web.Response(status=404)
    return web.FileResponse(full)


async def ws_handler(req):
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    sid = req.query.get("sid") or f"mock-{uuid.uuid4().hex[:6]}"
    print(f"[mock companion ws] new connection sid={sid}", flush=True)
    await ws.send_json({"type": "hello", "sid": sid,
                        "continue": {"sid": sid, "preview": [], "hint": "mock 验证"}})
    await ws.send_json({"type": "history", "messages": []})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                d = json.loads(msg.data)
                print(f"[mock companion ws] recv {d.get('type')}", flush=True)
                if d.get("type") == "user_text":
                    text = d.get("text", "")
                    await ws.send_json({"type": "user_echo", "text": text, "src": "text"})
                    # 写派发 buffer(user + assistant 都记)
                    buf = _MOCK_DISPATCH_BUF.setdefault(sid, [])
                    buf.append({"role": "user", "text": text, "ts": time.time()})
                    # mock AI 回复
                    reply = f"好,我来想下。{text[-20:]}这事我给你建议是…(mock)"
                    for ch in reply:
                        await ws.send_json({"type": "ai_delta", "text": ch})
                        await asyncio.sleep(0.005)
                    buf.append({"role": "assistant", "text": reply, "ts": time.time()})
                    await ws.send_json({"type": "ai_done", "text": reply,
                                        "elapsed": "00:01", "platform": "mock", "model": "mock"})
                    print(f"[mock companion ws] buf now len={len(buf)}", flush=True)
                elif d.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
    except Exception as e:
        print(f"[mock companion ws] {e}", file=sys.stderr, flush=True)
    return ws


async def api_dispatch(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except (TypeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    sid = (body.get("sid") or "").strip()
    if not sid:
        return web.json_response({"ok": False, "err": "sid 必填"}, status=400)
    mode = body.get("mode", "incremental")
    if not _MOCK_SETTINGS.get("enable_dispatch", True):
        return web.json_response({"ok": False, "err": "派发功能已关闭"}, status=403)
    # 拼文本(模仿 _format_dispatch)
    buf = _MOCK_DISPATCH_BUF.get(sid, [])
    cursor = _MOCK_DISPATCH_CURSOR.get(sid, 0)
    if mode == "incremental":
        new_turns = buf[cursor:]
    else:
        new_turns = list(buf)
    if not new_turns:
        return web.json_response({"ok": False, "err": "无新增对话可派发", "count": 0, "total": len(buf)})
    lines = [
        f"[对话上下文 · 来自 Prisir 陪聊 · {time.strftime('%Y-%m-%d %H:%M')} · 已派发 {_MOCK_DISPATCH_COUNTER.get(sid, 0) + 1} 次]",
        "",
    ]
    for t in new_turns:
        who = "user" if t["role"] == "user" else "assistant"
        lines.append(f"{who}: {t['text']}")
    lines.extend(["", "[意图]", "(空,用户可填)"])
    text = "\n".join(lines)
    prisirai_url = f"http://127.0.0.1:{PRISIRAI_PORT}/prisiragent/api/external_inject"
    payload = {"text": text, "source": "companion", "sid": sid}
    # 真打 mock PrisirAI(urllib 同步,放 to_thread)
    def _post():
        import urllib.request, urllib.error
        req2 = urllib.request.Request(
            prisirai_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req2, timeout=5)

    try:
        resp = await asyncio.to_thread(_post)
        body_resp = json.loads(resp.read().decode("utf-8"))
        if not body_resp.get("ok"):
            return web.json_response({"ok": False, "err": f"PrisirAI 拒绝: {body_resp.get('err', '?')}",
                                       "count": len(new_turns), "total": len(buf),
                                       "prisirai": prisirai_url}, status=502)
        count = len(new_turns)
        _MOCK_DISPATCH_CURSOR[sid] = _MOCK_DISPATCH_CURSOR.get(sid, 0) + count
        _MOCK_DISPATCH_COUNTER[sid] = _MOCK_DISPATCH_COUNTER.get(sid, 0) + 1
        print(f"[mock companion /api/dispatch] ok count={count} cursor={_MOCK_DISPATCH_CURSOR[sid]} dispatches={_MOCK_DISPATCH_COUNTER[sid]}", flush=True)
        return web.json_response({
            "ok": True,
            "count": count,
            "total": len(buf),
            "cursor": _MOCK_DISPATCH_CURSOR[sid],
            "dispatches": _MOCK_DISPATCH_COUNTER[sid],
            "prisirai": prisirai_url,
            "id": body_resp.get("id", ""),
        })
    except Exception as e:
        return web.json_response({"ok": False, "err": f"PrisirAI 不可达: {e}",
                                   "count": len(new_turns), "total": len(buf),
                                   "prisirai": prisirai_url}, status=502)


async def api_dispatch_buffer(req: web.Request) -> web.Response:
    sid = req.query.get("sid", "")
    prisirai_url = f"http://127.0.0.1:{PRISIRAI_PORT}/prisiragent/api/external_inject"
    # debug: 列出所有 sid 的 buf 概况(仅 mock 用,真服务端没这端点)
    if sid == "__all__":
        out = {}
        for s, buf in _MOCK_DISPATCH_BUF.items():
            out[s] = {
                "total": len(buf),
                "dispatched": _MOCK_DISPATCH_CURSOR.get(s, 0),
                "pending": max(0, len(buf) - _MOCK_DISPATCH_CURSOR.get(s, 0)),
                "dispatches": _MOCK_DISPATCH_COUNTER.get(s, 0),
            }
        return web.json_response({"all": out, "prisirai_url": prisirai_url})
    buf = _MOCK_DISPATCH_BUF.get(sid, [])
    cursor = _MOCK_DISPATCH_CURSOR.get(sid, 0)
    return web.json_response({
        "sid": sid,
        "total": len(buf),
        "dispatched": cursor,
        "pending": max(0, len(buf) - cursor),
        "dispatches": _MOCK_DISPATCH_COUNTER.get(sid, 0),
        "prisirai_url": prisirai_url,
    })


async def api_dispatch_settings(req: web.Request) -> web.Response:
    prisirai_url = f"http://127.0.0.1:{PRISIRAI_PORT}/prisiragent/api/external_inject"
    if req.method == "GET":
        return web.json_response({
            "enable_dispatch": _MOCK_SETTINGS.get("enable_dispatch", True),
            "prisirai_url": prisirai_url,
            "trigger_phrases": ["交给 PrisirAI", "派过去", "派给 PrisirAI", "让 PrisirAI 做",
                                  "dispatch to prisirai", "交给主面板"],
        })
    try:
        body = await req.json()
    except (TypeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    _MOCK_SETTINGS["enable_dispatch"] = bool(body.get("enable_dispatch", True))
    return web.json_response({"ok": True, "enable_dispatch": _MOCK_SETTINGS["enable_dispatch"]})


def make_companion_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/static/{path:.*}", handle_static)
    app.router.add_post("/api/dispatch", api_dispatch)
    app.router.add_get("/api/dispatch/buffer", api_dispatch_buffer)
    app.router.add_route("*", "/api/dispatch/settings", api_dispatch_settings)
    return app


async def start_companion_mock():
    runner = web.AppRunner(make_companion_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", COMPANION_PORT)
    await site.start()
    print(f"[mock companion] listening http://127.0.0.1:{COMPANION_PORT}/", flush=True)
    return runner


# ============================================================
# 主入口(供外部触发 Puppeteer 验证步骤时调用)
# ============================================================
async def main():
    prisirai = start_prisirai_mock()
    runner = await start_companion_mock()
    print("=== mock servers up. companion:18850 + prisirai:18800 ===", flush=True)
    print("按 Ctrl+C 退出", flush=True)
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        prisirai.shutdown()


if __name__ == "__main__":
    asyncio.run(main())