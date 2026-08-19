//! 会话:混合 KEM 握手 + 双向 KDF 链,seal/open 的整合点。
//!
//! 握手(无交互往返——异步 IM 场景,一条握手消息即可):
//!   发起方:生成 X25519 临时对 + 封装到对端 ML-KEM 公钥
//!          shared = HKDF(x25519_ss ‖ mlkem_ss)
//!          用身份私钥签名所有握手公钥,防中间人
//!   接收方:验签 -> 解封 -> 重建 shared -> 链就�的
//!
//! 握手产物分出两条链(发起方收 / 发起方发),靠 info 域分离。

use chacha20poly1305::{
    aead::{Aead, KeyInit},
    XChaCha20Poly1305, XNonce,
};
use hkdf::Hkdf;
use ml_kem::{
    kem::{Decapsulate, Encapsulate},
    KeyExport, MlKem768, TryKeyInit,
};
use getrandom::{
    rand_core::{TryRng, UnwrapErr},
    SysRng,
};
use sha2::Sha256;
use x25519_dalek::{PublicKey as XPublicKey, StaticSecret};
use zeroize::{Zeroize, ZeroizeOnDrop};

use crate::error::ConduitError;
use crate::identity::{verify_signature, Identity};
use crate::ratchet::{Chain, RecvChain};
use crate::wire;

type MlKemEk = ml_kem::EncapsulationKey<MlKem768>;
type MlKemDk = ml_kem::DecapsulationKey<MlKem768>;
type MlKemCt = ml_kem::kem::Ciphertext<MlKem768>;
type MlKemShared = ml_kem::SharedKey;

const INFO_ROOT: &[u8] = b"crypto-conduit root v1";
const INFO_SEND: &[u8] = b"crypto-conduit send v1";
const INFO_RECV: &[u8] = b"crypto-conduit recv v1";

/// 对端的长期公钥材料(握手前需带外获得并信任,TOFU)。
#[derive(Clone)]
pub struct PeerPublic {
    pub identity_pk: [u8; 32],
    pub mlkem_ek: Vec<u8>,
    pub x25519_pk: [u8; 32],
}

impl PeerPublic {
    /// 序列化:[identity_pk:32][ek_len:2 LE][ek][x25519_pk:32]
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut v = Vec::with_capacity(32 + 2 + self.mlkem_ek.len() + 32);
        v.extend_from_slice(&self.identity_pk);
        v.extend_from_slice(&(self.mlkem_ek.len() as u16).to_le_bytes());
        v.extend_from_slice(&self.mlkem_ek);
        v.extend_from_slice(&self.x25519_pk);
        v
    }

    pub fn from_bytes(b: &[u8]) -> Result<Self, ConduitError> {
        if b.len() < 32 + 2 {
            return Err(ConduitError::MalformedWire("peer too short"));
        }
        let mut identity_pk = [0u8; 32];
        identity_pk.copy_from_slice(&b[0..32]);
        let ek_len = u16::from_le_bytes([b[32], b[33]]) as usize;
        let need = 32 + 2 + ek_len + 32;
        if b.len() != need {
            return Err(ConduitError::MalformedWire("peer bad len"));
        }
        let mlkem_ek = b[34..34 + ek_len].to_vec();
        let mut x25519_pk = [0u8; 32];
        x25519_pk.copy_from_slice(&b[34 + ek_len..need]);
        Ok(Self {
            identity_pk,
            mlkem_ek,
            x25519_pk,
        })
    }
}

/// 我方长期密钥材料(每个身份一份,持久化)。
pub struct LocalKeys {
    pub identity: Identity,
    pub mlkem_dk: MlKemDk,
    pub x25519: StaticSecret,
}

impl LocalKeys {
    pub fn generate(identity: Identity) -> Self {
        let mut rng = UnwrapErr(SysRng);
        let (mlkem_dk, _ek) = <MlKem768 as ml_kem::Kem>::generate_keypair_from_rng(&mut rng);
        let x25519 = StaticSecret::random_from_rng(&mut rng);
        Self {
            identity,
            mlkem_dk,
            x25519,
        }
    }

    pub fn public(&self) -> PeerPublic {
        PeerPublic {
            identity_pk: self.identity.public_key(),
            mlkem_ek: self.mlkem_dk.encapsulation_key().to_bytes().to_vec(),
            x25519_pk: XPublicKey::from(&self.x25519).to_bytes(),
        }
    }

    /// 导出种子供持久化:[identity_seed:32][mlkem_seed:64][x25519:32] = 128B。
    /// 调用方负责落盘后的保密(密钥材料记忆隔离纪律)。
    pub fn to_seed(&self) -> Option<Vec<u8>> {
        let mut v = Vec::with_capacity(128);
        v.extend_from_slice(&self.identity.to_seed());
        v.extend_from_slice(self.mlkem_dk.to_seed()?.as_slice());
        v.extend_from_slice(&self.x25519.to_bytes());
        Some(v)
    }

    /// 从 128B 种子恢复。
    pub fn from_seed(b: &[u8]) -> Result<Self, ConduitError> {
        if b.len() != 128 {
            return Err(ConduitError::MalformedWire("localkeys seed != 128"));
        }
        let mut id_seed = [0u8; 32];
        id_seed.copy_from_slice(&b[0..32]);
        let mut kem_seed = [0u8; 64];
        kem_seed.copy_from_slice(&b[32..96]);
        let mut x_seed = [0u8; 32];
        x_seed.copy_from_slice(&b[96..128]);
        Ok(Self {
            identity: Identity::from_seed(id_seed),
            mlkem_dk: MlKemDk::from_seed(kem_seed.into()),
            x25519: StaticSecret::from(x_seed),
        })
    }
}

/// 握手消息(发起方 -> 接收方)。自包含,一条即可建会话。
pub struct HandshakeMsg {
    pub x25519_ephemeral: [u8; 32],
    pub mlkem_ct: Vec<u8>,
    pub identity_pk: [u8; 32],
    pub signature: [u8; 64],
}

impl HandshakeMsg {
    /// 待签名内容:所有握手公钥拼接。
    fn signable(&self) -> Vec<u8> {
        let mut v = Vec::new();
        v.extend_from_slice(&self.x25519_ephemeral);
        v.extend_from_slice(&self.mlkem_ct);
        v
    }

    /// 序列化:[x25519:32][ct_len:2 LE][ct][identity_pk:32][sig:64]
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut v = Vec::with_capacity(32 + 2 + self.mlkem_ct.len() + 32 + 64);
        v.extend_from_slice(&self.x25519_ephemeral);
        v.extend_from_slice(&(self.mlkem_ct.len() as u16).to_le_bytes());
        v.extend_from_slice(&self.mlkem_ct);
        v.extend_from_slice(&self.identity_pk);
        v.extend_from_slice(&self.signature);
        v
    }

    pub fn from_bytes(b: &[u8]) -> Result<Self, ConduitError> {
        if b.len() < 32 + 2 {
            return Err(ConduitError::MalformedWire("hs too short"));
        }
        let mut x25519_ephemeral = [0u8; 32];
        x25519_ephemeral.copy_from_slice(&b[0..32]);
        let ct_len = u16::from_le_bytes([b[32], b[33]]) as usize;
        let need = 32 + 2 + ct_len + 32 + 64;
        if b.len() != need {
            return Err(ConduitError::MalformedWire("hs bad len"));
        }
        let mlkem_ct = b[34..34 + ct_len].to_vec();
        let mut identity_pk = [0u8; 32];
        identity_pk.copy_from_slice(&b[34 + ct_len..34 + ct_len + 32]);
        let mut signature = [0u8; 64];
        signature.copy_from_slice(&b[34 + ct_len + 32..need]);
        Ok(Self {
            x25519_ephemeral,
            mlkem_ct,
            identity_pk,
            signature,
        })
    }
}

/// 已建立的会话。发链 + 收链。
#[derive(Zeroize, ZeroizeOnDrop)]
pub struct Session {
    send: Chain,
    #[zeroize(skip)]
    recv: RecvChain,
}

/// 会话角色:决定双向链种子的配对方向。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Role {
    Initiator,
    Responder,
}

impl Session {
    /// 发起方:用对端公钥建会话,产出 (会话, 握手消息)。
    pub fn initiate(
        my_id: &Identity,
        peer: &PeerPublic,
    ) -> Result<(Self, HandshakeMsg), ConduitError> {
        let mut rng = UnwrapErr(SysRng);

        // X25519 临时 DH
        let eph = StaticSecret::random_from_rng(&mut rng);
        let eph_pub = XPublicKey::from(&eph);
        let x_ss = eph
            .diffie_hellman(&XPublicKey::from(peer.x25519_pk))
            .to_bytes();

        // ML-KEM 封装到对端公钥
        let ek = MlKemEk::new_from_slice(&peer.mlkem_ek)
            .map_err(|_| ConduitError::MalformedWire("bad mlkem ek len"))?;
        let (mlkem_ct, mlkem_ss): (MlKemCt, MlKemShared) = ek.encapsulate_with_rng(&mut rng);

        let root = derive_root(&x_ss, mlkem_ss.as_slice());

        let mut hs = HandshakeMsg {
            x25519_ephemeral: eph_pub.to_bytes(),
            mlkem_ct: mlkem_ct.to_vec(),
            identity_pk: my_id.public_key(),
            signature: [0u8; 64],
        };
        hs.signature = my_id.sign(&hs.signable());

        let sess = Self::from_root(&root, Role::Initiator);
        Ok((sess, hs))
    }

    /// 接收方:从握手消息恢复会话。
    pub fn accept(my: &LocalKeys, hs: &HandshakeMsg) -> Result<Self, ConduitError> {
        // 验签:确认握手出自声称的身份
        verify_signature(&hs.identity_pk, &hs.signable(), &hs.signature)?;

        // X25519
        let x_ss = my
            .x25519
            .diffie_hellman(&XPublicKey::from(hs.x25519_ephemeral))
            .to_bytes();

        // ML-KEM 解封
        let mlkem_ss: MlKemShared = my
            .mlkem_dk
            .decapsulate_slice(&hs.mlkem_ct)
            .map_err(|_| ConduitError::MalformedWire("bad mlkem ct len"))?;

        let root = derive_root(&x_ss, mlkem_ss.as_slice());
        Ok(Self::from_root(&root, Role::Responder))
    }

    /// 由根密钥按角色建双向链。同一 root,两端角色互补:
    ///   Initiator 的发送链种子 == Responder 的接收链种子,反之亦然。
    fn from_root(root: &[u8; 32], role: Role) -> Self {
        let (send_seed, recv_seed) = derive_chain_seeds(root);
        let (send_seed, recv_seed) = match role {
            Role::Initiator => (send_seed, recv_seed),
            Role::Responder => (recv_seed, send_seed),
        };
        Self {
            send: Chain::new(&send_seed),
            recv: RecvChain::new(&recv_seed),
        }
    }

    /// 加密一条明文 -> wire 包。
    pub fn seal(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, ConduitError> {
        let seq = self.send.next_seq();
        let mk = self.send.ratchet();

        let cipher = XChaCha20Poly1305::new_from_slice(&mk)
            .map_err(|_| ConduitError::Internal("aead key".into()))?;
        let mut nonce_bytes = [0u8; 24];
        UnwrapErr(SysRng).try_fill_bytes(&mut nonce_bytes).expect("OS rng");
        let nonce = XNonce::from_slice(&nonce_bytes);

        // AEAD 正文 = 填充后的明文(seq 已在 wire 头,无需内嵌)
        let body = wire::pad(plaintext);

        let ct = cipher
            .encrypt(nonce, body.as_ref())
            .map_err(|_| ConduitError::Internal("aead enc".into()))?;

        // wire: [ver][suite][flags=0][seq:8B][nonce][ct]
        // seq 明文置于头部(非秘密,泄露无妨),接收方据此精确定位消息密钥。
        let mut out = Vec::with_capacity(3 + 8 + 24 + ct.len());
        out.push(wire::WIRE_VERSION);
        out.push(wire::SUITE_X25519_MLKEM768);
        out.push(0u8);
        out.extend_from_slice(&seq.to_le_bytes());
        out.extend_from_slice(&nonce_bytes);
        out.extend_from_slice(&ct);
        Ok(out)
    }

    /// 解密 wire 包 -> 明文。
    pub fn open(&mut self, blob: &[u8]) -> Result<Vec<u8>, ConduitError> {
        if blob.len() < 3 + 8 + 24 + 16 {
            return Err(ConduitError::MalformedWire("too short"));
        }
        if blob[0] != wire::WIRE_VERSION {
            return Err(ConduitError::UnknownVersion(blob[0]));
        }
        if blob[1] != wire::SUITE_X25519_MLKEM768 {
            return Err(ConduitError::UnknownSuite(blob[1]));
        }
        let seq = u64::from_le_bytes(
            blob[3..11]
                .try_into()
                .map_err(|_| ConduitError::MalformedWire("bad seq"))?,
        );
        let nonce = XNonce::from_slice(&blob[11..35]);
        let ct = &blob[35..];

        // 用头部明文 seq 精确定位消息密钥(处理乱序/跳消息缓存)。
        let mk = self.recv.msg_key(seq)?;
        let cipher = XChaCha20Poly1305::new_from_slice(&mk)
            .map_err(|_| ConduitError::Internal("aead key".into()))?;
        let body = cipher
            .decrypt(nonce, ct)
            .map_err(|_| ConduitError::DecryptFailed)?;
        wire::unpad(&body)
    }
}

fn derive_root(x_ss: &[u8; 32], mlkem_ss: &[u8]) -> [u8; 32] {
    let mut ikm = Vec::with_capacity(64);
    ikm.extend_from_slice(x_ss);
    ikm.extend_from_slice(mlkem_ss);
    let hk = Hkdf::<Sha256>::new(None, &ikm);
    let mut root = [0u8; 32];
    hk.expand(INFO_ROOT, &mut root).expect("HKDF 32B");
    ikm.zeroize();
    root
}

/// 从根分出 (发起方发送链, 发起方接收链) 的种子。
/// 从根分出两条链的种子 (initiator_send_seed, initiator_recv_seed)。
/// 两端各自据此建链,方向相反(见 Session::from_root)。
fn derive_chain_seeds(root: &[u8; 32]) -> ([u8; 32], [u8; 32]) {
    let hk = Hkdf::<Sha256>::new(None, root);
    let mut s = [0u8; 32];
    let mut r = [0u8; 32];
    hk.expand(INFO_SEND, &mut s).expect("HKDF 32B");
    hk.expand(INFO_RECV, &mut r).expect("HKDF 32B");
    (s, r)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Pair {
        alice: Session,
        bob: Session,
    }

    fn establish() -> Pair {
        let alice_id = Identity::generate();
        let bob_keys = LocalKeys::generate(Identity::generate());
        let bob_pub = bob_keys.public();

        let (alice, hs) = Session::initiate(&alice_id, &bob_pub).unwrap();
        let bob = Session::accept(&bob_keys, &hs).unwrap();
        Pair { alice, bob }
    }

    #[test]
    fn handshake_and_roundtrip() {
        let mut p = establish();
        let blob = p.alice.seal(b"hello conduit").unwrap();
        let back = p.bob.open(&blob).unwrap();
        assert_eq!(back, b"hello conduit");
    }

    #[test]
    fn bidirectional() {
        let mut p = establish();
        let m1 = p.alice.seal(b"from alice").unwrap();
        assert_eq!(p.bob.open(&m1).unwrap(), b"from alice");
        let m2 = p.bob.seal(b"from bob").unwrap();
        assert_eq!(p.alice.open(&m2).unwrap(), b"from bob");
    }

    #[test]
    fn out_of_order_delivery() {
        let mut p = establish();
        let b0 = p.alice.seal(b"m0").unwrap();
        let b1 = p.alice.seal(b"m1").unwrap();
        let b2 = p.alice.seal(b"m2").unwrap();
        // 乱序:2 先到,再 0、1
        assert_eq!(p.bob.open(&b2).unwrap(), b"m2");
        assert_eq!(p.bob.open(&b0).unwrap(), b"m0");
        assert_eq!(p.bob.open(&b1).unwrap(), b"m1");
    }

    #[test]
    fn replay_rejected() {
        let mut p = establish();
        let b0 = p.alice.seal(b"m0").unwrap();
        let b1 = p.alice.seal(b"m1").unwrap();
        p.bob.open(&b1).unwrap();
        p.bob.open(&b0).unwrap();
        // 重放 b0 -> 拒绝
        assert!(p.bob.open(&b0).is_err());
    }

    #[test]
    fn tamper_detected() {
        let mut p = establish();
        let mut blob = p.alice.seal(b"integrity").unwrap();
        let n = blob.len();
        blob[n - 1] ^= 0x01; // 翻转 tag 末位
        assert!(p.bob.open(&blob).is_err());
    }

    #[test]
    fn wrong_version_and_suite() {
        let mut p = establish();
        let mut blob = p.alice.seal(b"x").unwrap();
        blob[0] = 0x7F;
        assert!(matches!(
            p.bob.open(&blob),
            Err(ConduitError::UnknownVersion(0x7F))
        ));
        let mut blob2 = p.alice.seal(b"x").unwrap();
        blob2[1] = 0x7E;
        assert!(matches!(
            p.bob.open(&blob2),
            Err(ConduitError::UnknownSuite(0x7E))
        ));
    }

    #[test]
    fn localkeys_seed_roundtrip_preserves_handshake() {
        // 持久化恢复后,握手与加解密能力不变
        let bob_keys = LocalKeys::generate(Identity::generate());
        let seed = bob_keys.to_seed().unwrap();
        let bob_keys2 = LocalKeys::from_seed(&seed).unwrap();
        // 公钥一致
        assert_eq!(
            bob_keys.public().to_bytes(),
            bob_keys2.public().to_bytes()
        );

        // 恢复的密钥能完成握手 + 加解密
        let alice_id = Identity::generate();
        let (mut alice, hs) = Session::initiate(&alice_id, &bob_keys2.public()).unwrap();
        let mut bob = Session::accept(&bob_keys2, &hs).unwrap();
        let blob = alice.seal(b"persisted").unwrap();
        assert_eq!(bob.open(&blob).unwrap(), b"persisted");
    }

    #[test]
    fn peer_and_handshake_serde_roundtrip() {
        let bob_keys = LocalKeys::generate(Identity::generate());
        let pp = bob_keys.public();
        let pp2 = PeerPublic::from_bytes(&pp.to_bytes()).unwrap();
        assert_eq!(pp.to_bytes(), pp2.to_bytes());

        let alice_id = Identity::generate();
        let (_a, hs) = Session::initiate(&alice_id, &pp2).unwrap();
        let hs2 = HandshakeMsg::from_bytes(&hs.to_bytes()).unwrap();
        assert_eq!(hs.to_bytes(), hs2.to_bytes());
    }

    #[test]
    fn length_quantized() {
        let mut p = establish();
        let short = p.alice.seal(b"a").unwrap();
        let longer = p.alice.seal(&vec![0u8; 100]).unwrap();
        // 不同明文长 -> 相同 wire 总长(同桶)
        assert_eq!(short.len(), longer.len());
    }

    #[test]
    fn mitm_wrong_identity_rejected() {
        let alice_id = Identity::generate();
        let bob_keys = LocalKeys::generate(Identity::generate());
        let bob_pub = bob_keys.public();
        let (_alice, mut hs) = Session::initiate(&alice_id, &bob_pub).unwrap();
        // 攻击者替换身份公钥为别人的
        let mallory = Identity::generate();
        hs.identity_pk = mallory.public_key();
        assert!(matches!(
            Session::accept(&bob_keys, &hs),
            Err(ConduitError::BadSignature)
        ));
    }
}
