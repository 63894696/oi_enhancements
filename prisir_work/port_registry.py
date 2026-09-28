"""prisir_work/port_registry.py — 子模块端口共享读取(P3j T14)。

PrisirWork 主服务(默认 18826)要代理到 companion 子服务(wechat-publisher)时,
需要先知道那个服务的端口。端口注册位:
    1) HKCU\\Software\\PrisirAI\\<name> REG_DWORD  (Windows)
    2) companion/_prisir_registry/<name>.json    (跨平台)

暴露:
    read_port(name) → int | None
    base_url(name) → str | None    # "http://127.0.0.1:<port>"
    proxy_post(name, path, body, timeout) → dict    # 走 aiohttp post → 返响应 JSON
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["read_port", "base_url", "proxy_post", "proxy_get"]


# 模块根(可被 monkeypatch 测试)
_REPO = Path(__file__).resolve().parent.parent


def _registry_dir() -> Path:
    return _REPO / "companion" / "_prisir_registry"


def read_port(name: str) -> int | None:
    """读 <name> 注册端口(HKCU first, fallback to json)。"""
    # 1) HKCU(Windows)
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\PrisirAI") as k:
                v, _ = winreg.QueryValueEx(k, name)
                port = int(v)
                if port:
                    return port
        except (OSError, FileNotFoundError, ValueError):
            pass
    # 2) JSON 兜底
    try:
        p = _registry_dir() / f"{name}.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            port = int(d.get("port") or 0)
            if port:
                return port
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def base_url(name: str) -> str | None:
    p = read_port(name)
    return f"http://127.0.0.1:{p}" if p else None


# 简单 HTTP 调用(不依赖 aiohttp client 风格 — urllib 即可,跟测试一致)
import urllib.error
import urllib.request


def _request(method: str, name: str, path: str,
             body: dict | None = None, timeout: float = 30.0) -> dict:
    """内部 helper:GET/POST 到 <name>:<path>。失败/未注册 → 返 dict 不抛."""
    base = base_url(name)
    if not base:
        return {"ok": False, "error": f"{name}_not_registered"}
    url = base + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read().decode("utf-8") or "{}"
            return json.loads(payload)
    except urllib.error.HTTPError as e:
        # 4xx/5xx 也读 body — 后端通常返 ok=false 在 200 之外
        try:
            payload = e.read().decode("utf-8") or "{}"
            return json.loads(payload)
        except Exception:
            return {"ok": False, "error": f"http_{e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_error:{e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def proxy_get(name: str, path: str, *, timeout: float = 30.0) -> dict:
    return _request("GET", name, path, body=None, timeout=timeout)


def proxy_post(name: str, path: str, body: dict,
               *, timeout: float = 30.0) -> dict:
    return _request("POST", name, path, body=body, timeout=timeout)