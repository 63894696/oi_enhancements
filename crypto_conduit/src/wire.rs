//! Wire 格式:自描述、可移植、抗内容尺寸侧信道。
//!
//! ```text
//! [版本:1B][套件ID:1B][flags:1B][KEM密文][nonce][AEAD(填充明文)][tag]
//! ```
//!
//! - 版本字节最前 -> 未来换套件平滑迁移
//! - 长度填充到固定桶(256B/1KB/4KB/16KB)-> 削弱按消息长度画像
//! - 零 IM 私有字段 -> 任何通道原样搬运

use crate::error::ConduitError;

/// 当前 wire 版本。
pub const WIRE_VERSION: u8 = 0x01;

/// 套件 ID:X25519 + ML-KEM-768 / XChaCha20-Poly1305 / HKDF-SHA256 / Ed25519。
pub const SUITE_X25519_MLKEM768: u8 = 0x01;

/// 套件 ID:X25519 ECDH / AES-256-GCM / HKDF-SHA256 / Ed25519。
///
/// 双栈协商的 Web 端套件(SecureDM 群聊形态3):浏览器 WebCrypto 无 ML-KEM,
/// 但有 X25519 + AES-GCM,与本常量对应。Rust conduit 端自身**不产出** 0x02
/// 密文(seal 固定用 0x01),此常量供群聊房间钥协商时识别/声明;Web 端的
/// 房间钥 wrap/消息 seal 以 0x02 自描述,接收方按套件字节分派解密路径。
pub const SUITE_X25519_AESGCM: u8 = 0x02;

/// 长度填充桶。消息正文(含内部 seq 头)向上取整到这些尺寸之一。
/// 最大桶即 SimpleX 固定块尺寸 16KB,恰好兼容。
pub const PAD_BUCKETS: [usize; 4] = [256, 1024, 4096, 16384];

/// 对正文长度选填充目标。超过最大桶则原样(不拆包,留给上层分片)。
pub fn pad_target(plain_len: usize) -> usize {
    for &b in PAD_BUCKETS.iter() {
        if plain_len <= b {
            return b;
        }
    }
    plain_len
}

/// 把正文填充到桶尺寸。格式:[原长:4B LE][正文][0x00...]。
/// 4 字节原长头让解填充能精确还原,零字节填充不引入歧义。
pub fn pad(plain: &[u8]) -> Vec<u8> {
    let target = pad_target(plain.len() + 4);
    let mut out = Vec::with_capacity(target);
    out.extend_from_slice(&(plain.len() as u32).to_le_bytes());
    out.extend_from_slice(plain);
    out.resize(target, 0);
    out
}

/// 解填充。还原原始正文。
pub fn unpad(padded: &[u8]) -> Result<Vec<u8>, ConduitError> {
    if padded.len() < 4 {
        return Err(ConduitError::MalformedWire("padded body < 4"));
    }
    let n = u32::from_le_bytes([padded[0], padded[1], padded[2], padded[3]]) as usize;
    if n > padded.len() - 4 {
        return Err(ConduitError::MalformedWire("declared len > body"));
    }
    Ok(padded[4..4 + n].to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pad_roundtrip_various_sizes() {
        for n in [0usize, 1, 100, 300, 1020, 2000, 5000, 16000] {
            let data = vec![0xABu8; n];
            let padded = pad(&data);
            assert!(padded.len() >= n + 4);
            // 尺寸应落在某个桶内(或超桶原样)
            let back = unpad(&padded).unwrap();
            assert_eq!(back, data, "roundtrip failed for n={n}");
        }
    }

    #[test]
    fn pad_quantizes_length() {
        // 1 字节和 200 字节正文,填充后应同桶 -> 服务器无法按长度区分
        let a = pad(b"x");
        let b = pad(&vec![7u8; 200]);
        assert_eq!(a.len(), b.len());
        assert_eq!(a.len(), 256);
    }
}
