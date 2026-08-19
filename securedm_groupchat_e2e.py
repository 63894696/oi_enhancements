"""securedm_groupchat_e2e.py — SecureDM 群聊 E2E 密码学层(形态3)。

房间钥管理 + 逐人分发协议:
- AES-256-GCM 房间对称钥(每房间按 epoch 存钥,旧钥缓存解历史)。
- 逐人分发借用 crypto_conduit 已测通的会话(X25519+ML-KEM 握手 +
  XChaCha20-Poly1305 AEAD,身份签名防 MITM):房主对每成员一次性
  initiate+seal(key32),接收方 accept+open 解回。
- 成员离开即轮换(epoch+1,新钥只发给剩余成员 → 前向保密)。

只产 JSON 可序列化的 body dict(kind=key-dist / ctext),relay 层原样转发,
定向投递由调用方填 body["to_user_id"]。不接 WS、不改 relay。

套件:
- SUITE_RUST(0x01): X25519+ML-KEM / XChaCha20-Poly1305(conduit 原生)。
- SUITE_WEB_AESGCM(0x02): X25519+AES-256-GCM,与 Web 端 WebCrypto 互操作,
  Python 端 seal_msg 默认用此套件。

跨端分发(suite=2 key-dist,与 Web 端 chatroom.html 逐字节对齐):
- 包裹:临时 X25519 对 → 与对端 X25519 公钥 ECDH(32B shared)→
  HKDF-SHA256(salt = 32×0x00, info = "securedm-roomkey-v1")导出
  AES-256-GCM-256 wrapKey → 12B 随机 nonce AES-GCM 加密 32B 房间钥。
- body: {"kind":"key-dist","suite":2,"epoch":N,"room":..,"to_user_id":..,
  "eph":b64(临时公钥32B),"nonce":b64(12B),"ct":b64}。
- 每个 GroupE2E 持有一对**长期 X25519**(分发用),公告 x25519_public_bytes;
  与 conduit `.public`(suite=1)并列。持久化 export_secret() 打包
  seed128(128B)+x25519_priv(32B)=160B。
"""

import base64
import os
import sys

# crypto_conduit/conduit_ffi.py 已在仓库中测通,直接复用。
_CRYPTO_CONDUIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "crypto_conduit")
if _CRYPTO_CONDUIT_DIR not in sys.path:
    sys.path.insert(0, _CRYPTO_CONDUIT_DIR)

import conduit_ffi  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: E402
    X25519PrivateKey, X25519PublicKey)
from cryptography.hazmat.primitives.ciphers.aead import (  # noqa: E402
    AESGCM, ChaCha20Poly1305)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402

SUITE_RUST = 0x01        # X25519+ML-KEM / XChaCha20(conduit wire.rs SUITE_X25519_MLKEM768)
SUITE_WEB_AESGCM = 0x02  # X25519+AES-256-GCM(conduit wire.rs SUITE_X25519_AESGCM,Web 端)

_KEY_LEN = 32
_NONCE_LEN = 12  # AES-GCM / ChaCha20-Poly1305 标准 96-bit nonce

# 跨端(suite=2)房间钥包裹 HKDF 参数:与 Web 端 chatroom.html 逐字节对齐
_HKDF_SALT = b"\x00" * 32
_HKDF_INFO = b"securedm-roomkey-v1"
_X25519_PRIV_LEN = 32
# 持久化布局:seed128(128B) + x25519_priv(32B) = 160B
_SEED_LEN = 128
_SECRET_LEN = _SEED_LEN + _X25519_PRIV_LEN


def _x25519_pub_raw(priv: X25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _hkdf_wrap_key(shared: bytes) -> bytes:
    """ECDH shared(32B)→ HKDF-SHA256(salt=32×0x00, info=HKDF_INFO)→ 32B AES key。"""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=_KEY_LEN,
                salt=_HKDF_SALT, info=_HKDF_INFO)
    return hkdf.derive(bytes(shared))


def _b64e(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class RoomKeyBook:
    """每个房间的对称房间钥账本:按 epoch 存钥,旧钥缓存用于解历史。"""

    def __init__(self):
        # {room: {"cur_epoch": int, "keys": {epoch: bytes32}}}
        self._rooms = {}

    def set_key(self, room, epoch, key32):
        """登记某房间某 epoch 的房间钥;cur_epoch 取已登记的最大值。"""
        key = bytes(key32)
        if len(key) != _KEY_LEN:
            raise ValueError(f"key32 must be {_KEY_LEN} bytes, got {len(key)}")
        epoch = int(epoch)
        ent = self._rooms.setdefault(room, {"cur_epoch": -1, "keys": {}})
        ent["keys"][epoch] = key
        if epoch > ent["cur_epoch"]:
            ent["cur_epoch"] = epoch

    def get_key(self, room, epoch=None) -> bytes:
        """取房间钥。epoch=None 取当前 epoch;没有则抛 KeyError。"""
        ent = self._rooms.get(room)
        if ent is None:
            raise KeyError(f"no room key for room {room!r}")
        if epoch is None:
            epoch = ent["cur_epoch"]
        try:
            return ent["keys"][int(epoch)]
        except KeyError:
            raise KeyError(
                f"no key for room {room!r} epoch {epoch}") from None

    def current_epoch(self, room) -> int:
        ent = self._rooms.get(room)
        return ent["cur_epoch"] if ent else -1


class GroupE2E:
    """一个成员的群聊 E2E:持有自己的 conduit 密钥材料,管多个房间的钥。"""

    def __init__(self, seed128=None, x25519_priv32=None):
        self._seed = bytes(seed128) if seed128 is not None \
            else conduit_ffi.localkeys_generate()
        if len(self._seed) != _SEED_LEN:
            raise ValueError(f"seed128 must be {_SEED_LEN} bytes")
        self._public = conduit_ffi.localkeys_public(self._seed)
        # suite=2 跨端分发的长期 X25519 对(与 conduit 身份独立,纯 ECDH 用)
        if x25519_priv32 is not None:
            raw = bytes(x25519_priv32)
            if len(raw) != _X25519_PRIV_LEN:
                raise ValueError(f"x25519_priv32 must be {_X25519_PRIV_LEN} bytes")
            self._x25519 = X25519PrivateKey.from_private_bytes(raw)
        else:
            self._x25519 = X25519PrivateKey.generate()
        self.book = RoomKeyBook()

    @property
    def public(self) -> bytes:
        """PeerPublic bytes,逐人分发前要发给其他成员。"""
        return self._public

    @property
    def x25519_public_bytes(self) -> bytes:
        """suite=2 跨端分发公告的 X25519 公钥(raw 32B),与 `.public` 并列。"""
        return _x25519_pub_raw(self._x25519)

    # —— 持久化:seed128 + x25519 私钥 打包(不打印真值)——

    def export_secret(self) -> bytes:
        """打包全部密钥材料:seed128(128B) + x25519_priv(32B) = 160B。"""
        return self._seed + self._x25519_priv_bytes()

    def _x25519_priv_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._x25519.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())

    @classmethod
    def from_secret(cls, blob: bytes) -> "GroupE2E":
        """从 export_secret 的 160B 恢复;向后兼容旧 128B 纯 seed 文件
        (重新生成 X25519 对并打印提示)。"""
        raw = bytes(blob)
        if len(raw) == _SECRET_LEN:
            return cls(seed128=raw[:_SEED_LEN], x25519_priv32=raw[_SEED_LEN:])
        if len(raw) == _SEED_LEN:
            print("[GroupE2E] 旧格式(128B,无 X25519):已重新生成分发用 X25519 对,"
                  "请重新公告 e2e_pub", flush=True)
            return cls(seed128=raw)
        raise ValueError(
            f"secret must be {_SECRET_LEN} bytes (or legacy {_SEED_LEN}), got {len(raw)}")

    # —— 房间钥(对称)生成与逐人分发 ——

    def new_room_key(self) -> bytes:
        """生成 32B 随机房间钥。新房/轮换时由房主调。"""
        return os.urandom(_KEY_LEN)

    def wrap_for_member(self, room, epoch, key32, member_pub) -> dict:
        """用 conduit 对某成员一次性 initiate+seal(key32),产出 suite=1 key-dist body。

        body 为 JSON 可序列化 dict;"to_user_id" 由调用方按 relay 的
        定向投递语义填写。
        member_pub: conduit PeerPublic bytes;兼容 ChatClient.member_pubs 的
        {"rust":..,"x25519":..} dict 形态(取其 rust 键)。
        """
        if isinstance(member_pub, dict):  # 兼容:member_pubs 记录形态
            member_pub = member_pub.get("rust")
        if not member_pub:
            raise ValueError("member conduit PeerPublic required for suite=1 wrap")
        key = bytes(key32)
        if len(key) != _KEY_LEN:
            raise ValueError(f"key32 must be {_KEY_LEN} bytes")
        sess, hs = conduit_ffi.Session.initiate(self._seed, bytes(member_pub))
        try:
            ct = sess.seal(key)
        finally:
            sess.close()
        return {
            "kind": "key-dist",
            "to_user_id": None,  # 由调用方填
            "suite": SUITE_RUST,
            "epoch": int(epoch),
            "room": room,
            "hs": _b64e(hs),
            "ct": _b64e(ct),
        }

    def unwrap_key_dist(self, body: dict):
        """接收 key-dist,按 body["suite"] 分派:suite=1 走 conduit,suite=2 走 Web 路径。

        返回 (room, epoch, key32)。
        """
        if body.get("kind") != "key-dist":
            raise ValueError("not a key-dist body")
        suite = int(body.get("suite", SUITE_RUST))
        if suite == SUITE_WEB_AESGCM:
            return self.unwrap_key_dist_web(body)
        if suite == SUITE_RUST:
            return self._unwrap_key_dist_rust(body)
        raise ValueError(f"unknown key-dist suite {suite}")

    def _unwrap_key_dist_rust(self, body: dict):
        """suite=1:accept(hs) -> session -> open(ct) -> key32,登记进 book。"""
        sess = conduit_ffi.Session.accept(self._seed, _b64d(body["hs"]))
        try:
            key = sess.open(_b64d(body["ct"]))
        finally:
            sess.close()
        if len(key) != _KEY_LEN:
            raise ValueError(f"unwrapped key must be {_KEY_LEN} bytes")
        room, epoch = body["room"], int(body["epoch"])
        self.book.set_key(room, epoch, key)
        return room, epoch, key

    # —— suite=2 跨端分发(与 Web 端 chatroom.html 逐字节对齐)——

    def wrap_for_member_web(self, room, epoch, key32,
                            member_x25519_pub32: bytes) -> dict:
        """按 Web 端参数包裹房间钥:临时 X25519 ECDH → HKDF(salt=32×0x00,
        info="securedm-roomkey-v1")→ AES-256-GCM。产 suite=2 key-dist body。

        member_x25519_pub32: 对端公告的 X25519 公钥(raw 32B,不是 conduit pub)。
        """
        key = bytes(key32)
        if len(key) != _KEY_LEN:
            raise ValueError(f"key32 must be {_KEY_LEN} bytes")
        pub_raw = bytes(member_x25519_pub32)
        if len(pub_raw) != _X25519_PRIV_LEN:
            raise ValueError(f"member x25519 pub must be {_X25519_PRIV_LEN} bytes")
        their_pub = X25519PublicKey.from_public_bytes(pub_raw)
        eph = X25519PrivateKey.generate()
        shared = eph.exchange(their_pub)
        wrap_key = _hkdf_wrap_key(shared)
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(wrap_key).encrypt(nonce, key, None)
        return {
            "kind": "key-dist",
            "to_user_id": None,  # 由调用方填
            "suite": SUITE_WEB_AESGCM,
            "epoch": int(epoch),
            "room": room,
            "eph": _b64e(_x25519_pub_raw(eph)),
            "nonce": _b64e(nonce),
            "ct": _b64e(ct),
        }

    def unwrap_key_dist_web(self, body: dict):
        """suite=2 接收:用自己的长期 X25519 私钥与 eph 公钥 ECDH → 同 HKDF →
        AES-GCM 解密 key32,登记进 book。返回 (room, epoch, key32)。"""
        eph_pub = X25519PublicKey.from_public_bytes(_b64d(body["eph"]))
        shared = self._x25519.exchange(eph_pub)
        wrap_key = _hkdf_wrap_key(shared)
        key = AESGCM(wrap_key).decrypt(_b64d(body["nonce"]), _b64d(body["ct"]), None)
        if len(key) != _KEY_LEN:
            raise ValueError(f"unwrapped key must be {_KEY_LEN} bytes")
        room, epoch = body["room"], int(body["epoch"])
        self.book.set_key(room, epoch, key)
        return room, epoch, key

    # —— 消息加解密(用房间对称钥,不是 conduit 会话)——

    @staticmethod
    def _aead(suite, key32):
        if suite == SUITE_WEB_AESGCM:
            return AESGCM(key32)
        if suite == SUITE_RUST:
            return ChaCha20Poly1305(key32)
        raise ValueError(f"unknown suite {suite}")

    def seal_msg(self, room, plaintext: str, suite=SUITE_WEB_AESGCM) -> dict:
        """用当前 epoch 房间钥加密消息,产出 ctext body。"""
        epoch = self.book.current_epoch(room)
        if epoch < 0:
            raise KeyError(f"no room key for room {room!r}")
        key = self.book.get_key(room, epoch)
        nonce = os.urandom(_NONCE_LEN)
        ct = self._aead(suite, key).encrypt(nonce,
                                            plaintext.encode("utf-8"), None)
        return {
            "kind": "ctext",
            "suite": int(suite),
            "epoch": epoch,
            "room": room,
            "nonce": _b64e(nonce),
            "ct": _b64e(ct),
        }

    def open_msg(self, body: dict) -> str:
        """按 body["epoch"] 取 book 里的房间钥解密。无钥/验签失败抛错。"""
        if body.get("kind") != "ctext":
            raise ValueError("not a ctext body")
        key = self.book.get_key(body["room"], body["epoch"])  # KeyError 即"没有该 epoch 钥"
        pt = self._aead(int(body["suite"]), key).decrypt(
            _b64d(body["nonce"]), _b64d(body["ct"]), None)
        return pt.decode("utf-8")

    # —— 成员离开轮换 ——

    def rotate(self, room, remaining_member_pubs: dict) -> list:
        """epoch+1 生成新钥,对每个剩余成员按套件分发,返回 key-dist body 列表。

        remaining_member_pubs: {user_id: pub}。pub 形态:
        - dict(新版): {"rust": PeerPublic bytes|None, "x25519": raw32 bytes|None}
          → 有 x25519 公钥则 suite=2(wrap_for_member_web,全端通用,优先);
            只有 rust 公钥则 suite=1(wrap_for_member)。
        - bytes(旧版,向后兼容): conduit PeerPublic → suite=1。
        (不含离开者;是否含自己由调用方决定,含自己则自己也会收到一把登记用)
        """
        old_epoch = self.book.current_epoch(room)
        new_epoch = old_epoch + 1 if old_epoch >= 0 else 0
        key = self.new_room_key()
        self.book.set_key(room, new_epoch, key)
        out = []
        for user_id, pub in remaining_member_pubs.items():
            body = self._wrap_for(room, new_epoch, key, pub)
            if body is None:
                continue  # 该成员无任何可用公钥(如纯 Web 成员在纯 Rust 路径下)
            body["to_user_id"] = user_id
            out.append(body)
        return out

    def _wrap_for(self, room, epoch, key32, pub) -> dict | None:
        """按 pub 形态选套件:优先 suite=2(跨端互通),退化 suite=1(纯 Rust)。"""
        if isinstance(pub, dict):
            x_pub = pub.get("x25519")
            if x_pub:
                return self.wrap_for_member_web(room, epoch, key32, x_pub)
            rust_pub = pub.get("rust")
            if rust_pub:
                return self.wrap_for_member(room, epoch, key32, rust_pub)
            return None
        return self.wrap_for_member(room, epoch, key32, pub)
