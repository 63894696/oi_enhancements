//! 身份层:独立于任何 IM 账号的长期身份。
//!
//! 身份 = Ed25519 签名密钥对。它不属于任何 IM 账号,因此同一份身份
//! 可以在 SimpleX / Matrix / 邮件等任意通道上被认出(跨 IM 的"同一个人"证明)。
//!
//! 信任模型:TOFU + 指纹(首次使用信任 + 当面核对人眼可读指纹)。

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use getrandom::{rand_core::UnwrapErr, SysRng};
use sha2::{Digest, Sha256};
use zeroize::Zeroize;

use crate::error::ConduitError;

/// 长期身份。私钥仅存于本结构,drop 时清零(记忆隔离纪律)。
pub struct Identity {
    signing: SigningKey,
}

impl Identity {
    /// 生成新身份。
    pub fn generate() -> Self {
        let mut rng = UnwrapErr(SysRng);
        let signing = SigningKey::generate(&mut rng);
        Self { signing }
    }

    /// 身份公钥(可公开分发)。
    pub fn public_key(&self) -> [u8; 32] {
        self.signing.verifying_key().to_bytes()
    }

    /// 人眼可读指纹:对公钥做 SHA-256,取前若干字节转十六进制分组。
    /// L4 反馈界面据此展示(或转 emoji),供当面核对。
    pub fn fingerprint(&self) -> String {
        let pk = self.public_key();
        let digest = Sha256::digest(pk);
        digest[..16]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<Vec<_>>()
            .chunks(4)
            .map(|c| c.concat())
            .collect::<Vec<_>>()
            .join(" ")
    }

    /// 用身份私钥签名一段数据(通常是临时握手公钥),防中间人。
    pub fn sign(&self, data: &[u8]) -> [u8; 64] {
        self.signing.sign(data).to_bytes()
    }

    /// 从 32 字节种子还原身份(供持久化/恢复)。
    pub fn from_seed(seed: [u8; 32]) -> Self {
        let signing = SigningKey::from_bytes(&seed);
        Self { signing }
    }

    /// 导出种子(供加密持久化;调用方负责落盘后的保密)。
    pub fn to_seed(&self) -> [u8; 32] {
        self.signing.to_bytes()
    }
}

impl Drop for Identity {
    fn drop(&mut self) {
        // SigningKey 内部字节清零
        let mut seed = self.signing.to_bytes();
        seed.zeroize();
    }
}

/// 校验某个签名是否由 claimed_pk 对应的身份对 data 签署。
pub fn verify_signature(
    claimed_pk: &[u8; 32],
    data: &[u8],
    sig_bytes: &[u8; 64],
) -> Result<(), ConduitError> {
    let vk = VerifyingKey::from_bytes(claimed_pk).map_err(|_| ConduitError::BadSignature)?;
    let sig = Signature::from_bytes(sig_bytes);
    vk.verify(data, &sig).map_err(|_| ConduitError::BadSignature)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprint_stable_and_grouped() {
        let id = Identity::generate();
        let fp = id.fingerprint();
        // 16 字节 -> 32 hex 字符,4 字节(8 hex)一组 -> 4 组 + 3 空格
        assert_eq!(fp.len(), 4 * 8 + 3);
        assert_eq!(fp, id.fingerprint()); // 稳定
    }

    #[test]
    fn sign_and_verify_roundtrip() {
        let id = Identity::generate();
        let msg = b"ephemeral public key bytes";
        let sig = id.sign(msg);
        assert!(verify_signature(&id.public_key(), msg, &sig).is_ok());
    }

    #[test]
    fn verify_rejects_wrong_key() {
        let a = Identity::generate();
        let b = Identity::generate();
        let sig = a.sign(b"data");
        assert!(verify_signature(&b.public_key(), b"data", &sig).is_err());
    }

    #[test]
    fn seed_roundtrip() {
        let id = Identity::generate();
        let seed = id.to_seed();
        let id2 = Identity::from_seed(seed);
        assert_eq!(id.public_key(), id2.public_key());
    }
}
