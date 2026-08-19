"""白名单端点登记 + 能力注册(F1 能力门面)。

端点层(endpoints):路径白名单,未登记一律 404(红线③)。
能力层(capability):统一能力注册表 + search/execute 两入口(F1)。
  - 能力 = 可被 agent 发现/调用的最小单元,绑定一个白名单端点。
  - execute_capability 不绕过白名单:校验能力存在后,路由回其 endpoint handler 执行。
"""
from __future__ import annotations

from . import __version__, capability, endpoints, plugins, team, wallet


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


@endpoints.register("/wallet/receive", method="POST", risk="L0", auth=True)
def _wallet_receive(body: dict):
    """生成收款地址(只读向,免确认)。"""
    body = body if isinstance(body, dict) else {}
    return wallet.receive(memo=body.get("memo", ""), amount=body.get("amount")), 200


@endpoints.register("/wallet/history", method="GET", risk="L0", auth=True)
def _wallet_history(_body: dict):
    """到账查账(只读,入金引导用)。"""
    return wallet.history(), 200


@endpoints.register("/wallet/payto", method="POST", risk="L3", auth=True)
def _wallet_payto(body: dict):
    """L3 付款。两阶段:confirm=False 构造 unsigned 待确认;confirm=True+txid+口令 签名广播。

    确认门槛在扩展/shell 侧(渲染确认卡/A2H);PrisirWork 不替用户做决定。
    testnet/regtest only,mainnet 由 wallet.payto 拒绝。
    """
    body = body if isinstance(body, dict) else {}
    return wallet.payto(
        address=body.get("address", ""), amount=body.get("amount", 0),
        memo=body.get("memo", ""), passphrase=body.get("passphrase", ""),
        confirm=bool(body.get("confirm")), txid=body.get("txid", ""),
    ), 200


# ---------------------------------------------------------------------------
# F3 oiagent 团队协作:派单(L1)+ 查状态(L0),路由到根目录 task_queue
# ---------------------------------------------------------------------------

@endpoints.register("/team/submit", method="POST", risk="L1", auth=True)
def _team_submit(body: dict):
    """派单进 oiagent 队列。content 写「改动文件: ...」+ namespace=tasks-code 走主会话认领。"""
    body = body if isinstance(body, dict) else {}
    return team.submit(
        title=body.get("title", ""), content=body.get("content", ""),
        priority=body.get("priority", 0), depends_on=body.get("depends_on"),
        namespace=body.get("namespace", "tasks"),
    ), 200


@endpoints.register("/team/list", method="POST", risk="L0", auth=True)
def _team_list(body: dict):
    """查状态:ready(可调度)/blocked(待决策)/其他 status。只读。"""
    body = body if isinstance(body, dict) else {}
    return team.list_tasks(
        status=body.get("status", "ready"), limit=body.get("limit", 10),
        namespace=body.get("namespace", "tasks"),
    ), 200


# ---------------------------------------------------------------------------
# F6 plugin 能力包:加载声明式能力包进门面
# ---------------------------------------------------------------------------

@endpoints.register("/plugins/load", method="POST", risk="L1", auth=True)
def _plugins_load(body: dict):
    """加载能力包:dir 指单包(含 plugin.json);root 指扫描目录下所有包。二选一。"""
    body = body if isinstance(body, dict) else {}
    if body.get("dir"):
        return plugins.load_plugin(body["dir"]), 200
    if body.get("root"):
        return plugins.load_plugins_dir(body["root"]), 200
    return {"ok": False, "error": "dir_or_root_required"}, 400


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
capability.register_capability(
    "wallet.receive", title="生成收款地址(给别人打钱进来用)",
    endpoint="/wallet/receive", method="POST", risk="L0", auth=True,
    keywords=("钱包", "收款", "地址", "入金", "wallet", "receive", "address"),
)
capability.register_capability(
    "wallet.history", title="到账查账(最近交易概要,地址脱敏)",
    endpoint="/wallet/history", method="GET", risk="L0", auth=True,
    keywords=("钱包", "查账", "到账", "交易", "history", "transactions"),
)
capability.register_capability(
    "wallet.payto", title="付款(两阶段:先构造 unsigned 待确认,确认+口令才签名广播)",
    endpoint="/wallet/payto", method="POST", risk="L3", auth=True,
    keywords=("钱包", "付款", "转账", "支付", "wallet", "pay", "send", "transfer"),
    confirm=("L3 付款:agent 先构造未签名交易给你看(收款方/金额/备注),"
             "你确认并输入钱包口令后才签名广播。授权权永远在你;testnet/regtest only。"),
)
capability.register_capability(
    "team.submit", title="派单进 oiagent 协作队列(改代码走主会话认领/文本走 consumer)",
    endpoint="/team/submit", method="POST", risk="L1", auth=True,
    keywords=("派单", "任务", "协作", "团队", "team", "task", "dispatch", "submit"),
    confirm=("L1 派单:把任务提交进 oiagent 队列等待认领执行。"
             "content 写「改动文件: ...」+ namespace=tasks-code 走主会话认领落盘;"
             "纯文本派 tasks 走 consumer。执行结果可经 team.list 查。"),
)
capability.register_capability(
    "team.list", title="查 oiagent 协作队列状态(ready 可调度 / blocked 待决策)",
    endpoint="/team/list", method="POST", risk="L0", auth=True,
    keywords=("队列", "状态", "任务", "进度", "team", "queue", "status", "list"),
)
capability.register_capability(
    "plugins.load", title="加载能力包(声明式 plugin.json → 注册进门面,不执行任意代码)",
    endpoint="/plugins/load", method="POST", risk="L1", auth=True,
    keywords=("插件", "能力包", "加载", "扩展", "plugin", "pack", "load", "import"),
    confirm=("L1 加载能力包:解析 plugin.json 声明,把能力注册进门面。"
             "只注册已绑白名单端点的能力,不 import/执行任意代码。"),
)
