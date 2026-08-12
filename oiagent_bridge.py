"""oiagent_bridge.py — oiagent↔扩展桥方向A (M5 深入研)

给浏览器扩展提供轻量 HTTP 任务接口,跑耗时的深入研究(deep-research)。
复用 oiagent_web.py 验证过的模式: PrisirKeyStore + PrisirRouter + 后台线程执行。

安全红线(与计划 §六一致):
  - 只绑 127.0.0.1(不监听外部接口)
  - kind 白名单: 仅 deep-research;其他 kind 一律 400
  - KEY 只读本机 PrisirKeyStore(sqlite),不收/不传浏览器端 key
  - 不做 CORS 放开; 仅响应本机 fetch

Usage:
  python oiagent_bridge.py [--port 12308] [--strategy smart]

API:
  POST /task            {kind:"deep-research", query, context?} -> {taskId}
  GET  /task/<id>       -> {taskId, status, kind, result?, error?}
  GET  /health          -> {ok, platforms}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastlane.providers.llm_prisir import (  # noqa: E402
    PrisirKeyStore, PrisirRouter,
)

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = int(os.environ.get("OIAGENT_BRIDGE_PORT", "12308"))
DEFAULT_STRATEGY = os.environ.get("PRISIR_STRATEGY", "smart")

# kind 白名单 —— 只放行深入研; 防桥被当成通用 prompt 注入面
_ALLOWED_KINDS = {"deep-research"}

_key_store = PrisirKeyStore()
_router = PrisirRouter(_key_store)


def _env_key_candidates():
    """env 里的云端 key 候选(key store 指向的本地代理没起时用)。
    与 mcp_oiagent_server 同一套 BAILIAN/AGNES/MOONSHOT env 约定; 只读不打印。
    返回 [(platform_name, base_url, api_key, model), ...](可能多个, 调用方逐个试)。"""
    cands = [
        ("moonshot", "MOONSHOT_BASE_URL", "MOONSHOT_API_KEY", "moonshot-v1-8k"),
        ("bailian", "BAILIAN_BASE_URL", "BAILIAN_API_KEY", "qwen-plus"),
        ("agnes", "AGNES_BASE_URL", "AGNES_API_KEY", "agnes"),
    ]
    out = []
    for name, bu, ak, model in cands:
        base, key = os.environ.get(bu), os.environ.get(ak)
        if base and key:
            out.append((name, base.rstrip("/"), key, model))
    return out


async def _gen_with_fallback(msgs, strategy, temperature, max_tokens):
    """先用 PrisirRouter(本地代理); 失败时逐个回落到 env 云端 key 候选。
    返回 {text, platform, model, task_type}。"""
    import httpx
    try:
        return await _router.generate(msgs, strategy=strategy,
                                      temperature=temperature, max_tokens=max_tokens)
    except Exception:  # noqa: BLE001
        pass  # 本地代理没起 → 走 env 候选
    cands = _env_key_candidates()
    if not cands:
        raise RuntimeError("PrisirRouter 不可用且 env 无云端 key 兜底")
    last_err = None
    for name, base, key, model in cands:
        payload = {"model": model, "messages": msgs,
                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(f"{base}/chat/completions", json=payload,
                                      headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                data = r.json()
            text = data["choices"][0]["message"]["content"]
            return {"text": text, "platform": f"env:{name}", "model": model, "task_type": "fallback"}
        except Exception as e:  # noqa: BLE001
            last_err = e  # 这个候选不行, 试下一个
    raise RuntimeError(f"env 云端 key 候选全部失败, 最后错误: {last_err}")


_FOLLOWUP_PROMPT = (
    "基于上面的问答,生成 {n} 个用户最可能想接着问的相关延续话题。"
    "要求:每条不超过 20 字,是问句或祈使句,彼此角度不同(优缺点/实现/资源/对比/深入)。"
    "只输出 JSON 数组字符串,不要其他内容。例: [\"话题1\",\"话题2\"]"
)


async def _followups_with_fallback(question, answer, n=4):
    """延续话题生成,走 _gen_with_fallback 同一 fallback。失败返回 [](不阻塞主报告)。"""
    import json as _json
    import re as _re
    n = max(2, min(5, n))
    convo = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer[:2000]},
        {"role": "user", "content": _FOLLOWUP_PROMPT.format(n=n)},
    ]
    try:
        res = await _gen_with_fallback(convo, strategy=DEFAULT_STRATEGY,
                                       temperature=0.7, max_tokens=300)
        text = res["text"].strip()
        m = _re.search(r"\[.*\]", text, _re.S)
        if not m:
            return []
        arr = _json.loads(m.group(0))
        return [str(x)[:60] for x in arr if isinstance(x, str)][:n]
    except Exception:  # noqa: BLE001
        return []

# 任务表(内存; 任务是一次性的, 落盘无意义)。task_id -> {status, kind, result, error, ts}
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


# ============================================================
# 深入研执行(后台线程 → asyncio 跑 router)
# ============================================================
_DEEP_SYSTEM = (
    "你是一名严谨的研究助手。用户给你一个主题,你要做深入研究并输出结构化的中文报告。"
    "要求: ① 先给一段 TL;DR(3 句内); ② 再用 markdown 分节展开(背景/核心机制/对比/落地/风险);"
    " ③ 论断要有依据, 不确定就明说; ④ 精炼, 总长不超过 1500 字; ⑤ 结尾列出 3-5 个值得继续深挖的方向。"
)


def _run_deep_research(task_id: str, query: str, context: str):
    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
    try:
        msgs = [{"role": "system", "content": _DEEP_SYSTEM}]
        if context:
            msgs.append({"role": "user", "content": f"(背景上下文)\n{context[:3000]}"})
        msgs.append({"role": "user", "content": f"深入研究主题: {query}"})

        res = asyncio.run(_gen_with_fallback(msgs, strategy=DEFAULT_STRATEGY,
                                             temperature=0.4, max_tokens=4096))
        report = res["text"]
        used = f"{res['platform']}:{res['model']}"

        # 报告末尾再挂延续话题(Perplexity Related 思路); 走同一 fallback 保证 router 死了也能出
        followups = asyncio.run(_followups_with_fallback(query, report)) \
            if len(report) < 6000 else []

        with _tasks_lock:
            _tasks[task_id].update({
                "status": "done",
                "result": {"report": report, "followups": followups, "model": used},
            })
    except Exception as e:  # noqa: BLE001
        with _tasks_lock:
            _tasks[task_id].update({
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })


# ============================================================
# HTTP 处理
# ============================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802
        pass

    def _json(self, data, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 只服务本机页面; 不放开 CORS(浏览器扩展 fetch 127.0.0.1 受 host_permissions 管)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # ---------------- GET ----------------
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        path = u.path
        if path == "/health":
            self._json({"ok": True, "platforms": _router.available_platforms(),
                        "strategy": DEFAULT_STRATEGY})
        elif path.startswith("/task/"):
            task_id = path[len("/task/"):].strip("/")
            with _tasks_lock:
                t = _tasks.get(task_id)
            if not t:
                self._json({"error": "not found"}, 404)
                return
            out = {"taskId": task_id, "status": t["status"], "kind": t["kind"]}
            if t.get("result") is not None:
                out["result"] = t["result"]
            if t.get("error"):
                out["error"] = t["error"]
            self._json(out)
        else:
            self._json({"error": "not found"}, 404)

    # ---------------- POST ----------------
    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path != "/task":
            self._json({"error": "not found"}, 404)
            return
        body = self._read_body()
        kind = (body.get("kind") or "").strip()
        # 白名单校验
        if kind not in _ALLOWED_KINDS:
            self._json({"error": f"kind not allowed: {kind or '(empty)'}"}, 400)
            return
        query = (body.get("query") or "").strip()
        if not query:
            self._json({"error": "empty query"}, 400)
            return
        context = (body.get("context") or "").strip()

        task_id = uuid.uuid4().hex[:12]
        with _tasks_lock:
            _tasks[task_id] = {"status": "queued", "kind": kind,
                               "result": None, "error": None, "ts": time.time()}
        t = threading.Thread(target=_run_deep_research,
                             args=(task_id, query, context), daemon=True)
        t.start()
        self._json({"taskId": task_id, "status": "queued"})


def main():
    global DEFAULT_STRATEGY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=BRIDGE_PORT)
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY)
    args = ap.parse_args()
    DEFAULT_STRATEGY = args.strategy

    srv = ThreadingHTTPServer((BRIDGE_HOST, args.port), Handler)
    print(f"oiagent 桥(方向A): http://{BRIDGE_HOST}:{args.port}  "
          f"路由={DEFAULT_STRATEGY}  平台={_router.available_platforms()}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
