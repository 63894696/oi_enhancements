"""审计日志(F5):敏感操作留痕,口令/token 绝不落日志。

红线:
- 只记「谁调了哪个端点、什么风险级、结果成败」,不记 body 细节(防口令/地址落盘)。
- L3/L2 等高风险必记;L1 记摘要;L0 只读不记(量大且无敏感性)。
- 口令、token、私钥、地址全文永不进审计——只记风险级与成败,不含 payload。
- 文件尽量收紧 0600(Windows 尽力而为),仅当前用户可读。

审计文件:~/.oi/prisir_audit.jsonl(可用 PRISIR_WORK_AUDIT 覆盖,便于测试)。
每行一个 JSON 事件:{ts, endpoint, method, risk, ok, status}。不含 body/口令/token。
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

# 记审计的最低风险级:L0 只读不记,L1/L2/L3 记。
_RISK_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_MIN_AUDIT = 1  # >= L1 记审计


def audit_path() -> Path:
    env = os.environ.get("PRISIR_WORK_AUDIT")
    if env:
        return Path(env)
    oi_home = Path(os.environ.get("OI_HOME", Path.home() / ".oi"))
    return oi_home / "prisir_audit.jsonl"


def _restrict_perms(p: Path) -> None:
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows 尽力而为


def should_audit(risk: str) -> bool:
    return _RISK_ORDER.get(risk, 0) >= _MIN_AUDIT


def record(endpoint: str, method: str, risk: str, ok: bool, status: int) -> None:
    """追加一条审计事件。永不抛异常(审计失败不拖垮业务)。

    只记元信息,不记 body/口令/token/地址全文。
    """
    if not should_audit(risk):
        return
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        evt = {
            "ts": round(time.time(), 3),
            "endpoint": endpoint,
            "method": method,
            "risk": risk,
            "ok": bool(ok),
            "status": int(status),
        }
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        _restrict_perms(p)
    except OSError:
        pass  # 审计失败不阻断业务;但绝不因审计泄露敏感
