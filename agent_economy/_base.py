"""_base.py — 三件套共享基础:Ed25519 工具、agent 目录、SQLite 审计、stdlib HTTP 骨架。

复用现有模式,不引入新依赖:
  - Ed25519 用 cryptography 库(与 simplex_integrity.py 一致)
  - fp = sha256(raw_pubkey).hexdigest()[:16](与 simplex_integrity._set_pubkey 一致)
  - SQLite 用 check_same_thread=False + Lock(与 policy_engine.py 一致)
  - HTTP 用 ThreadingHTTPServer(与 l4_web.py 一致)
  - 数据目录用 ~/.local/share/aureon(与 l4_remote_relay.py 一致)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)

# ── 数据目录(与 l4_remote_relay 的 aureon 目录同级)──────────────────
DATA_DIR = Path(os.environ.get(
    "AGENT_ECONOMY_DIR",
    Path.home() / ".local" / "share" / "aureon" / "agent_economy"))


def data_path(name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


# ── Ed25519 工具 ────────────────────────────────────────────────────
def gen_keypair() -> tuple[Ed25519PrivateKey, str]:
    """生成密钥对,返回 (私钥对象, 公钥 b64)。"""
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return priv, pub_b64


def pubkey_b64(priv: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def fingerprint(pub_b64: str) -> str:
    """与 simplex_integrity._set_pubkey 一致:sha256(raw)[:16]。"""
    return hashlib.sha256(base64.b64decode(pub_b64)).hexdigest()[:16]


def sign_b64(priv: Ed25519PrivateKey, msg: bytes) -> str:
    return base64.b64encode(priv.sign(msg)).decode()


def verify_b64(pub_b64: str, msg: bytes, sig_b64: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(sig_b64), msg)
        return True
    except Exception:  # noqa: BLE001
        return False


def save_private_key(priv: Ed25519PrivateKey, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(priv.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_private_key(path: Path) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


# ── agent 目录(identity 的持久化)──────────────────────────────────
class AgentDirectory:
    """agent 公钥目录,JSON 落盘。保留 integrity_pubkeys.json 的 contacts 兼容,
    agent 条目放 "agents" 键下。线程安全。"""

    def __init__(self, path: Path | None = None):
        self._path = path or data_path("agents.json")
        self._lock = threading.Lock()
        if not self._path.exists():
            self._write({"agents": {}})

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"agents": {}}

    def _write(self, obj: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    def register(self, agent_id: str, pubkey_b64: str, display: str,
                 caps: list[str] | None = None, allow_overwrite: bool = False) -> dict:
        from . import schemas
        with self._lock:
            obj = self._read()
            agents = obj.setdefault("agents", {})
            if agent_id in agents and not allow_overwrite:
                return {"ok": False, "error": f"agent '{agent_id}' 已注册"}
            agents[agent_id] = schemas.agent_entry(
                pubkey_b64, fingerprint(pubkey_b64), display, time.time(), caps)
            self._write(obj)
            return {"ok": True, "fp": agents[agent_id]["fp"]}

    def get(self, agent_id: str) -> dict | None:
        with self._lock:
            return self._read().get("agents", {}).get(agent_id)

    def list_all(self) -> dict:
        with self._lock:
            return self._read().get("agents", {})


# ── SQLite 审计/账本(meter、authz 共用模式)─────────────────────────
class AuditDB:
    """线程安全 SQLite(policy_engine 同模式:check_same_thread=False + Lock)。"""

    def __init__(self, path: Path, ddl: str):
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(ddl)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()


# ── stdlib HTTP 服务骨架(l4_web 同模式)─────────────────────────────
HandlerFn = Callable[[str, dict, bytes], tuple[int, dict]]
# HandlerFn(method_path, query, body_bytes) -> (status, json_obj)


def make_handler(service_name: str,
                 routes: dict[tuple[str, str], HandlerFn]) -> type[BaseHTTPRequestHandler]:
    """routes: {(method, path_prefix): fn}。支持 GET 路径参数(前缀匹配)。"""

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:  # 静音
            pass

        def _dispatch(self, method: str) -> None:
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            path, query = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            # 精确匹配优先,再前缀匹配(GET /identity/{id})
            fn = routes.get((method, path))
            if fn is None:
                for (m, prefix), f in routes.items():
                    if m == method and path.startswith(prefix) and prefix != path:
                        fn = f
                        break
            if fn is None:
                self._send(404, {"ok": False, "error": "not found", "service": service_name})
                return
            try:
                status, obj = fn(path, query, body)
            except Exception as e:  # noqa: BLE001
                status, obj = 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self._send(status, obj)

        def _send(self, status: int, obj: dict) -> None:
            obj.setdefault("service", service_name)
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = lambda s: s._dispatch("GET")    # noqa: E731
        do_POST = lambda s: s._dispatch("POST")  # noqa: E731

    return _H


def serve(service_name: str, port: int,
          routes: dict[tuple[str, str], HandlerFn], host: str = "127.0.0.1") -> None:
    srv = ThreadingHTTPServer((host, port), make_handler(service_name, routes))
    print(f"[{service_name}] listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


def ok(obj: dict | None = None, status: int = 200) -> tuple[int, dict]:
    return status, {"ok": True, **(obj or {})}


def err(msg: str, status: int = 400) -> tuple[int, dict]:
    return status, {"ok": False, "error": msg}


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))
