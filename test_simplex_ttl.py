"""test_simplex_ttl.py — 阅后即焚 ttl 工具面测试

不连真 SimpleX 服务器:mock _runtime + policy_check_daemon,断言审批闸 +
联系人解析 + ttl 参数边界 + 命令拼接(发送端字符串,本地可验)。

跑法:`python -m unittest test_simplex_ttl.py`
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class TestSendMessageTtlGate(unittest.TestCase):
    """simplex_send_message_ttl 的审批闸 + 边界。"""

    def setUp(self):
        import simplex_tools as st
        self.st = st

    def _fake_rt(self, active=True):
        rt = mock.Mock()
        rt._thread.is_alive.return_value = True
        rt.resolve_contact.return_value = (
            {"contact_id": 7, "display_name": "bob", "active": active} if active is not None else None
        )
        rt.send_message_ttl.return_value = {
            "sent_items": 1, "contact_id": 7, "text": "hi", "ttl": 60,
        }
        return rt

    def test_deny_blocks(self):
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("deny", "危险")), \
             mock.patch.object(self.st, "_runtime") as rt:
            r = self.st.simplex_send_message_ttl("bob", "hi", 60)
        self.assertFalse(r["ok"])
        self.assertIn("审批拒绝", r["error"])
        rt.assert_not_called()

    def test_invalid_ttl_zero_rejected(self):
        """ttl<=0 → 明确报错,提示用普通消息。不触 runtime.send_message_ttl。"""
        fake = self._fake_rt()
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("bob", "hi", 0)
        self.assertFalse(r["ok"])
        self.assertIn("ttl", r["error"])
        fake.send_message_ttl.assert_not_called()

    def test_invalid_ttl_negative_rejected(self):
        fake = self._fake_rt()
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("bob", "hi", -5)
        self.assertFalse(r["ok"])
        fake.send_message_ttl.assert_not_called()

    def test_unknown_contact_diagnosed(self):
        fake = self._fake_rt()
        fake.resolve_contact.return_value = None
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("nobody", "hi", 60)
        self.assertFalse(r["ok"])
        self.assertIn("没有联系人", r["error"])

    def test_inactive_contact_diagnosed(self):
        fake = self._fake_rt(active=False)
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("bob", "hi", 60)
        self.assertFalse(r["ok"])
        self.assertIn("未激活", r["error"])

    def test_success_calls_runtime_with_ttl(self):
        """正常路径:解析 contact_id 后调 rt.send_message_ttl(cid, text, ttl)。"""
        fake = self._fake_rt()
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("bob", "secret", 300)
        self.assertTrue(r["ok"])
        fake.send_message_ttl.assert_called_once_with(7, "secret", 300)
        # 诊断里诚实标注防不住截图
        self.assertIn("截图", r["diagnosable"])

    def test_ask_marks_approved(self):
        fake = self._fake_rt()
        with mock.patch.object(self.st, "policy_check_daemon",
                               return_value=("ask", "需确认")), \
             mock.patch.object(self.st, "_runtime", return_value=fake):
            r = self.st.simplex_send_message_ttl("bob", "hi", 60)
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("approved"))


class TestTtlCommandString(unittest.TestCase):
    """发送端命令拼接(本地可验,不连服务器)——确保 ttl 进入 /_send 命令。"""

    def test_cmd_string_includes_ttl(self):
        from simplex_chat.types import _commands as CC
        cmd = CC.APISendMessages_cmd_string({
            "sendRef": {"chatType": "direct", "chatId": 7},
            "composedMessages": [
                {"msgContent": {"type": "text", "text": "x"}, "mentions": {}}
            ],
            "liveMessage": False,
            "ttl": 60,
        })
        self.assertIn("ttl=60", cmd)
        self.assertTrue(cmd.startswith("/_send @7"))
        self.assertIn("json", cmd)

    def test_cmd_string_omits_ttl_when_absent(self):
        from simplex_chat.types import _commands as CC
        cmd = CC.APISendMessages_cmd_string({
            "sendRef": {"chatType": "direct", "chatId": 7},
            "composedMessages": [
                {"msgContent": {"type": "text", "text": "x"}, "mentions": {}}
            ],
            "liveMessage": False,
        })
        self.assertNotIn("ttl=", cmd)


class TestRuntimeSendTtlValidation(unittest.TestCase):
    """runtime.send_message_ttl 的同步封装边界(ttl<=0 拒绝)——不启动 actor。"""

    def test_runtime_rejects_nonpositive_ttl(self):
        import simplex_runtime as sr
        rt = sr.SimplexRuntime.__new__(sr.SimplexRuntime)  # 不跑 __init__/actor
        with self.assertRaises(ValueError):
            rt.send_message_ttl(7, "hi", 0)


if __name__ == "__main__":
    unittest.main()
