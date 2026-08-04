"""test_simplex_integrity_ed25519.py — per-identity Ed25519 签名升级测试

不连真 SimpleX 服务器:mock runtime + chat_items,用 tmp 目录隔离 _db_prefix,
覆盖身份密钥隔离 / TOFU 防降级 / Ed25519 签验 / 篡改检测 / 旧 HMAC 向后兼容。

跑法:`python -m unittest test_simplex_integrity_ed25519.py -v`
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import simplex_integrity as si  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402


def _mk_rt(db_prefix: str, texts: list[str] | None = None, display: str = "me"):
    """构造一个最小可用 runtime mock:线程活着、可解析联系人、chat_items 返回给定文本。

    display 同时写入 _display_name(签名 sender 的真实来源,修复后不再读 status().active_user)
    和 status()["active_user"](保留以便其他路径);改 ID 走 _display_name,故 sender 须随它变。
    """
    rt = mock.Mock()
    rt._thread.is_alive.return_value = True
    rt._db_prefix = db_prefix
    rt._display_name = display
    rt._file_download_dir = str(Path(db_prefix).parent / "downloads")
    rt.resolve_contact.side_effect = lambda c: {"contact_id": 7, "display_name": str(c)}
    rt.status.return_value = {"active_user": display}
    rt.chat_items.side_effect = lambda cid, limit=60: [{"text": t, "dir": "them"} for t in (texts or [])]
    return rt


def _payload(name: str, digest: str, size: int, sender: str) -> str:
    return f"{name}|{digest}|{size}|{sender}"


class TestIdentityKey(unittest.TestCase):
    """身份密钥:首次生成 + 0600 + 幂等不覆盖 + 按 _db_prefix 隔离。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_first_create_and_idempotent(self):
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        k1 = si._load_or_create_identity(rt)
        key_path, pub_path = si._identity_key_paths(rt)
        self.assertTrue(key_path.exists())
        self.assertTrue(pub_path.exists())
        self.assertEqual(len(key_path.read_bytes()), 32)
        # 0600(POSIX 语义;Windows os.chmod 仅只读位,尽力而为不强制断言位值)
        k2 = si._load_or_create_identity(rt)
        # 幂等:重入读出同一把私钥(公钥相同)
        self.assertEqual(k1.public_key().public_bytes_raw(), k2.public_key().public_bytes_raw())

    def test_db_prefix_isolation_no_env(self):
        """bob 铁证:env 完全无 DM_DB_PREFIX/DM_IDENTITY/SECUREDM_INSTANCE,两个不同
        _db_prefix 的 runtime 必须隔离出**不同**身份密钥(防 oiagent/bob 串钥)。"""
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("DM_DB_PREFIX", "DM_IDENTITY", "SECUREDM_INSTANCE")}
        with mock.patch.dict(os.environ, clean, clear=True):
            rt_a = _mk_rt(str(self.root / "a" / "oiagent_simplex"))
            rt_b = _mk_rt(str(self.root / "b" / "bob_simplex"))
            ka = si._load_or_create_identity(rt_a)
            kb = si._load_or_create_identity(rt_b)
            self.assertNotEqual(ka.public_key().public_bytes_raw(), kb.public_key().public_bytes_raw())
            # 路径也确实按 _db_prefix 隔离
            pa, _ = si._identity_key_paths(rt_a)
            pb, _ = si._identity_key_paths(rt_b)
            self.assertNotEqual(pa, pb)

    def test_pubkey_b64_and_fingerprint(self):
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        pub = si._identity_pubkey_b64(rt)
        self.assertEqual(len(base64.b64decode(pub)), 32)
        fp = si._identity_fingerprint(rt)
        self.assertEqual(len(fp), 16)
        expect = hashlib.sha256(base64.b64decode(pub)).hexdigest()[:16]
        self.assertEqual(fp, expect)


class TestTrustConsume(unittest.TestCase):
    """trust 消息消费:合法公钥存入 / TOFU 防降级拒绝覆盖并告警。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.rt = _mk_rt(str(self.root / "db" / "alice_simplex"))

    def _trust_msg(self, pub_b64: str, identity: str = "peer") -> str:
        return f"{si._MANIFEST_PREFIX}trust " + json.dumps(
            {"v": 1, "algorithm": "Ed25519", "pubkey": pub_b64,
             "fp": hashlib.sha256(base64.b64decode(pub_b64)).hexdigest()[:16],
             "identity": identity})

    def test_consume_stores_pubkey(self):
        peer = Ed25519PrivateKey.generate()
        pub_b64 = base64.b64encode(peer.public_key().public_bytes_raw()).decode()
        r = si._consume_trust_message(self.rt, 7, self._trust_msg(pub_b64))
        self.assertTrue(r["ok"])
        self.assertIsNone(r["alert"])
        self.assertEqual(si._pubkey_for(self.rt, 7), pub_b64)

    def test_tofu_rejects_different_pubkey(self):
        """同 cid 换一个**不同**公钥 → 必须拒绝覆盖 + 显式告警(红线 3,防中间人换绑)。"""
        p1 = Ed25519PrivateKey.generate()
        p2 = Ed25519PrivateKey.generate()
        b1 = base64.b64encode(p1.public_key().public_bytes_raw()).decode()
        b2 = base64.b64encode(p2.public_key().public_bytes_raw()).decode()
        si._consume_trust_message(self.rt, 7, self._trust_msg(b1))
        r = si._consume_trust_message(self.rt, 7, self._trust_msg(b2, identity="peer2"))
        self.assertFalse(r["ok"])
        self.assertIsNotNone(r["alert"])
        # 未被覆盖,仍是第一把
        self.assertEqual(si._pubkey_for(self.rt, 7), b1)

    def test_tofu_same_pubkey_idempotent(self):
        p1 = Ed25519PrivateKey.generate()
        b1 = base64.b64encode(p1.public_key().public_bytes_raw()).decode()
        si._consume_trust_message(self.rt, 7, self._trust_msg(b1))
        r = si._consume_trust_message(self.rt, 7, self._trust_msg(b1))
        self.assertTrue(r["ok"])
        self.assertIsNone(r["alert"])

    def test_non_trust_message_returns_none(self):
        self.assertIsNone(si._consume_trust_message(self.rt, 7, "hello"))
        self.assertIsNone(si._consume_trust_message(self.rt, 7, f"{si._MANIFEST_PREFIX}file {{}}"))


class TestEd25519SignVerify(unittest.TestCase):
    """端到端签验 + 篡改检测 + 换绑攻击 + 旧 HMAC 向后兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = b"hello-ed25519-file-body"
        self.fpath = self.root / "payload.bin"
        self.fpath.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.size = len(self.data)
        # 对端(发送方)身份
        self.peer_priv = Ed25519PrivateKey.generate()
        self.peer_pub_b64 = base64.b64encode(self.peer_priv.public_key().public_bytes_raw()).decode()
        self.sender = "peer-agent"
        # 本端 runtime(验证方),先固定对方公钥
        self.rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        si._set_pubkey(self.rt, 7, self.peer_pub_b64, "peer")

    def _manifest(self, sig: str, pub: str | None = None, alg: str = "Ed25519") -> dict:
        return {"v": 2, "algorithm": alg, "pubkey": pub if pub is not None else self.peer_pub_b64,
                "file": "payload.bin", "sha256": self.digest, "size": self.size,
                "sender": self.sender, "sig": sig, "ts": 0}

    def _file_msg(self, manifest: dict) -> str:
        return f"{si._MANIFEST_PREFIX}file {json.dumps(manifest, ensure_ascii=False)}"

    def _run_verify(self, texts: list[str]):
        self.rt.chat_items.side_effect = lambda cid, limit=60: [{"text": t, "dir": "them"} for t in texts]
        with mock.patch.object(si, "_runtime", return_value=self.rt):
            return si.simplex_verify_received_file("peer", str(self.fpath), timeout=0.1)

    def test_sign_verify_roundtrip(self):
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        r = self._run_verify([self._file_msg(self._manifest(sig))])
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["output"]["verified"], r)
        self.assertTrue(r["output"]["signature_valid"])
        self.assertEqual(r["output"]["algorithm"], "Ed25519")

    def test_tampered_file_content_fails(self):
        # 先签原文件,再改文件内容(hash 变)→ hash_match=False → verified=False
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        m = self._manifest(sig)
        self.fpath.write_bytes(b"TAMPERED")
        r = self._run_verify([self._file_msg(m)])
        self.assertTrue(r["ok"])
        self.assertFalse(r["output"]["hash_match"])
        self.assertFalse(r["output"]["verified"])

    def test_tampered_sig_fails(self):
        # 换一个对别的 payload 的签名 → signature_valid=False
        payload = _payload("payload.bin", self.digest, self.size, "someone-else")
        bad_sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        r = self._run_verify([self._file_msg(self._manifest(bad_sig))])
        self.assertTrue(r["ok"])
        self.assertFalse(r["output"]["signature_valid"])
        self.assertFalse(r["output"]["verified"])

    def test_manifest_pubkey_mismatch_alerts(self):
        """manifest 自清 pubkey 与固定公钥不符 → 疑似换绑,signature_valid=False + alert。"""
        evil = Ed25519PrivateKey.generate()
        evil_pub = base64.b64encode(evil.public_key().public_bytes_raw()).decode()
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        # 攻击者用自己的私钥签,塞自己的公钥进 manifest
        sig = base64.b64encode(evil.sign(payload.encode())).decode()
        r = self._run_verify([self._file_msg(self._manifest(sig, pub=evil_pub))])
        self.assertTrue(r["ok"])
        self.assertFalse(r["output"]["signature_valid"])
        self.assertFalse(r["output"]["verified"])
        self.assertIn("alert", r["output"])

    def test_missing_peer_pubkey_diagnosable(self):
        # 全新 runtime,没固定对方公钥 → 可诊断错误而非异常
        rt2 = _mk_rt(str(self.root / "db2" / "carol_simplex"))
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        m = self._manifest(sig)
        rt2.chat_items.side_effect = lambda cid, limit=60: [{"text": self._file_msg(m), "dir": "them"}]
        with mock.patch.object(si, "_runtime", return_value=rt2):
            r = si.simplex_verify_received_file("peer", str(self.fpath), timeout=0.1)
        self.assertFalse(r["ok"])
        self.assertIn("公钥", r["diagnosable"])

    def test_trust_consumed_during_verify(self):
        """被动消费:聊天历史里带 trust 公钥消息,验证时顺带固定,随即用它验签成功。"""
        rt3 = _mk_rt(str(self.root / "db3" / "dave_simplex"))
        trust = f"{si._MANIFEST_PREFIX}trust " + json.dumps(
            {"v": 1, "algorithm": "Ed25519", "pubkey": self.peer_pub_b64,
             "fp": hashlib.sha256(base64.b64decode(self.peer_pub_b64)).hexdigest()[:16],
             "identity": "peer"})
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        m = self._manifest(sig)
        rt3.chat_items.side_effect = lambda cid, limit=60: [{"text": trust, "dir": "them"}, {"text": self._file_msg(m), "dir": "them"}]
        with mock.patch.object(si, "_runtime", return_value=rt3):
            r = si.simplex_verify_received_file("peer", str(self.fpath), timeout=0.1)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["output"]["verified"], r)

    def test_legit_rekey_via_trust_announcement_accepted(self):
        """合法换钥:对方换了身份钥,经 E2E 发 trust 公告新钥,manifest 用新钥签。
        verify 应识别 trust 公告(钥 == manifest 钥)→ 接受换绑 → 按新钥验签通过(非中间人)。"""
        rt = _mk_rt(str(self.root / "db" / "rekey_simplex"))
        # 已固定旧钥
        old_priv = Ed25519PrivateKey.generate()
        old_pub = base64.b64encode(old_priv.public_key().public_bytes_raw()).decode()
        si._set_pubkey(rt, 7, old_pub, "peer")
        # 对方换钥,用新钥签 manifest,并在聊天里发 trust 公告新钥
        new_priv = Ed25519PrivateKey.generate()
        new_pub = base64.b64encode(new_priv.public_key().public_bytes_raw()).decode()
        trust = f"{si._MANIFEST_PREFIX}trust " + json.dumps(
            {"v": 1, "algorithm": "Ed25519", "pubkey": new_pub,
             "fp": hashlib.sha256(base64.b64decode(new_pub)).hexdigest()[:16], "identity": "peer"})
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(new_priv.sign(payload.encode())).decode()
        m = self._manifest(sig, pub=new_pub)
        rt.chat_items.side_effect = lambda cid, limit=60: [{"text": trust, "dir": "them"}, {"text": self._file_msg(m), "dir": "them"}]
        with mock.patch.object(si, "_runtime", return_value=rt):
            r = si.simplex_verify_received_file("peer", str(self.fpath), timeout=0.1)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["output"]["signature_valid"], r)
        self.assertTrue(r["output"]["verified"], r)
        # 换绑已生效:固定钥更新为新钥
        self.assertEqual(si._pubkey_for(rt, 7), new_pub)

    def test_own_echo_trust_not_consumed_as_peer_key(self):
        """回归:同一对话里本端自己发出的 trust 公告(dir="me")绝不能被当作对方公钥消费。

        真实 bug("两窗口交换发文件都来自 bob"):verify 用 chat_items 取回**双向**消息,
        旧代码对每条 trust 都消费,本端自己的回声(dir=me)也进 pin。bob 的公告 id 更靠后
        → 覆盖 oiagent 的钥 → oiagent 侧把 bob 的钥钉成"对方公钥",两侧 pin 都是 bob 的钥,
        manifest 又恰好用它签 → 双向 verify 都"通过"且 sender=bob。
        修复:只消费 dir=="them" 的 trust/manifest。本测试:them(对方钥) + me(自己回声)
        同会话,且 me 排后(最易被误钉的次序)—— pin 必须是对方钥,verify 必须 verified=True
        且 sender 为对方。"""
        rt = _mk_rt(str(self.root / "db" / "echo_simplex"))
        # 本端自己的身份钥(回声里携带,绝不能进 pin)
        own_priv = Ed25519PrivateKey.generate()
        own_pub = base64.b64encode(own_priv.public_key().public_bytes_raw()).decode()
        own_echo = f"{si._MANIFEST_PREFIX}trust " + json.dumps(
            {"v": 1, "algorithm": "Ed25519", "pubkey": own_pub,
             "fp": hashlib.sha256(base64.b64decode(own_pub)).hexdigest()[:16], "identity": "me-self"})
        # 对方(peer)的 trust + 用对方钥签的 manifest
        peer_trust = f"{si._MANIFEST_PREFIX}trust " + json.dumps(
            {"v": 1, "algorithm": "Ed25519", "pubkey": self.peer_pub_b64,
             "fp": hashlib.sha256(base64.b64decode(self.peer_pub_b64)).hexdigest()[:16],
             "identity": self.sender})
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = base64.b64encode(self.peer_priv.sign(payload.encode())).decode()
        m = self._manifest(sig)
        # dir=me 的自己回声排在 dir=them 之后(按 ts 升序,旧代码会被它覆盖)
        rt.chat_items.side_effect = lambda cid, limit=60: [
            {"text": peer_trust, "dir": "them"},
            {"text": self._file_msg(m), "dir": "them"},
            {"text": own_echo, "dir": "me"},
        ]
        # 预清 pin,模拟首次
        with mock.patch.object(si, "_runtime", return_value=rt):
            r = si.simplex_verify_received_file("peer", str(self.fpath), timeout=0.1)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["output"]["verified"], r)
        self.assertEqual(r["output"]["sender"], self.sender, r)
        # pin 必须是对方钥,而非自己的回声钥
        self.assertEqual(si._pubkey_for(rt, 7), self.peer_pub_b64)
        self.assertNotEqual(si._pubkey_for(rt, 7), own_pub)

    def test_old_hmac_manifest_backward_compatible(self):
        """旧 HMAC manifest(algorithm="HMAC-SHA256")仍按旧 _trust_key_for 路径验(红线 4)。"""
        key = "ab" * 32  # 64 hex
        payload = _payload("payload.bin", self.digest, self.size, self.sender)
        sig = _hmac.new(bytes.fromhex(key), payload.encode(), hashlib.sha256).hexdigest()
        m = {"v": 1, "algorithm": "HMAC-SHA256", "file": "payload.bin",
             "sha256": self.digest, "size": self.size, "sender": self.sender, "sig": sig, "ts": 0}
        with mock.patch.object(si, "_trust_key_for", return_value=key):
            r = self._run_verify([self._file_msg(m)])
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["output"]["verified"], r)
        self.assertEqual(r["output"]["algorithm"], "HMAC-SHA256")


class TestSendFileSigned(unittest.TestCase):
    """send_file_signed 产出 Ed25519 manifest(algorithm/pubkey 字段在)。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fpath = self.root / "doc.txt"
        self.fpath.write_bytes(b"doc-body")

    def test_manifest_has_ed25519_fields(self):
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        sent_texts: list[str] = []

        def fake_call(name, args):
            if name == "simplex_send_file":
                return {"ok": True, "output": {}}
            if name == "simplex_send_message":
                sent_texts.append(args["text"])
                return {"ok": True, "output": {}}
            return {"ok": True, "output": {}}

        with mock.patch.object(si, "_runtime", return_value=rt), \
             mock.patch.object(si._st, "call_tool", side_effect=fake_call), \
             mock.patch.object(si._sf, "call_tool", side_effect=fake_call), \
             mock.patch.object(si, "policy_check_daemon", return_value=("allow", "")):
            r = si.simplex_send_file_signed("peer", str(self.fpath))
        self.assertTrue(r["ok"], r)
        # 找到 file manifest 那条消息
        file_msgs = [t for t in sent_texts if t.startswith(f"{si._MANIFEST_PREFIX}file ")]
        self.assertTrue(file_msgs, "应发出一条 file manifest")
        m = json.loads(file_msgs[0][len(f"{si._MANIFEST_PREFIX}file "):])
        self.assertEqual(m["algorithm"], "Ed25519")
        self.assertIn("pubkey", m)
        self.assertEqual(len(base64.b64decode(m["pubkey"])), 32)
        self.assertIn("sig", m)
        # manifest 里绝不含私钥材料(红线 2):只有 32 字节公钥 b64,无 64 字节私钥 b64
        self.assertNotIn("privkey", m)
        self.assertNotIn("private", m)


class TestSenderIdentitySync(unittest.TestCase):
    """sender 身份必须取 runtime 当前显示名(改 ID 实时同步),不能停在 setup 时 argv 身份。

    复现用户 bug:发送方把窗口改成「窗口2」,接收端却显示「已验证来自 窗口1」——
    根因是 sender 取 rt.status().active_user(SimpleX profile,改名不更新)。
    修复后 sender 取 rt._display_name(改名实时更新)。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_current_sender_uses_display_name_not_stale_profile(self):
        # 模拟:setup 时 argv 身份是"窗口1"(写进 SimpleX profile),用户改 ID 成"窗口2"。
        # _display_name 被 update_display_name 实时改为"窗口2",但 status().active_user 仍是旧的"窗口1"。
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        rt._display_name = "窗口2"
        rt.status.return_value = {"active_user": "窗口1"}  # SimpleX profile 停在启动身份
        self.assertEqual(si._current_sender(rt), "窗口2")

    def test_current_sender_fallback_when_display_empty(self):
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        rt._display_name = ""
        rt.status.return_value = {"active_user": "窗口1"}
        self.assertEqual(si._current_sender(rt), "agent")

    def test_send_manifest_sender_tracks_custom_id(self):
        """发文件 manifest 的 sender 字段 = 当前自定义 ID,不是 setup 启动身份。"""
        rt = _mk_rt(str(self.root / "db" / "alice_simplex"))
        rt._display_name = "窗口2"
        rt.status.return_value = {"active_user": "窗口1"}  # 启动身份(陈旧)
        fpath = self.root / "doc.txt"
        fpath.write_bytes(b"doc-body")
        sent_texts: list[str] = []

        def fake_call(name, args):
            if name == "simplex_send_message":
                sent_texts.append(args["text"])
            return {"ok": True, "output": {}}

        with mock.patch.object(si, "_runtime", return_value=rt), \
             mock.patch.object(si._st, "call_tool", side_effect=fake_call), \
             mock.patch.object(si._sf, "call_tool", side_effect=fake_call), \
             mock.patch.object(si, "policy_check_daemon", return_value=("allow", "")):
            r = si.simplex_send_file_signed("peer", str(fpath))
        self.assertTrue(r["ok"], r)
        file_msgs = [t for t in sent_texts if t.startswith(f"{si._MANIFEST_PREFIX}file ")]
        self.assertTrue(file_msgs)
        m = json.loads(file_msgs[0][len(f"{si._MANIFEST_PREFIX}file "):])
        self.assertEqual(m["sender"], "窗口2")  # 接收端将显示「已验证来自 窗口2」


class TestCrossKeyGuard(unittest.TestCase):
    """防串钥:db_prefix 缺失拒绝 + 属主不符拒绝复用他实例密钥。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_empty_db_prefix_refused_no_silent_shared_fallback(self):
        """db_prefix 与 env 全空 → 身份密钥必须 raise,绝不静默落到共享默认目录(串钥根)。"""
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("DM_DB_PREFIX", "DM_IDENTITY", "SECUREDM_INSTANCE")}
        rt = _mk_rt("")  # 空 db_prefix
        with mock.patch.dict(os.environ, clean, clear=True):
            with self.assertRaises(RuntimeError):
                si._load_or_create_identity(rt)

    def test_owner_mismatch_refuses_foreign_key(self):
        """目录里已有为 prefix-A 生成的密钥,当前实例是 prefix-B(同父目录)→ 拒绝复用。"""
        shared_parent = self.root / "shared"
        prefix_a = str(shared_parent / "alice_simplex")
        prefix_b = str(shared_parent / "bob_simplex")
        # A 先生成(同父目录)
        rt_a = _mk_rt(prefix_a)
        si._load_or_create_identity(rt_a)
        # B 同父目录,试图加载 → 属主不符必须拒绝
        rt_b = _mk_rt(prefix_b)
        with self.assertRaises(RuntimeError):
            si._load_or_create_identity(rt_b)

    def test_owner_match_loads_own_key(self):
        """同 prefix 重入正常加载(owner 匹配),且两把不同 prefix 的钥确实不同。"""
        pa = str(self.root / "a" / "alice_simplex")
        rt1 = _mk_rt(pa)
        k1 = si._load_or_create_identity(rt1)
        rt2 = _mk_rt(pa)
        k2 = si._load_or_create_identity(rt2)
        self.assertEqual(k1.public_key().public_bytes_raw(), k2.public_key().public_bytes_raw())

    def test_legacy_key_without_owner_gets_registered(self):
        """历史遗留:有密钥但无 owner 文件 → 不拒绝,补登记当前 prefix,正常加载。"""
        prefix = str(self.root / "db" / "alice_simplex")
        rt = _mk_rt(prefix)
        k1 = si._load_or_create_identity(rt)
        # 删掉 owner 模拟旧安装
        owner = si._identity_owner_path(rt)
        if owner.exists():
            owner.unlink()
        k2 = si._load_or_create_identity(rt)  # 应正常加载并补登记
        self.assertEqual(k1.public_key().public_bytes_raw(), k2.public_key().public_bytes_raw())
        self.assertTrue(owner.exists())
        self.assertEqual(owner.read_text(encoding="utf-8").strip(), prefix)


if __name__ == "__main__":
    unittest.main()
