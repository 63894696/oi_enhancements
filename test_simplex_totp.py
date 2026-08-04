"""RFC 6238 TOTP 测试 — 官方测试向量 + 时间窗 + 容错。

向量来自 RFC 6238 Appendix B(SHA1,8 位码);本实现取 6 位,故断言官方 8 位码的后 6 位。
共享密钥(ASCII "12345678901234567890")的 base32 编码。
"""
from __future__ import annotations

import base64
import unittest

import simplex_totp as t

# RFC 6238 测试密钥(ASCII)-> base32(不带填充,与 generate_secret 同格式)
_SEED_ASCII = b"12345678901234567890"
_SECRET = base64.b32encode(_SEED_ASCII).decode("ascii").rstrip("=")

# RFC 6238 Appendix B(SHA1):时间 -> 8 位码。本实现 6 位 = 后 6 位。
_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


class TestRfcVectors(unittest.TestCase):
    def test_official_vectors_last6(self):
        for ts, code8 in _VECTORS:
            with self.subTest(ts=ts):
                self.assertEqual(t.totp_code(_SECRET, at=ts), code8[-6:])

    def test_verify_accepts_current_window(self):
        ts = 1234567890
        code = t.totp_code(_SECRET, at=ts)
        self.assertTrue(t.verify_totp(_SECRET, code, at=ts))


class TestTimeWindow(unittest.TestCase):
    def test_neighbor_windows_accepted(self):
        # window=±1 → t-30s / t / t+30s 三个窗的码都应通过
        base = 1_700_000_000
        for delta in (-30, 0, 30):
            code = t.totp_code(_SECRET, at=base + delta)
            self.assertTrue(t.verify_totp(_SECRET, code, at=base), f"delta={delta}")

    def test_far_window_rejected(self):
        base = 1_700_000_000
        code_far = t.totp_code(_SECRET, at=base + 120)  # 超出 ±1 窗
        self.assertFalse(t.verify_totp(_SECRET, code_far, at=base))


class TestBadInput(unittest.TestCase):
    def test_wrong_code(self):
        self.assertFalse(t.verify_totp(_SECRET, "000000", at=59))  # 59s 真码 287082

    def test_non_numeric(self):
        self.assertFalse(t.verify_totp(_SECRET, "abcdef", at=59))

    def test_wrong_length(self):
        self.assertFalse(t.verify_totp(_SECRET, "12345", at=59))
        self.assertFalse(t.verify_totp(_SECRET, "1234567", at=59))

    def test_empty(self):
        self.assertFalse(t.verify_totp(_SECRET, "", at=59))

    def test_bad_secret(self):
        self.assertFalse(t.verify_totp("!!!not-base32!!!", "123456", at=59))

    def test_secret_with_spaces_and_case(self):
        # 容错:空格 + 小写密钥
        spaced = " ".join([_SECRET[i:i+4] for i in range(0, len(_SECRET), 4)]).lower()
        code = t.totp_code(_SECRET, at=59)
        self.assertTrue(t.verify_totp(spaced, code, at=59))


class TestSecretGen(unittest.TestCase):
    def test_generate_roundtrip(self):
        s = t.generate_secret()
        self.assertTrue(s.isalnum() and "=" not in s)
        code = t.totp_code(s, at=59)
        self.assertTrue(t.verify_totp(s, code, at=59))

    def test_unique(self):
        self.assertNotEqual(t.generate_secret(), t.generate_secret())


class TestUri(unittest.TestCase):
    def test_otpauth_uri(self):
        uri = t.totp_uri("ABC123", account="bob", issuer="SecureDM")
        self.assertTrue(uri.startswith("otpauth://totp/SecureDM:bob"))
        self.assertIn("secret=ABC123", uri)
        self.assertIn("issuer=SecureDM", uri)


if __name__ == "__main__":
    unittest.main()
