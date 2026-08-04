"""批次A2 后端测试:密码门(locked 拦截/内存注入)+ 2FA API 组合。

全部 mock 掉 SimplexRuntime 与 st.call_tool,不触碰真实 simplex 库;
密钥/库前缀指向临时目录,测完即清。
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 用独立实例身份 + 临时库前缀,避免碰到真实 oiagent/bob 库
_TMP = tempfile.mkdtemp(prefix="sdm_a2_")
os.environ["DM_IDENTITY"] = "a2test"
os.environ["DM_DB_PREFIX"] = str(Path(_TMP) / "a2test_simplex")
os.environ.setdefault("DM_PORT", "18901")

sys.path.insert(0, str(Path(__file__).parent))

import securedm_web as web  # noqa: E402
import simplex_totp as stotp  # noqa: E402


def _fresh_runtime():
    """一个停掉的假 runtime(无线程),带 _db_key 槽。"""
    rt = mock.Mock()
    rt._thread = None
    rt._db_key = None
    rt._display_name = "a2test"
    return rt


class Base(unittest.TestCase):
    def setUp(self):
        web._rt = lambda: self.rt  # type: ignore[attr-defined]
        self.rt = _fresh_runtime()
        # 清掉可能残留的密钥/totp 文件
        for f in Path(_TMP).glob("a2test_simplex*"):
            try:
                f.unlink()
            except Exception:
                pass

    def tearDown(self):
        importlib.reload(web)  # 还原 _rt


class TestPasswordApis(Base):
    def test_status_empty(self):
        self.assertFalse(web.api_db_password_status()["output"]["encrypted"])

    def test_set_then_status(self):
        r = web.api_db_set_password("pw1234")
        self.assertTrue(r["ok"])
        self.assertTrue(web.api_db_password_status()["output"]["encrypted"])
        # 内存注入
        self.assertIsNotNone(self.rt._db_key)

    def test_set_too_short(self):
        self.assertFalse(web.api_db_set_password("ab")["ok"])

    def test_unlock_correct(self):
        web.api_db_set_password("pw1234")
        self.rt._db_key = None  # 模拟重启后门未解锁
        with mock.patch.object(web.st, "call_tool", return_value={"ok": True}) as ct:
            r = web.api_db_unlock("pw1234")
        self.assertTrue(r["ok"], r)
        self.assertIsNotNone(self.rt._db_key)
        ct.assert_called()  # 解锁后触发了 setup 重启到加密库

    def test_unlock_wrong(self):
        web.api_db_set_password("pw1234")
        self.rt._db_key = None
        r = web.api_db_unlock("nope")
        self.assertFalse(r["ok"])
        self.assertIsNone(self.rt._db_key)

    def test_unlock_does_not_write_env(self):
        os.environ.pop("DM_DB_KEY", None)
        web.api_db_set_password("pw1234")
        self.rt._db_key = None
        with mock.patch.object(web.st, "call_tool", return_value={"ok": True}):
            web.api_db_unlock("pw1234")
        self.assertNotIn("DM_DB_KEY", os.environ)


class TestLockStatus(Base):
    def test_needs_unlock_when_password_but_no_key(self):
        web.api_db_set_password("pw1234")
        self.rt._db_key = None
        st = web.api_db_lock_status()["output"]
        self.assertTrue(st["has_password"])
        self.assertTrue(st["needs_unlock"])

    def test_no_unlock_needed_after_unlock(self):
        web.api_db_set_password("pw1234")  # 设口令即注入内存 key
        st = web.api_db_lock_status()["output"]
        self.assertFalse(st["needs_unlock"])

    def test_first_run(self):
        st = web.api_db_lock_status()["output"]
        self.assertTrue(st["first_run"])  # 临时目录无库文件
        self.assertFalse(st["has_password"])


class TestTotpApis(Base):
    def setUp(self):
        super().setUp()
        web.api_db_set_password("pw1234")  # 2FA 前置:先设口令

    def test_status_disabled_initially(self):
        self.assertFalse(web.api_2fa_status()["output"]["totp_enabled"])

    def test_setup_returns_secret_and_uri(self):
        r = web.api_2fa_setup()
        self.assertTrue(r["ok"])
        self.assertIn("secret", r["output"])
        self.assertTrue(r["output"]["otpauth_uri"].startswith("otpauth://totp/"))

    def test_setup_requires_password(self):
        # 清掉 key 文件再调 setup → 应拒
        for f in Path(_TMP).glob("a2test_simplex.key"):
            f.unlink()
        r = web.api_2fa_setup()
        self.assertFalse(r["ok"])

    def test_enable_with_valid_code(self):
        secret = web.api_2fa_setup()["output"]["secret"]
        code = stotp.totp_code(secret)
        r = web.api_2fa_enable(code)
        self.assertTrue(r["ok"], r)
        self.assertTrue(web.api_2fa_status()["output"]["totp_enabled"])

    def test_enable_with_bad_code(self):
        web.api_2fa_setup()
        self.assertFalse(web.api_2fa_enable("000000")["ok"])
        self.assertFalse(web.api_2fa_status()["output"]["totp_enabled"])

    def test_disable(self):
        secret = web.api_2fa_setup()["output"]["secret"]
        web.api_2fa_enable(stotp.totp_code(secret))
        r = web.api_2fa_disable(stotp.totp_code(secret))
        self.assertTrue(r["ok"])
        self.assertFalse(web.api_2fa_status()["output"]["totp_enabled"])

    def test_unlock_requires_totp_when_enabled(self):
        secret = web.api_2fa_setup()["output"]["secret"]
        web.api_2fa_enable(stotp.totp_code(secret))
        self.rt._db_key = None
        with mock.patch.object(web.st, "call_tool", return_value={"ok": True}):
            r = web.api_db_unlock("pw1234")  # 无 totp 码
        self.assertFalse(r["ok"])
        self.assertIn("动态码", r.get("error", "") + r.get("diagnosable", ""))

    def test_unlock_with_totp(self):
        secret = web.api_2fa_setup()["output"]["secret"]
        web.api_2fa_enable(stotp.totp_code(secret))
        self.rt._db_key = None
        with mock.patch.object(web.st, "call_tool", return_value={"ok": True}):
            r = web.api_db_unlock("pw1234", stotp.totp_code(secret))
        self.assertTrue(r["ok"], r)


class TestGateOrdering(unittest.TestCase):
    """结构性断言:do_POST 里 locked 拦截必须排在白名单 API 之后、业务 API 之前。"""

    def test_gate_after_whitelist_before_business(self):
        import inspect
        import re
        src = inspect.getsource(web.DMHandler.do_POST)
        gate_pos = src.find("_locked_gate_active()")
        self.assertGreater(gate_pos, 0, "locked 拦截不存在")
        # 白名单 API 必须在门之前注册
        for wl in ("db_unlock", "db_password_status", "db_lock_status", "new_user_id",
                   "2fa_status", "2fa_setup", "2fa_enable", "2fa_disable"):
            pos = src.find(f'"/dm/api/{wl}"')
            self.assertGreater(pos, 0, f"白名单 {wl} 未注册")
            self.assertLess(pos, gate_pos, f"白名单 {wl} 必须在 locked 拦截之前")
        # 业务(读/发消息数据)API 必须在门之后被拦截。setup/create_invite 等引导类
        # 刻意留在门前(首启初始化需要;对已加密库,带空密钥 setup 会被 SQLCipher 拒)。
        for biz in ('"/dm/api/send"', '"/dm/api/send_file"', '"/dm/api/delete_contact"'):
            pos = src.find(biz)
            self.assertGreater(pos, 0, f"业务 API {biz} 未在 do_POST 注册")
            self.assertGreater(pos, gate_pos, f"业务 API {biz} 必须在 locked 拦截之后")

    def test_get_also_gated(self):
        import inspect
        src = inspect.getsource(web.DMHandler.do_GET)
        gate_pos = src.find("_locked_gate_active()")
        self.assertGreater(gate_pos, 0, "do_GET 缺 locked 拦截")
        # status/contacts/history(读消息数据)必须在门之后
        for biz in ('"/dm/api/status"', '"/dm/api/contacts"', '"/dm/api/history"'):
            pos = src.find(biz)
            self.assertGreater(pos, 0, f"{biz} 未在 do_GET 注册")
            self.assertGreater(pos, gate_pos, f"{biz} 必须在 do_GET locked 拦截之后")


if __name__ == "__main__":
    unittest.main()
