"""run_python.py — 在 wasmtime(Python)运行时加载同一份 wasm_meter.wasm。

验证 L2 统一运行时的核心命题:同一字节码,跨运行时(Node↔Python)结果一致。
capability 注入与 Node 侧完全同构:host 显式提供 host_audit 导入。
"""
import json
import sys
from pathlib import Path

from wasmtime import Engine, Linker, Module, Store, FuncType, ValType, Func

HERE = Path(__file__).resolve().parent
WASM = HERE / "target" / "wasm32-unknown-unknown" / "release" / "wasm_meter.wasm"

audit_log = []
OPS = ["open", "grant", "charge", "charge_rejected"]


def host_audit(op: int, amount: int) -> None:
    audit_log.append({"op": OPS[op] if op < len(OPS) else f"op{op}", "amount": amount})


def main() -> int:
    engine = Engine()
    store = Store(engine)
    module = Module.from_file(engine, str(WASM))

    # capability 注入:host 把 host_audit 这个能力提供给 wasm
    linker = Linker(engine)
    ft = FuncType([ValType.i32(), ValType.i64()], [])
    linker.define(store, "env", "host_audit", Func(store, ft, host_audit))

    inst = linker.instantiate(store, module)
    ex = inst.exports(store)

    m_open = ex["meter_open"]
    m_grant = ex["meter_grant"]
    m_charge = ex["meter_charge"]
    m_balance = ex["meter_balance"]
    m_count = ex["meter_count"]

    results = []
    acct = m_open(store, 3)
    results.append(("open(3) → acct", acct))
    results.append(("balance", m_balance(store, acct)))
    results.append(("grant(+5)", m_grant(store, acct, 5)))    # 8
    results.append(("charge(2)", m_charge(store, acct, 2)))   # 6
    results.append(("charge(2)", m_charge(store, acct, 2)))   # 4
    results.append(("charge(2)", m_charge(store, acct, 2)))   # 2
    results.append(("charge(5) 超额拒付", m_charge(store, acct, 5)))  # -2
    results.append(("balance(终)", m_balance(store, acct)))
    results.append(("count", m_count(store)))

    expected = [0, 3, 8, 6, 4, 2, -2, 2, 1]
    actual = [v for _, v in results]
    pypass = actual == expected

    print("=== wasm_meter @ Python(wasmtime) ===")
    for label, v in results:
        print(f"  {label:<18} = {v}")
    print("audit trail:", json.dumps(audit_log, ensure_ascii=False))
    print("expected:", expected)
    print("actual  :", actual)
    print("PY_RESULT:", "PASS" if pypass else "FAIL")

    (HERE / "_result_python.json").write_text(
        json.dumps({"actual": actual, "auditLog": audit_log}))
    return 0 if pypass else 1


if __name__ == "__main__":
    sys.exit(main())
