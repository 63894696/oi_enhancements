"""test_policy_gate_simplex.py — 双保险 policy-gate 测试

覆盖本次三处改动:
  (a) policy_engine.policy_check_daemon: 信任扩展工具默认 ask,approved_rules 命中才 allow
  (b) simplex_tools: standalone 降级 fail-closed + accept_invitation 本体审批闸
  (c) simplex_auto_accept: 本地确认白名单(首见→需确认,confirm 后→接受)

隔离:用 OIAGENT_POLICY_DB 指到临时文件,不碰真实 policy_rules.db;
     测试间用 importlib.reload 重置模块级连接/白名单缓存。
跑法:`python -m unittest test_policy_gate_simplex.py`
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _fresh_policy_engine(db_path: str):
    """在隔离 DB 下重新加载 policy_engine,返回模块。"""
    os.environ["OIAGENT_POLICY_DB"] = db_path
    import policy_engine as pe
    importlib.reload(pe)  # 重读 _POLICY_DB + 重置 _policy_conn
    return pe


class TestPolicyCheckDaemonTrustExtending(unittest.TestCase):
    """(a) 信任扩展工具在 branch 3 内置放行之前被拦截。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "policy.db")
        self.pe = _fresh_policy_engine(self.db)

    def tearDown(self):
        # Windows 文件锁:先关闭打开的 sqlite 连接,再清理临时目录,否则
        # rmtree 报 WinError 32(文件被占用)。
        conn = getattr(self.pe, "_policy_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.pe._policy_conn = None
        self._tmp.cleanup()

    def test_accept_invitation_defaults_to_ask(self):
        """未批准时,accept_invitation 必须 ask(不再是内置放行)。"""
        verdict, reason = self.pe.policy_check_daemon(
            "simplex_accept_invitation", {"link": "simplex:/invitation#x"})
        self.assertEqual(verdict, "ask")
        self.assertIn("人工批准", reason)

    def test_delete_contact_defaults_to_ask(self):
        verdict, _ = self.pe.policy_check_daemon(
            "simplex_delete_contact", {"contact": "bob"})
        self.assertEqual(verdict, "ask")

    def test_approved_rule_allows(self):
        """人工批准(remember allow)后,同类指纹 → allow。"""
        args = {"link": "simplex:/invitation#x"}
        fp = self.pe.rule_fingerprint("simplex_accept_invitation", args)
        self.pe.policy_remember(fp, "simplex_accept_invitation", "allow")
        verdict, reason = self.pe.policy_check_daemon(
            "simplex_accept_invitation", args)
        self.assertEqual(verdict, "allow")
        self.assertIn("已批准", reason)

    def test_denied_rule_denies(self):
        args = {"contact": "bob"}
        fp = self.pe.rule_fingerprint("simplex_delete_contact", args)
        self.pe.policy_remember(fp, "simplex_delete_contact", "deny")
        verdict, _ = self.pe.policy_check_daemon("simplex_delete_contact", args)
        self.assertEqual(verdict, "deny")

    def test_readonly_still_builtin_allow(self):
        """只读工具不受影响,仍走内置放行。"""
        verdict, _ = self.pe.policy_check_daemon("read_file", {"path": "x"})
        self.assertEqual(verdict, "allow")

    def test_fingerprint_is_tool_level(self):
        """simplex 工具指纹为 '{tool}:default'(工具级,批准一次同类免重复)。"""
        fp = self.pe.rule_fingerprint("simplex_accept_invitation",
                                      {"link": "simplex:/invitation#anything"})
        self.assertEqual(fp, "simplex_accept_invitation:default")


class TestStandaloneFailClosed(unittest.TestCase):
    """(b1) policy_engine 缺席时,信任扩展工具降级 ask(不静默放行)。"""

    def _make_standalone(self):
        """模拟 policy_engine 不可 import 的 standalone 命名空间。"""
        code = (
            'try:\n'
            '    from policy_engine import policy_check_daemon\n'
            'except Exception:\n'
            '    _STANDALONE_ASK = {"simplex_accept_invitation", "simplex_delete_contact"}\n'
            '    def policy_check_daemon(tool, args):\n'
            '        if tool in _STANDALONE_ASK:\n'
            '            return ("ask", "policy_engine 不可用,信任扩展工具 fail-closed 待人工")\n'
            '        return ("allow", "policy_engine 不可用,standalone 放行")\n'
        )
        # 强制 import 失败,走 except 分支
        g = {"__name__": "_standalone_test"}
        with mock.patch.dict(sys.modules, {"policy_engine": None}):
            exec(compile(code, "<standalone>", "exec"), g)
        return g["policy_check_daemon"]

    def test_accept_invitation_ask_when_engine_missing(self):
        pcd = self._make_standalone()
        verdict, _ = pcd("simplex_accept_invitation", {"link": "x"})
        self.assertEqual(verdict, "ask")

    def test_delete_contact_ask_when_engine_missing(self):
        pcd = self._make_standalone()
        verdict, _ = pcd("simplex_delete_contact", {"contact": "bob"})
        self.assertEqual(verdict, "ask")

    def test_other_tools_allow_when_engine_missing(self):
        pcd = self._make_standalone()
        verdict, _ = pcd("simplex_send_message", {"contact": "b", "text": "hi"})
        self.assertEqual(verdict, "allow")


class TestAcceptInvitationGate(unittest.TestCase):
    """(b2) simplex_accept_invitation 本体审批闸。"""

    def setUp(self):
        import simplex_tools as st
        self.st = st

    def test_deny_blocks_before_runtime(self):
        """deny → 直接 _err,不触 runtime。"""
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("deny", "危险")), \
             mock.patch.object(self.st, "_runtime") as rt:
            r = self.st.simplex_accept_invitation("simplex:/invitation#x")
        self.assertFalse(r["ok"])
        self.assertIn("审批拒绝", r["error"])
        rt.assert_not_called()

    def test_ask_marks_approved_on_success(self):
        """ask(人工已批)→ 放行且结果标 approved=True。"""
        fake_rt = mock.Mock()
        fake_rt._thread.is_alive.return_value = True
        fake_rt.accept_invitation.return_value = {"display_name": "bob",
                                                  "contact_id": 7}
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("ask", "需确认")), \
             mock.patch.object(self.st, "_runtime", return_value=fake_rt):
            r = self.st.simplex_accept_invitation("simplex:/invitation#x")
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("approved"))

    def test_allow_not_marked_approved(self):
        fake_rt = mock.Mock()
        fake_rt._thread.is_alive.return_value = True
        fake_rt.accept_invitation.return_value = {"display_name": "bob",
                                                  "contact_id": 7}
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake_rt):
            r = self.st.simplex_accept_invitation("simplex:/invitation#x")
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("approved"))


class TestLocalWhitelist(unittest.TestCase):
    """(c) scan_and_accept 本地确认白名单(第二道保险)。"""

    def setUp(self):
        import simplex_auto_accept as saa
        importlib.reload(saa)  # 重置 _CONFIRMED_LINKS 缓存
        # 强制内存模式(忽略 env 文件持久化),保证测试隔离
        saa._WHITELIST_PATH = ""
        saa._CONFIRMED_LINKS = set()
        self.saa = saa

    def test_first_seen_link_needs_confirmation_even_if_policy_allows(self):
        """policy allow 但首见链接 → 拦下,needs_confirmation=True,不调 call_tool。"""
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.saa, "call_tool") as ct:
            r = self.saa.scan_and_accept("加 simplex:/invitation#xyz 我")
        self.assertTrue(r["found"])
        self.assertFalse(r["accepted"])
        self.assertTrue(r.get("needs_confirmation"))
        self.assertIn("首次见到", r["reason"])
        ct.assert_not_called()

    def test_confirmed_link_accepts_when_policy_allows(self):
        """人工 confirm 后 + policy allow → 接受。"""
        link = "simplex:/invitation#xyz"
        self.saa.confirm_invitation(link)
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.saa, "call_tool",
                               return_value={"ok": True, "diagnosable": "已联系"}) as ct:
            r = self.saa.scan_and_accept(f"加 {link} 我")
        self.assertTrue(r["accepted"])
        ct.assert_called_once_with("simplex_accept_invitation", {"link": link})

    def test_policy_ask_still_blocks_before_whitelist(self):
        """policy ask → 在 whitelist 闸之前就拦下(不查确认状态)。"""
        link = "simplex:/invitation#xyz"
        self.saa.confirm_invitation(link)  # 即便已确认
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("ask", "需确认")), \
             mock.patch.object(self.saa, "call_tool") as ct:
            r = self.saa.scan_and_accept(f"加 {link}")
        self.assertFalse(r["accepted"])
        self.assertIn("需人工批准", r["reason"])
        ct.assert_not_called()

    def test_confirm_invitation_idempotent(self):
        link = "simplex:/invitation#xyz"
        r1 = self.saa.confirm_invitation(link)
        r2 = self.saa.confirm_invitation(link)
        self.assertTrue(r1["ok"])
        self.assertFalse(r1["already"])
        self.assertTrue(r2["already"])
        self.assertTrue(self.saa.is_confirmed(link))

    def test_confirm_rejects_empty(self):
        r = self.saa.confirm_invitation("")
        self.assertFalse(r["ok"])

    def test_unconfirmed_not_in_set(self):
        self.assertFalse(self.saa.is_confirmed("simplex:/invitation#never"))


if __name__ == "__main__":
    unittest.main()
