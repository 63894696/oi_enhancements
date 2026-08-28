"""tests/test_meter.py — meter 服务单测(直接测处理函数)。

覆盖:充值 / 记账扣减 / 余额 / 耗尽返 402 / 流水。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["AGENT_ECONOMY_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import agent_meter as M  # noqa: E402


def charge(aid, resource, units=1, tokens=0):
    return M._h_charge("/meter/charge", {}, json.dumps(
        {"agent_id": aid, "resource": resource, "units": units,
         "meta": {"tokens": tokens}}).encode())


class TestMeter(unittest.TestCase):
    def setUp(self):
        M._DB.execute("DELETE FROM ledger")
        M._DB.execute("DELETE FROM quota")

    def test_grant_and_balance(self):
        M._h_grant("/meter/grant", {}, json.dumps(
            {"agent_id": "a", "quota": 100}).encode())
        status, obj = M._h_balance("/meter/balance/a", {}, b"")
        self.assertEqual((status, obj["balance"]), (200, 100))

    def test_charge_deducts(self):
        M._h_grant("/meter/grant", {}, json.dumps({"agent_id": "a", "quota": 10}).encode())
        status, obj = charge("a", "tool:bash", units=3, tokens=50)
        self.assertEqual((status, obj["balance"]), (200, 7))
        _, bal = M._h_balance("/meter/balance/a", {}, b"")
        self.assertEqual(bal["used"], 3)

    def test_exhausted_returns_402(self):
        M._h_grant("/meter/grant", {}, json.dumps({"agent_id": "a", "quota": 2}).encode())
        charge("a", "tool:bash", units=2)
        status, obj = charge("a", "tool:bash", units=1)
        self.assertEqual(status, 402)
        self.assertIn("x402", obj)

    def test_ledger_audit(self):
        M._h_grant("/meter/grant", {}, json.dumps({"agent_id": "a", "quota": 5}).encode())
        charge("a", "tool:bash", units=1, tokens=10)
        charge("a", "llm:dashscope", units=1, tokens=200)
        _, obj = M._h_ledger("/meter/ledger", {"agent_id": "a"}, b"")
        self.assertEqual(obj["count"], 2)
        self.assertEqual(obj["entries"][0]["resource"], "llm:dashscope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
