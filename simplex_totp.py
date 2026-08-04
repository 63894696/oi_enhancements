"""SecureDM 2FA — RFC 6238 TOTP(纯 stdlib,零依赖)。

设计:
- 共享密钥 base32 编码(兼容 Google Authenticator / 2FAS / Aegis 等标准工具)。
- TOTP = HMAC-SHA1(密钥, 30 秒时间窗计数器),取 6 位动态截断码。
- 只借鉴这些工具的「扫码/手输密钥」交互,不绑定任何账户体系;密钥完全本地。
- 校验允许 ±1 个时间窗(共 3 窗),容忍客户端与服务器 30~90 秒时钟漂移。

所有函数离线、无副作用;密钥的落盘/权限由调用方(securedm_web)负责(0600)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

# 6 位码、30 秒步长、SHA1 —— 与 Google Authenticator/2FAS 默认完全一致。
_DIGITS = 6
_PERIOD = 30
_ALGO = hashlib.sha1


def generate_secret(num_bytes: int = 20) -> str:
    """生成 base32 编码的共享密钥(默认 20 字节 = 160bit,SHA1 标准长度)。

    base32 不带 '=' 填充,正是各验证器 App 期望的格式。
    """
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """base32 解码;容错:去空格、补回被剥掉的 '=' 填充、大小写不敏感。"""
    s = (secret or "").replace(" ", "").upper()
    if not s:
        raise ValueError("空密钥")
    pad = (-len(s)) % 8
    s += "=" * pad
    return base64.b32decode(s)


def _hotp(secret_bytes: bytes, counter: int) -> str:
    """RFC 4226 HOTP:HMAC-SHA1(secret, counter) 动态截断取 6 位。"""
    msg = struct.pack(">Q", counter)
    digest = hmac.new(secret_bytes, msg, _ALGO).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**_DIGITS)).zfill(_DIGITS)


def totp_code(secret: str, at: float | None = None) -> str:
    """算某一时刻(默认现在)的 TOTP 码。主要供测试/调试;校验请用 verify_totp。"""
    ts = time.time() if at is None else at
    counter = int(ts // _PERIOD)
    return _hotp(_decode_secret(secret), counter)


def verify_totp(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    """校验用户输入的 6 位码。window=±1 → 检查 [t-1, t, t+1] 三个时间窗。

    只接受纯数字且长度正确;常量时间比对防时序侧信道(码短,风险低,但顺手做对)。
    """
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == _DIGITS):
        return False
    try:
        secret_bytes = _decode_secret(secret)
    except Exception:  # noqa: BLE001
        return False
    ts = time.time() if at is None else at
    counter = int(ts // _PERIOD)
    for w in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_bytes, counter + w), code):
            return True
    return False


def totp_uri(secret: str, account: str, issuer: str = "SecureDM") -> str:
    """otpauth:// URI,供验证器 App 扫码导入(沿用现有 renderInviteQr 渲染二维码)。"""
    label = urllib.parse.quote(f"{issuer}:{account}", safe=":")
    q = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": _DIGITS, "period": _PERIOD})
    return f"otpauth://totp/{label}?{q}"
