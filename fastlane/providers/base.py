"""FastLane provider 公共设施:环境变量 key / TLS1.3 HTTP 客户端"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from ..adapters import make_ssl_context


class ProviderNotConfigured(Exception):
    """缺 API key / 必要配置 —— 工厂捕获后把该 provider 排除出降级链"""


def require_env(*names: str) -> str:
    """按顺序找第一个非空环境变量;全空抛 ProviderNotConfigured"""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    raise ProviderNotConfigured(f"缺环境变量:{' / '.join(names)}")


def optional_env(*names: str) -> Optional[str]:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return None


def tls13_client(timeout_s: float = 30.0) -> httpx.AsyncClient:
    """F-6:TLS 1.3 最低版本 + 证书校验的异步 HTTP 客户端"""
    return httpx.AsyncClient(verify=make_ssl_context(), timeout=timeout_s)
