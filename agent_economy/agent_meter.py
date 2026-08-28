"""agent_meter.py — meter 服务(:18903):SQLite 计量账本 + x402 语义配额拦截。

x402 对齐:配额耗尽返回 HTTP 402 + X-Payment-Required 头。内部用"配额"记账,
不接真实结算 — 未来换稳定币只改 settlement 层,账本与接口不变。

端点:
  GET  /meter/health
  POST /meter/grant    {agent_id, quota}                       → 充值配额
  POST /meter/charge   {agent_id, resource, units, meta?}      → {ok, balance} | 402
  GET  /meter/balance/{agent_id}                               → {ok, balance, used}
  GET  /meter/ledger?agent_id=                                 → {ok, entries}
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import PORT_METER, HOST, schemas  # noqa: E402
from agent_economy._base import AuditDB, data_path, err, ok, serve  # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS quota(
  agent_id TEXT PRIMARY KEY,
  quota    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  ts       REAL NOT NULL,
  resource TEXT NOT NULL,
  units    INTEGER NOT NULL,
  tokens   INTEGER NOT NULL DEFAULT 0,
  meta     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ledger_agent ON ledger(agent_id);
"""

_DB = AuditDB(data_path("meter.db"), _DDL)


def _balance(agent_id: str) -> tuple[int, int]:
    """返回 (剩余配额, 已用)。"""
    rows = _DB.query("SELECT quota FROM quota WHERE agent_id=?", (agent_id,))
    quota = rows[0][0] if rows else 0
    used_rows = _DB.query(
        "SELECT COALESCE(SUM(units),0) FROM ledger WHERE agent_id=?", (agent_id,))
    used = used_rows[0][0] if used_rows else 0
    return quota - used, used


def _h_grant(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    agent_id, quota = d.get("agent_id"), d.get("quota")
    if not agent_id or quota is None:
        return err("agent_id 与 quota 必填")
    _DB.execute(
        "INSERT INTO quota(agent_id,quota) VALUES(?,?) "
        "ON CONFLICT(agent_id) DO UPDATE SET quota=quota+excluded.quota",
        (agent_id, int(quota)))
    bal, used = _balance(agent_id)
    return ok({"agent_id": agent_id, "balance": bal, "used": used})


def _h_charge(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    agent_id, resource = d.get("agent_id"), d.get("resource")
    units = int(d.get("units", 1))
    if not agent_id or not resource:
        return err("agent_id 与 resource 必填")
    meta = d.get("meta", {})
    tokens = int(meta.get("tokens", 0))
    bal, _ = _balance(agent_id)
    if bal < units:
        # x402:配额耗尽 → 402 + X-Payment-Required 语义(在 body 标注,头由 HTTP 层加)
        return 402, {
            "ok": False,
            "error": "quota exhausted",
            "x402": {"header": schemas.X402_HEADER, "balance": bal,
                     "required": units, "resource": resource},
        }
    _DB.execute(
        "INSERT INTO ledger(agent_id,ts,resource,units,tokens,meta) VALUES(?,?,?,?,?,?)",
        (agent_id, time.time(), resource, units, tokens, json.dumps(meta, ensure_ascii=False)))
    new_bal, used = _balance(agent_id)
    return ok({"agent_id": agent_id, "charged": units,
               "balance": new_bal, "used": used})


def _h_balance(path: str, query: dict, body: bytes):
    agent_id = path[len("/meter/balance/"):]
    bal, used = _balance(agent_id)
    return ok({"agent_id": agent_id, "balance": bal, "used": used})


def _h_ledger(path: str, query: dict, body: bytes):
    agent_id = query.get("agent_id")
    sql = ("SELECT id,agent_id,ts,resource,units,tokens,meta FROM ledger "
           + ("WHERE agent_id=? " if agent_id else "") + "ORDER BY id DESC LIMIT 200")
    rows = _DB.query(sql, (agent_id,) if agent_id else ())
    entries = [{"id": r[0], "agent_id": r[1], "ts": r[2], "resource": r[3],
                "units": r[4], "tokens": r[5], "meta": json.loads(r[6])} for r in rows]
    return ok({"entries": entries, "count": len(entries)})


ROUTES = {
    ("GET", "/meter/health"): lambda p, q, b: ok({"status": "up"}),
    ("POST", "/meter/grant"): _h_grant,
    ("POST", "/meter/charge"): _h_charge,
    ("GET", "/meter/ledger"): _h_ledger,
    ("GET", "/meter/balance/"): _h_balance,
}


if __name__ == "__main__":
    serve("meter", PORT_METER, ROUTES, HOST)
