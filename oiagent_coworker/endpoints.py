"""endpoint 白名单注册表(红线③)。

coworker 只暴露这里列名的端点;其余一律 404。
不开放任意 shell、不开放 Electrum 全量 RPC——只有经这里登记、
且标了风险级别的方法才可达。

每个 entry:
  method  : HTTP 方法("GET" / "POST")
  risk    : "L0" 只读免确认 / "L1" 内嵌卡 / "L2" 全回显 / "L3" 安全对话框(口令)
            —— 确认门槛在扩展侧;此处标注供扩展渲染对应确认卡,coworker 不替用户决定。
  auth    : 是否需要 X-OI-Token(默认全部需要;只有 /health 探活可免)
  handler : 处理函数,签名 handler(body: dict) -> (payload: dict, http_status: int)
"""
from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[dict], tuple[dict, int]]

_REGISTRY: dict[str, dict[str, Any]] = {}


def register(path: str, *, method: str = "GET", risk: str = "L0",
             auth: bool = True) -> Callable[[Handler], Handler]:
    """装饰器:把一个处理函数登记进白名单。"""
    def deco(fn: Handler) -> Handler:
        _REGISTRY[path] = {"method": method.upper(), "risk": risk, "auth": auth, "handler": fn}
        return fn
    return deco


def lookup(path: str) -> dict[str, Any] | None:
    return _REGISTRY.get(path)


def is_whitelisted(path: str, method: str) -> bool:
    e = _REGISTRY.get(path)
    return bool(e and e["method"] == method.upper())


def catalog() -> list[dict[str, str]]:
    """列出白名单(供 /health 与调试;不含 handler 本体)。"""
    return [
        {"path": p, "method": e["method"], "risk": e["risk"], "auth": e["auth"]}
        for p, e in sorted(_REGISTRY.items())
    ]
