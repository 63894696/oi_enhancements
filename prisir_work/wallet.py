"""wallet 子系统:托管 Electrum daemon(首个「运营方 cli」)。

CW-1(P0)只做骨架:`/wallet/status` 只读打通——连得上 Electrum daemon 就报
真实状态,连不上就如实报 daemon_unavailable(反 flattery:不伪造余额/地址)。
真正 receive/payto/history 属 CW-2(P6),且严禁主网真钱(testnet/regtest only)。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

# Electrum daemon JSON-RPC(默认 127.0.0.1:7777,rpcuser/rpcpassword basic auth)。
# 由整合包安装时配置;CW-1 不强行起 daemon,只「连得上就用、连不上如实报」。
DAEMON_URL = os.environ.get("OI_ELECTRUM_RPC", "http://127.0.0.1:7777")
RPC_USER = os.environ.get("OI_ELECTRUM_RPCUSER", "user")
RPC_PASS = os.environ.get("OI_ELECTRUM_RPCPASSWORD", "")  # 空 → 视为未配置,直接报不可用


def _rpc(method: str, params: dict | None = None, timeout: float = 2.0) -> dict:
    """调 Electrum daemon JSON-RPC。失败抛异常,由调用方兜底成 daemon_unavailable。"""
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(DAEMON_URL, data=body, headers={"Content-Type": "application/json"})
    cred = base64.b64encode(f"{RPC_USER}:{RPC_PASS}".encode()).decode()
    req.add_header("Authorization", "Basic " + cred)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode())
    if "error" in out and out["error"]:
        raise RuntimeError(str(out["error"]))
    return out.get("result", {})


def daemon_available() -> bool:
    """探测 daemon 是否可用(配置了口令且 RPC 应答)。"""
    if not RPC_PASS:
        return False
    try:
        _rpc("version")
        return True
    except Exception:
        return False


def status() -> dict:
    """CW-1 只读状态。连不上就如实报,不编造。

    返回字段:
      ok            端点本身可达(恒 True,能被调到就是 ok)
      daemon        "unavailable" | "ok"
      reason        不可用时给原因(未配置口令 / 连接失败)
      以下仅 daemon=ok 时给,且地址脱敏:
      unlocked      钱包是否解锁
      balance       getbalance(confirmed/unconfirmed)
      addresses     listaddresses(脱敏:只给前 6…后 4,不完整暴露)
    """
    if not daemon_available():
        reason = "未配置 Electrum RPC 口令" if not RPC_PASS else "Electrum daemon 连接失败或未启动"
        return {"ok": True, "daemon": "unavailable", "reason": reason,
                "note": "CW-1 骨架:仅打通通道;余额/地址待 CW-2,且仅 testnet"}
    bal = _rpc("getbalance")
    addrs = _rpc("listaddresses") or []

    def mask(a: str) -> str:
        a = str(a)
        return a[:6] + "…" + a[-4:] if len(a) > 12 else "（短地址不脱敏风险,略)"
    return {
        "ok": True, "daemon": "ok",
        "unlocked": True,  # CW-1 简化;CW-2 细分 locked/unlocked
        "balance": {"confirmed": bal.get("confirmed"), "unconfirmed": bal.get("unconfirmed")},
        "addresses": [mask(a) for a in addrs[:8]],
        "network": os.environ.get("OI_ELECTRUM_NET", "testnet"),  # 红线:CW-2 前只认 testnet
    }


def _mask(a: str) -> str:
    a = str(a)
    return a[:6] + "…" + a[-4:] if len(a) > 12 else a


def receive(memo: str = "", amount: float | None = None) -> dict:
    """生成收款地址(只读向,免确认;不产生资金流出)。

    CW-2:连得上 daemon 调 add_request 生成收款请求;连不上如实报 unavailable。
    """
    if not daemon_available():
        return {"ok": True, "daemon": "unavailable",
                "reason": "Electrum daemon 未配置或未启动", "note": "receive 需 CW-2 daemon"}
    req = _rpc("add_request", {"amount": amount, "memo": memo, "force": True})
    addr = req.get("address", "")
    return {"ok": True, "daemon": "ok", "address": _mask(addr),
            "address_full": addr,  # 收款地址本就要给对方,不算敏感泄露
            "memo": memo, "amount": amount,
            "network": os.environ.get("OI_ELECTRUM_NET", "testnet")}


def history() -> dict:
    """到账查账(只读,入金引导用)。"""
    if not daemon_available():
        return {"ok": True, "daemon": "unavailable",
                "reason": "Electrum daemon 未配置或未启动"}
    txs = _rpc("history") or []
    # 只回概要,地址脱敏;完整明细属隐私,调用方要再看可自行经 daemon。
    out = []
    for t in txs[:20]:
        out.append({
            "txid": str(t.get("txid", ""))[:12] + "…",
            "amount": t.get("amount"),
            "confirmations": t.get("confirmations"),
            "date": t.get("date"),
        })
    return {"ok": True, "daemon": "ok", "count": len(txs), "recent": out,
            "network": os.environ.get("OI_ELECTRUM_NET", "testnet")}


# 红线:L3 付款必须先构造 unsigned 返回确认,confirm:true 才真正签名广播。
# confirm 门槛在扩展/shell 侧渲染(钱包授权稿 §4);PrisirWork 不替用户做决定。
_UNSIGNED: dict[str, dict] = {}  # txid_placeholder → unsigned 详情(内存,不落盘)


def payto(address: str, amount: float, memo: str = "",
          passphrase: str = "", confirm: bool = False, txid: str = "") -> dict:
    """L3 付款。两阶段:
      confirm=False → 只构造 unsigned tx 返回给人审(不签名不广播),回 txid。
      confirm=True + txid + passphrase → 取回该笔 unsigned 签名广播(口令即用即弃)。

    红线:testnet/regtest only(网络由 OI_ELECTRUM_NET 控制,mainnet 直接拒)。
    """
    net = os.environ.get("OI_ELECTRUM_NET", "testnet")
    if net not in ("testnet", "regtest"):
        return {"ok": False, "error": "mainnet_forbidden",
                "note": "红线:CW-2 前严禁主网真钱;OI_ELECTRUM_NET 须为 testnet/regtest"}
    if not daemon_available():
        return {"ok": True, "daemon": "unavailable",
                "reason": "Electrum daemon 未配置或未启动"}
    if not confirm:
        # 阶段一:构造 unsigned,返回给调用方渲染确认卡。不签名不广播。
        unsigned = _rpc("payto", {"destination": address, "amount": amount,
                                  "memo": memo, "unsigned": True})
        txid = str(unsigned.get("txid", "")) or f"u{len(_UNSIGNED)}"
        _UNSIGNED[txid] = {"address": address, "amount": amount, "memo": memo,
                           "hex": unsigned.get("hex")}
        return {"ok": True, "stage": "unsigned", "txid": txid, "daemon": "ok",
                "to": _mask(address), "amount": amount, "memo": memo, "network": net,
                "confirm_required": True,
                "note": "unsigned 已构造;确认后带 confirm:true + passphrase + txid 才签名广播"}
    # 阶段二:确认 + 口令 + txid → 取回该笔 unsigned 签名广播。口令即用即弃,不落盘。
    if not passphrase:
        return {"ok": False, "error": "passphrase_required"}
    pending = _UNSIGNED.pop(str(txid or ""), None)  # 取一次性,签名后即弃
    if not pending or not pending.get("hex"):
        return {"ok": False, "error": "no_pending_unsigned",
                "note": "先以 confirm:false 构造 unsigned,再带其 txid 确认"}
    signed = _rpc("signtransaction", {"tx": pending["hex"], "password": passphrase})
    _rpc("broadcast", {"tx": signed.get("hex")})
    return {"ok": True, "stage": "broadcast", "daemon": "ok", "txid": signed.get("txid", txid),
            "to": _mask(pending["address"]), "amount": pending["amount"], "network": net}
