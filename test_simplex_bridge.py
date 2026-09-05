"""test_simplex_bridge.py — SimpleX MCP 桥接 + 安全自动接受测试

不真正发起 SimpleX 连接;mock call_tool 断言到边界。
跑法:`python -m unittest test_simplex_bridge.py`
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "mcp_prisiragent_server"))


class TestSimplexBridge(unittest.TestCase):
    """桥接层 TOOL_DEFS / HANDLERS 结构符合 dynamic_registry 提取约定。"""

    def setUp(self):
        import simplex_bridge_tools as bridge
        self.bridge = bridge

    def test_tool_defs_structure(self):
        """每个 def 有 name/description/inputSchema 平铺字段。"""
        self.assertIsInstance(self.bridge.TOOL_DEFS, list)
        self.assertGreater(len(self.bridge.TOOL_DEFS), 0)
        for d in self.bridge.TOOL_DEFS:
            self.assertIn("name", d)
            self.assertIn("description", d)
            self.assertIn("inputSchema", d)
            self.assertIsInstance(d["inputSchema"], dict)
            self.assertEqual(d["inputSchema"].get("type"), "object")

    def test_tool_defs_names_cover_source(self):
        """TOOL_DEFS 名字集 ⊆ simplex_tools.TOOL_NAMES。

        注意:源 simplex_tools.get_tools() 的 schema 列表漏了 simplex_delete_contact
        (它在 _TOOL_IMPLS/TOOL_NAMES 有实现,但无 schema)。桥接 TOOL_DEFS 只转换
        get_tools() 输出的项,故 7 个;dynamic_registry 只暴露这 7 个有 schema 的。
        """
        from simplex_tools import TOOL_NAMES
        names = {d["name"] for d in self.bridge.TOOL_DEFS}
        self.assertTrue(names.issubset(set(TOOL_NAMES)))
        self.assertEqual(len(names), len(self.bridge.get_tools()))

    def test_handlers_cover_defs(self):
        """每个 def 名都有可调 handler(dynamic_registry 要求 name in HANDLERS)。"""
        for d in self.bridge.TOOL_DEFS:
            self.assertIn(d["name"], self.bridge.HANDLERS)
            self.assertTrue(callable(self.bridge.HANDLERS[d["name"]]))

    def test_handler_routes_to_call_tool(self):
        """handler(**kw) → call_tool(name, kw),闭包名绑定正确(晚绑定坑)。

        桥接 handler 持有 import 时绑定的 call_tool 引用,故 mock 桥接层命名空间。
        """
        with mock.patch.object(self.bridge, "call_tool",
                               return_value={"ok": True, "output": "stub"}) as ct:
            self.bridge.HANDLERS["simplex_list_contacts"]()
            ct.assert_called_once_with("simplex_list_contacts", {})

    def test_handler_names_distinct(self):
        """不同工具的 handler 不同名绑定(验证闭包用默认参数捕获)。"""
        h1 = self.bridge.HANDLERS["simplex_setup"]
        h2 = self.bridge.HANDLERS["simplex_send_message"]
        self.assertEqual(h1.__name__, "simplex_setup")
        self.assertEqual(h2.__name__, "simplex_send_message")
        self.assertIsNot(h1, h2)


class TestScanAndAccept(unittest.TestCase):
    """scan_and_accept 链接识别 + 审批闸边界。"""

    def setUp(self):
        import importlib
        import simplex_auto_accept as saa
        importlib.reload(saa)  # 重置白名单缓存,测试隔离
        saa._WHITELIST_PATH = ""
        saa._CONFIRMED_LINKS = set()
        self.saa = saa

    # ── 链接识别 ──
    def test_extract_simplex_invitation(self):
        text = "加我: simplex:/invitation#abcDEF123_-xyz 谢谢"
        self.assertEqual(self.saa.extract_invitation_link(text),
                         "simplex:/invitation#abcDEF123_-xyz")

    def test_extract_https_short_link(self):
        text = "戳 https://smp.example.com/i#somedata 添加"
        self.assertEqual(self.saa.extract_invitation_link(text),
                         "https://smp.example.com/i#somedata")

    def test_extract_https_full_link(self):
        text = "链接 https://app.simplex.chat/invitation#contactdata。"
        self.assertEqual(self.saa.extract_invitation_link(text),
                         "https://app.simplex.chat/invitation#contactdata")

    def test_extract_none_on_plain_text(self):
        self.assertIsNone(self.saa.extract_invitation_link("你好,没有链接"))

    def test_extract_strips_trailing_punct(self):
        text = "用这个 simplex:/invitation#data123."
        self.assertEqual(self.saa.extract_invitation_link(text),
                         "simplex:/invitation#data123")

    # ── scan_and_accept ──
    def test_found_false_on_no_link(self):
        r = self.saa.scan_and_accept("纯文本,无链接")
        self.assertFalse(r["found"])
        self.assertIsNone(r["link"])
        self.assertFalse(r["accepted"])

    def test_found_true_on_link(self):
        # 双保险:policy allow + 人工 confirm 后才接受
        self.saa.confirm_invitation("simplex:/invitation#xyz")
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("allow", "ok")) as pc, \
             mock.patch.object(self.saa, "call_tool",
                               return_value={"ok": True, "diagnosable": "已联系"}) as ct:
            r = self.saa.scan_and_accept("加 simplex:/invitation#xyz 我")
        self.assertTrue(r["found"])
        self.assertEqual(r["link"], "simplex:/invitation#xyz")
        self.assertTrue(r["accepted"])
        pc.assert_called_once()
        ct.assert_called_once_with("simplex_accept_invitation",
                                   {"link": "simplex:/invitation#xyz"})

    def test_policy_deny_blocks(self):
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("deny", "危险")), \
             mock.patch.object(self.saa, "call_tool") as ct:
            r = self.saa.scan_and_accept("加 simplex:/invitation#xyz")
        self.assertTrue(r["found"])
        self.assertFalse(r["accepted"])
        self.assertIn("审批拒绝", r["reason"])
        ct.assert_not_called()

    def test_policy_ask_does_not_auto_accept(self):
        """ask = 需人工;standalone 不擅自批准,绝不静默加陌生人。"""
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("ask", "需确认")), \
             mock.patch.object(self.saa, "call_tool") as ct:
            r = self.saa.scan_and_accept("加 simplex:/invitation#xyz")
        self.assertTrue(r["found"])
        self.assertFalse(r["accepted"])
        self.assertIn("需人工批准", r["reason"])
        ct.assert_not_called()

    def test_contact_already_exists_passthrough(self):
        """ContactAlreadyExists 诊断直接透传(不额外处理)。"""
        err = {"ok": False, "error": "接受邀请失败",
               "diagnosable": "该联系人已存在,无需重复接受。"}
        self.saa.confirm_invitation("simplex:/invitation#xyz")  # 双保险:先确认
        with mock.patch.object(self.saa, "policy_check_daemon",
                               return_value=("allow", "ok")), \
             mock.patch.object(self.saa, "call_tool", return_value=err):
            r = self.saa.scan_and_accept("加 simplex:/invitation#xyz")
        self.assertTrue(r["found"])
        self.assertFalse(r["accepted"])
        self.assertIn("已存在", r["diagnosable"])


class TestDynamicRegistryExtraction(unittest.TestCase):
    """模拟 dynamic_registry 的 exec 沙箱提取,验证桥接模块能被发现。

    注意:全目录 discover_tool_modules 扫描时,既有模块 patch_team_lead.py
    在 import 时调 sys.exit(0),而 dynamic_registry 只 catch Exception 不 catch
    SystemExit → 扫描提前中断。这是既有 bug(非本桥接引入),故本测试单独对
    simplex_bridge_tools 做 exec 沙箱提取验证(与 discover 的内部机制一致)。
    """

    def test_extract_via_dynamic_registry(self):
        import importlib
        import dynamic_registry as dr
        entry = HERE / "mcp_prisiragent_server" / "simplex_bridge_tools.py"
        code = entry.read_text(encoding="utf-8")
        g = {
            "__name__": "simplex_bridge_tools", "__file__": str(entry),
            "__package__": "", "__builtins__": __builtins__,
            "sys": sys, "json": __import__("json"), "os": __import__("os"),
            "Path": Path, "importlib": importlib,
            "re": __import__("re"), "typing": __import__("typing"),
        }
        # 注意:不用 DynamicModule 占位污染 sys.modules['simplex_bridge_tools'],
        # 否则会遮蔽后续 test 对真模块的 import。dynamic_registry 提取只看 g。
        exec(compile(code, str(entry), "exec"), g)
        self.assertIn("TOOL_DEFS", g)
        self.assertIn("HANDLERS", g)
        extracted = dr._extract_tool_defs("simplex_bridge_tools", g)
        tools = {it["tool"].name for it in extracted}
        self.assertIn("simplex_accept_invitation", tools)
        self.assertIn("simplex_create_invitation", tools)
        # handler 可调
        for it in extracted:
            self.assertTrue(callable(it["handler"]))


if __name__ == "__main__":
    unittest.main()
