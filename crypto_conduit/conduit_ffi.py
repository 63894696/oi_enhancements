"""conduit_ffi.py — ctypes 封装 crypto_conduit.dll(SecureDM 群聊 E2E Python 端加密底座)。

内存纪律(与 src/ffi.rs 注释一致):
- 所有 CcBuffer 输出由 DLL(Rust)分配,必须调 cc_buffer_free 释放;Python 绝不可 free/delete。
- 会话句柄 CcSession* 是不透明指针,配对 cc_session_destroy。
- 每个返回 buffer 的函数,取出 bytes 后立即 cc_buffer_free。

直接运行 `python conduit_ffi.py` 会执行双向握手 + 加解密自检。
"""

import ctypes
import os
from ctypes import POINTER, byref, c_char_p, c_int, c_size_t, c_void_p

# ---------- 错误 ----------

_ERROR_NAMES = {
    0: "Ok",
    1: "NullPtr",
    2: "MalformedWire",
    3: "BadSignature",
    4: "DecryptFailed",
    5: "ReplayOrStale",
    6: "SkippedTooFar",
    7: "UnknownVersion",
    8: "UnknownSuite",
    9: "BadHandle",
    10: "Internal",
}


class ConduitError(Exception):
    """DLL 返回码非 0 时抛出。`code` 属性为原始 i32 码值。"""

    def __init__(self, code: int, what: str = ""):
        self.code = code
        name = _ERROR_NAMES.get(code, f"Unknown({code})")
        msg = f"ConduitError[{code}] {name}"
        if what:
            msg += f" in {what}"
        super().__init__(msg)


def _check(code: int, what: str) -> None:
    if code != 0:
        raise ConduitError(code, what)


# ---------- ABI 类型 ----------


class CcBuffer(ctypes.Structure):
    _fields_ = [("ptr", c_void_p), ("len", c_size_t)]


_IDENTITY_SEED_LEN = 32
_LOCALKEYS_SEED_LEN = 128


# ---------- 加载 ----------

_DLL = None
_DLL_PATH = None


def load(path=None):
    """加载 crypto_conduit.dll。

    path 为 None 时,依次找同目录 target/debug、target/release 下的
    crypto_conduit.dll,返回第一个加载成功的 ctypes.CDLL。
    """
    global _DLL, _DLL_PATH
    if path is not None:
        dll = ctypes.CDLL(str(path))
        _bind(dll)
        _DLL, _DLL_PATH = dll, str(path)
        return dll

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "target", "debug", "crypto_conduit.dll"),
        os.path.join(here, "target", "release", "crypto_conduit.dll"),
    ]
    errors = []
    for cand in candidates:
        if not os.path.isfile(cand):
            errors.append(f"{cand}: not found")
            continue
        try:
            dll = ctypes.CDLL(cand)
        except OSError as e:
            errors.append(f"{cand}: {e}")
            continue
        _bind(dll)
        _DLL, _DLL_PATH = dll, cand
        return dll
    raise ConduitError(10, "load: no usable crypto_conduit.dll (" + "; ".join(errors) + ")")


def _bind(dll) -> None:
    dll.cc_buffer_free.restype = c_int
    dll.cc_buffer_free.argtypes = [CcBuffer]

    dll.cc_identity_generate.restype = c_int
    dll.cc_identity_generate.argtypes = [POINTER(CcBuffer)]

    dll.cc_identity_fingerprint.restype = c_int
    dll.cc_identity_fingerprint.argtypes = [c_char_p, c_size_t, POINTER(CcBuffer)]

    dll.cc_localkeys_generate.restype = c_int
    dll.cc_localkeys_generate.argtypes = [POINTER(CcBuffer)]

    dll.cc_localkeys_public.restype = c_int
    dll.cc_localkeys_public.argtypes = [c_char_p, c_size_t, POINTER(CcBuffer)]

    dll.cc_session_initiate.restype = c_int
    dll.cc_session_initiate.argtypes = [
        c_char_p, c_size_t, c_char_p, c_size_t,
        POINTER(c_void_p), POINTER(CcBuffer),
    ]

    dll.cc_session_accept.restype = c_int
    dll.cc_session_accept.argtypes = [
        c_char_p, c_size_t, c_char_p, c_size_t,
        POINTER(c_void_p),
    ]

    dll.cc_session_destroy.restype = None
    dll.cc_session_destroy.argtypes = [c_void_p]

    dll.cc_seal.restype = c_int
    dll.cc_seal.argtypes = [c_void_p, c_char_p, c_size_t, POINTER(CcBuffer)]

    dll.cc_open.restype = c_int
    dll.cc_open.argtypes = [c_void_p, c_char_p, c_size_t, POINTER(CcBuffer)]


def _dll():
    if _DLL is None:
        load()
    return _DLL


# ---------- 内部辅助 ----------


def _take_buffer(out: CcBuffer) -> bytes:
    """把 DLL 分配的 CcBuffer 取出为 bytes,随后立即 cc_buffer_free。"""
    try:
        if not out.ptr or out.len == 0:
            return b""
        return ctypes.string_at(out.ptr, out.len)
    finally:
        _dll().cc_buffer_free(out)


def _check_seed(seed: bytes, expect: int, name: str) -> bytes:
    if not isinstance(seed, (bytes, bytearray)):
        raise ValueError(f"{name} must be bytes")
    if len(seed) != expect:
        raise ValueError(f"{name} must be {expect} bytes, got {len(seed)}")
    return bytes(seed)


# ---------- Python API ----------


def identity_generate() -> bytes:
    """生成身份,返回 32B 种子。"""
    dll = _dll()
    out = CcBuffer()
    _check(dll.cc_identity_generate(byref(out)), "cc_identity_generate")
    return _take_buffer(out)


def identity_fingerprint(seed32: bytes) -> str:
    """32B 身份种子 -> hex 指纹字符串。"""
    seed = _check_seed(seed32, _IDENTITY_SEED_LEN, "seed32")
    dll = _dll()
    out = CcBuffer()
    _check(dll.cc_identity_fingerprint(seed, len(seed), byref(out)),
           "cc_identity_fingerprint")
    return _take_buffer(out).decode("ascii")


def localkeys_generate() -> bytes:
    """生成完整密钥材料(身份+ML-KEM+X25519),返回 128B 种子。"""
    dll = _dll()
    out = CcBuffer()
    _check(dll.cc_localkeys_generate(byref(out)), "cc_localkeys_generate")
    return _take_buffer(out)


def localkeys_public(seed128: bytes) -> bytes:
    """128B 密钥种子 -> PeerPublic 序列化 bytes。"""
    seed = _check_seed(seed128, _LOCALKEYS_SEED_LEN, "seed128")
    dll = _dll()
    out = CcBuffer()
    _check(dll.cc_localkeys_public(seed, len(seed), byref(out)),
           "cc_localkeys_public")
    return _take_buffer(out)


class Session:
    """包装 CcSession*。配对 cc_session_destroy;支持 close() 与上下文管理器。"""

    def __init__(self, handle: c_void_p):
        if not handle:
            raise ConduitError(9, "Session: null handle")
        self._h = handle

    @classmethod
    def initiate(cls, my_seed128: bytes, peer_pub: bytes):
        """发起方:(session, handshake_bytes)。my_seed128 必须 128B。"""
        seed = _check_seed(my_seed128, _LOCALKEYS_SEED_LEN, "my_seed128")
        peer = bytes(peer_pub)
        dll = _dll()
        h = c_void_p()
        out = CcBuffer()
        _check(
            dll.cc_session_initiate(seed, len(seed), peer, len(peer),
                                    byref(h), byref(out)),
            "cc_session_initiate",
        )
        handshake = _take_buffer(out)
        if not h:
            raise ConduitError(9, "cc_session_initiate: null session handle")
        return cls(h), handshake

    @classmethod
    def accept(cls, my_seed128: bytes, handshake: bytes):
        """接收方:handshake_bytes -> session。my_seed128 必须 128B。"""
        seed = _check_seed(my_seed128, _LOCALKEYS_SEED_LEN, "my_seed128")
        hs = bytes(handshake)
        dll = _dll()
        h = c_void_p()
        _check(
            dll.cc_session_accept(seed, len(seed), hs, len(hs), byref(h)),
            "cc_session_accept",
        )
        if not h:
            raise ConduitError(9, "cc_session_accept: null session handle")
        return cls(h)

    def seal(self, plaintext: bytes) -> bytes:
        self._ensure_open()
        data = bytes(plaintext)
        dll = _dll()
        out = CcBuffer()
        _check(dll.cc_seal(self._h, data, len(data), byref(out)), "cc_seal")
        return _take_buffer(out)

    def open(self, blob: bytes) -> bytes:
        self._ensure_open()
        data = bytes(blob)
        dll = _dll()
        out = CcBuffer()
        _check(dll.cc_open(self._h, data, len(data), byref(out)), "cc_open")
        return _take_buffer(out)

    def close(self) -> None:
        h = getattr(self, "_h", None)
        if h:
            self._h = None
            _dll().cc_session_destroy(h)

    def _ensure_open(self) -> None:
        if not getattr(self, "_h", None):
            raise ConduitError(9, "Session: already closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# ---------- 自检 ----------


def _selftest() -> None:
    dll = load()
    print(f"DLL: {_DLL_PATH}")

    seed_a = localkeys_generate()
    seed_b = localkeys_generate()
    assert len(seed_a) == 128 and len(seed_b) == 128, "localkeys seed must be 128B"

    pub_b = localkeys_public(seed_b)
    pub_a = localkeys_public(seed_a)

    sess_a, handshake = Session.initiate(seed_a, pub_b)
    sess_b = Session.accept(seed_b, handshake)

    try:
        msg = "hello 群聊".encode("utf-8")
        wire = sess_a.seal(msg)
        back = sess_b.open(wire)
        assert back == msg, f"A->B mismatch: {back!r} != {msg!r}"

        wire2 = sess_b.seal(msg)
        back2 = sess_a.open(wire2)
        assert back2 == msg, f"B->A mismatch: {back2!r} != {msg!r}"
    finally:
        sess_a.close()
        sess_b.close()

    print("OK roundtrip")


if __name__ == "__main__":
    try:
        _selftest()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        raise SystemExit(1)
