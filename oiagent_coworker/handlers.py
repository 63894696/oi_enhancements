"""白名单端点登记:CW-1(P0)只开放 /health + /wallet/status。

加新端点 = 在这里 register 一条。路径不在注册表 → 404(红线③)。
"""
from __future__ import annotations

from . import __version__, endpoints, wallet


@endpoints.register("/health", method="GET", risk="L0", auth=False)
def _health(_body: dict):
    """探活(免 token):只报进程活着 + 版本 + 白名单目录,不泄露任何敏感信息。"""
    return {"ok": True, "service": "oiagent-coworker", "version": __version__,
            "endpoints": endpoints.catalog()}, 200


@endpoints.register("/wallet/status", method="GET", risk="L0", auth=True)
def _wallet_status(_body: dict):
    """CW-1 只读:连得上 Electrum 报真实状态,连不上如实报 unavailable。"""
    return wallet.status(), 200
