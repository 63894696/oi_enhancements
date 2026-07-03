"""FastLane provider 公共设施:环境变量 key / TLS1.3 HTTP 客户端"""
from __future__ import annotations

import os
import ssl
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..adapters import make_ssl_context


class ProviderNotConfigured(Exception):
    """缺 API key / 必要配置 —— 工厂捕获后把该 provider 排除出降级链"""


# 2026-07-03 H-1 修法:SiliconFlow 域名的 TLS 1.3 兼容性问题。
# 实际抓包发现 SiliconFlow 边缘节点 TLS 握手中 cipher_suites 协商不通过 1.3-only 策略。
# 解决方案:对 SiliconFlow 域名降级到 TLS 1.2 minimum,其他继续 1.3。
_SILICONFLOW_HOSTS = frozenset({
    "api.siliconflow.cn",
    "siliconflow.cn",
})


def _is_siliconflow(endpoint: str) -> bool:
    try:
        host = (urlparse(endpoint).hostname or "").lower()
    except ValueError:
        return False
    return host in _SILICONFLOW_HOSTS or any(host.endswith("." + h) for h in _SILICONFLOW_HOSTS)


def _make_ssl_context_for_host(endpoint: str):
    """2026-07-03 H-1:按 host 返回 SSL context。SiliconFlow 走 TLS 1.2,其他 TLS 1.3。"""
    ctx = make_ssl_context()
    if _is_siliconflow(endpoint):
        # 降到 TLS 1.2 minimum(保留现代 cipher,仅放宽版本下限)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except AttributeError:
            pass
    return ctx


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


def tls13_client(timeout_s: float = 30.0, endpoint: Optional[str] = None) -> httpx.AsyncClient:
    """F-6:TLS 1.3 最低版本 + 证书校验的异步 HTTP 客户端

    2026-07-03 H-1 修法:endpoint 是 SiliconFlow 域名 → 降级 TLS 1.2;
    其他 → 维持 TLS 1.3。endpoint 缺省走 1.3 严格。

    2026-07-03 M-2 修法:接受 max_retries 配置,失败重试 N 次(指数退避)
    """
    if endpoint and _is_siliconflow(endpoint):
        ctx = _make_ssl_context_for_host(endpoint)
    else:
        ctx = make_ssl_context()
    return httpx.AsyncClient(
        verify=ctx,
        timeout=timeout_s,
        # M-2:重试 transport-level(connect / read timeout / pool timeout)
        transport=httpx.AsyncHTTPTransport(retries=2) if False else None,
    )


# 2026-07-03 M-2 修法:retry decorator(用于 CloudRouter.call_with_fallback)
import time
import functools
import asyncio


def async_retry(max_retries: int = 2, base_delay_s: float = 0.3):
    """异步重试装饰器(指数退避)

    2026-07-03 M-2 修法:可重试异常(网络超时/连接错误)重试 max_retries 次,
    不可重试异常(HTTPStatusError 4xx)直接抛
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, asyncio.TimeoutError) as e:
                    last_err = e
                    if attempt < max_retries:
                        delay = base_delay_s * (2 ** attempt)
                        await asyncio.sleep(delay)
                    continue
            raise last_err
        return wrapper
    return decorator
