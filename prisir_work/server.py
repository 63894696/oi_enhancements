"""Prisir 工坊(PrisirWork)HTTP server:token 校验 + 白名单路由 + 仅 127.0.0.1。

红线①:只监听回环(config.ensure_loopback 硬校验)。
红线③:只路由 endpoints 注册表里的端点,其余 404。
敏感端点(auth=True)校验 X-OI-Token,无/错 → 401。
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, audit, config, endpoints


def _check_token(presented: str, expected: str) -> bool:
    """常量时间比对,防时序侧信道。两边都非空才比。"""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


class Handler(BaseHTTPRequestHandler):
    server_version = f"prisir-work/{__version__}"
    # 由 run() 注入
    token: str = ""

    # 安静一点:不写 stderr 访问日志(避免把 path/token 落日志)。
    def log_message(self, fmt, *args):  # noqa: D401
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > 1_000_000:  # 1MB 上限,防撑爆
            return {"__too_big__": True}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8")) or {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"__bad_json__": True}

    def _dispatch(self, method: str) -> None:
        # 只认 path,不带 query 参与白名单匹配
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        entry = endpoints.lookup(path)
        # 红线③:白名单外一律 404(不暴露存在性之外的任何信息)
        if not entry or entry["method"] != method:
            return self._send({"ok": False, "error": "not_found"}, 404)
        # 红线①配套:敏感端点强制 token
        if entry["auth"]:
            presented = self.headers.get("X-OI-Token", "")
            if not _check_token(presented, self.token):
                return self._send({"ok": False, "error": "unauthorized"}, 401)
        body = self._read_body() if method == "POST" else {}
        if body.get("__too_big__"):
            return self._send({"ok": False, "error": "payload_too_large"}, 413)
        if body.get("__bad_json__"):
            return self._send({"ok": False, "error": "bad_json"}, 400)
        try:
            payload, status = entry["handler"](body)
        except Exception as e:  # 兜底:不让单端点异常拖垮常驻进程
            return self._send({"ok": False, "error": "internal", "detail": type(e).__name__}, 500)
        # F5 审计:L1+ 端点留痕(只记元信息,不记 body/口令/token)
        audit.record(path, method, entry.get("risk", "L0"), bool(payload.get("ok", True)), status)
        self._send(payload, status)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # 其余方法一律不支持
    def do_PUT(self):    self._send({"ok": False, "error": "not_found"}, 404)
    def do_DELETE(self): self._send({"ok": False, "error": "not_found"}, 404)
    def do_PATCH(self):  self._send({"ok": False, "error": "not_found"}, 404)


def make_server(host: str, port: int, token: str) -> ThreadingHTTPServer:
    config.ensure_loopback(host)  # 红线①:非回环直接拒起
    Handler.token = token
    return ThreadingHTTPServer((host, port), Handler)


def run(host: str = config.HOST, port: int | None = None, token: str | None = None) -> None:
    port = port if port is not None else config.get_port()
    token = token if token is not None else config.load_or_create_token()
    srv = make_server(host, port, token)
    print(f"Prisir 工坊(prisir-work)就绪: http://{host}:{port}  (token 已加载, {len(endpoints.catalog())} 个白名单端点)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
