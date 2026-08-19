"""白名单端点登记 + 能力注册(F1 能力门面)。

端点层(endpoints):路径白名单,未登记一律 404(红线③)。
能力层(capability):统一能力注册表 + search/execute 两入口(F1)。
  - 能力 = 可被 agent 发现/调用的最小单元,绑定一个白名单端点。
  - execute_capability 不绕过白名单:校验能力存在后,路由回其 endpoint handler 执行。
"""
from __future__ import annotations

from . import __version__, capability, endpoints, wallet


@endpoints.register("/health", method="GET", risk="L0", auth=False)
def _health(_body: dict):
    """探活(免 token):只报进程活着 + 版本 + 白名单目录 + 能力目录,不泄露任何敏感信息。"""
    return {"ok": True, "service": "prisir-work", "version": __version__,
            "endpoints": endpoints.catalog(),
            "capabilities": capability.list_capabilities()}, 200


@endpoints.register("/wallet/status", method="GET", risk="L0", auth=True)
def _wallet_status(_body: dict):
    """CW-1 只读:连得上 Electrum 报真实状态,连不上如实报 unavailable。"""
    return wallet.status(), 200


# ---------------------------------------------------------------------------
# F1 能力门面:search_capabilities / execute_capability 两个统一入口
# ---------------------------------------------------------------------------

@endpoints.register("/cap/search", method="POST", risk="L0", auth=True)
def _cap_search(body: dict):
    """能力发现:按 query 匹配 id/title/keywords,返回可调用能力清单(精简)。

    请求:{"query": "钱包" | "wallet" | ""}  空 query 返回全部。
    """
    query = body.get("query", "") if isinstance(body, dict) else ""
    return {"ok": True, "capabilities": capability.search(query)}, 200


@endpoints.register("/cap/execute", method="POST", risk="L1", auth=True)
def _cap_execute(body: dict):
    """能力执行:按 id 查能力 → 校验 → 路由回其 endpoint handler 执行(不绕白名单)。

    请求:{"id": "wallet.status", "args": {...}}。
    能力的 risk/auth 仅作标注与确认卡提示;真正执行仍过 endpoints 白名单 + 该方法语义。
    """
    if not isinstance(body, dict):
        return {"ok": False, "error": "bad_json"}, 400
    cid = body.get("id", "")
    args = body.get("args", {}) or {}
    cap = capability.get(cid)
    if not cap:
        return {"ok": False, "error": "unknown_capability", "id": cid}, 404
    # 路由回端点白名单:能力必须绑一个已注册端点,否则执行失败(不放行未登记路径)。
    entry = endpoints.lookup(cap["endpoint"])
    if not entry or entry["method"] != cap["method"]:
        return {"ok": False, "error": "capability_endpoint_not_registered",
                "id": cid, "endpoint": cap["endpoint"]}, 404
    try:
        payload, status = entry["handler"](args)
    except Exception as e:  # 兜底:与 server 一致,单能力异常不拖垮进程
        return {"ok": False, "error": "internal", "detail": type(e).__name__}, 500
    # 包一层能力元信息,便于调用方知道执行的是哪个能力、什么风险级。
    return {"ok": payload.get("ok", True), "capability": cid, "risk": cap["risk"],
            "result": payload}, status


# ---------------------------------------------------------------------------
# 能力注册(绑定到上面的白名单端点)
# ---------------------------------------------------------------------------

capability.register_capability(
    "system.health", title="探活:进程是否在线 + 版本 + 能力目录",
    endpoint="/health", method="GET", risk="L0", auth=False,
    keywords=("健康", "探活", "health", "存活", "版本", "ping"),
)
capability.register_capability(
    "wallet.status", title="查询钱包状态(余额/地址脱敏/解锁态)",
    endpoint="/wallet/status", method="GET", risk="L0", auth=True,
    keywords=("钱包", "余额", "地址", "wallet", "balance", "status"),
)
