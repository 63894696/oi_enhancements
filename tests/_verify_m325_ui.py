# -*- coding: utf-8 -*-
"""M3.25 浏览器 UI 验证 — mock ws server + Puppeteer 截图:
  - 启一个静态 server serve companion/static/
  - ws 路由 mock 出 user_echo + knowledge_refs + ai_delta + ai_done
  - Puppeteer 打开页面,user_text → 等徽标出现 → 截图
  - 再点「📎 来源」徽标 → 等列表展开 → 截图
"""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from aiohttp import web, WSMsgType

STATIC_DIR = Path(r"C:\Users\Administrator\oi_enhancements\companion\static")


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


# 静态 mock hits 数据(2 个 hit)
MOCK_HITS = [
    {"path": "C:/notes/ai-overview.md", "snippet":
     "AI 是研究如何让机器表现出智能行为的学科,核心是学习、推理、感知。"
     " 大模型是 AI 的子集,通过海量数据训练,涌现能力包括代码、推理、创作。",
     "mtime": 1726000000.0, "size": 1024, "is_ocr": False},
    {"path": "C:/notes/rag-intro.md", "snippet":
     "RAG(Retrieval Augmented Generation)检索增强生成:先用向量检索相关文档,"
     "再把检索结果拼进 prompt 让 LLM 基于真实文档回答,降低幻觉。",
     "mtime": 1726100000.0, "size": 2048, "is_ocr": False},
]


async def ws_handler(req):
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    sid = req.query.get("sid") or "mock-sid"
    print(f"[mock ws] new connection sid={sid}", flush=True)
    # 1. hello
    await ws.send_json({"type": "hello", "sid": sid,
                        "continue": {"sid": sid, "preview": [], "hint": "mock 验证"}})
    # 2. history (空)
    await ws.send_json({"type": "history", "messages": []})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                d = json.loads(msg.data)
                print(f"[mock ws] recv {d.get('type')}", flush=True)
                if d.get("type") == "user_text":
                    text = d.get("text", "")
                    # user_echo
                    await ws.send_json({"type": "user_echo", "text": text, "src": "text"})
                    print("[mock ws] sent user_echo", flush=True)
                    # knowledge_refs (mock 命中 2 条)
                    await ws.send_json({"type": "knowledge_refs", "hits": MOCK_HITS})
                    print(f"[mock ws] sent knowledge_refs hits={len(MOCK_HITS)}", flush=True)
                    # ai_delta 流式
                    reply = ("好的,知识库里有 2 条相关笔记:"
                             "「AI 总览」和「RAG 入门」。我结合这两篇给你讲一下:")
                    for ch in reply:
                        await ws.send_json({"type": "ai_delta", "text": ch})
                        await asyncio.sleep(0.005)
                    # ai_done
                    await ws.send_json({"type": "ai_done", "text": reply,
                                          "elapsed": "00:01", "platform": "mock", "model": "mock"})
                    print("[mock ws] sent ai_done", flush=True)
                elif d.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
    except Exception as e:
        print(f"[mock ws] {e}", file=sys.stderr)
    return ws


def make_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/static/{path:.*}", handle_static)
    return app


async def main():
    import os
    port = int(os.environ.get("VERIFY_PORT", "18999"))
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[mock server] http://127.0.0.1:{port}/")
    # 持久运行,直到外部 kill
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())