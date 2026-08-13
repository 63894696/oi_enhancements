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
