//! 方案 B 的心脏:单向 KDF 链棘轮 + 跳消息密钥缓存。
//!
//! 砍掉 DH 棘轮的对称 ping-pong(底层 SimpleX 已有完整棘轮),
//! 保留哈希棘轮提供的:
//!   - 前向保密:消息密钥用后销毁,旧链密钥不可恢复
//!   - 入侵后恢复:链持续前滚,泄露当前链密钥不泄露未来
//!
//! 乱序/离线由跳消息密钥缓存兜底(容量有界、超时淘汰)。

use hkdf::Hkdf;
use sha2::Sha256;
use std::collections::HashMap;
use zeroize::{Zeroize, ZeroizeOnDrop};

use crate::error::ConduitError;

/// 跳消息缓存容量上限:最多为未来多少条消息缓存密钥。
/// 超出即拒绝(防内存放大攻击)。IM 场景 128 足够覆盖常规乱序。
pub const MAX_SKIP: u64 = 128;

/// 一条 KDF 链:对称方向(收或发各一条)。
#[derive(Zeroize, ZeroizeOnDrop)]
pub struct Chain {
    /// 当前链密钥。每次派生消息密钥后前滚并就地清零旧值。
    ck: [u8; 32],
    /// 已派生的消息序号(下一条消息的 seq)。
    next_seq: u64,
}

/// HKDF info 域分离:链前滚 vs 消息密钥,互不串用。
const INFO_CHAIN: &[u8] = b"crypto-conduit chain v1";
const INFO_MSG: &[u8] = b"crypto-conduit msgkey v1";

impl Chain {
    pub fn new(root: &[u8; 32]) -> Self {
        Self {
            ck: *root,
            next_seq: 0,
        }
    }

    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    /// 从 (ck, seq) 派生一次性消息密钥(不推进链)。
    fn derive_msg_key(ck: &[u8; 32], seq: u64) -> [u8; 32] {
        let hk = Hkdf::<Sha256>::new(Some(&seq.to_le_bytes()), ck);
        let mut okm = [0u8; 32];
        hk.expand(INFO_MSG, &mut okm)
            .expect("HKDF expand 32B 不会失败");
        okm
    }

    /// 推进一格:返回当前消息密钥,然后链前滚并销毁旧链密钥。
    pub fn ratchet(&mut self) -> [u8; 32] {
        let mk = Self::derive_msg_key(&self.ck, self.next_seq);
        // 前滚:ck' = HKDF(ck)
        let hk = Hkdf::<Sha256>::new(None, &self.ck);
        let mut new_ck = [0u8; 32];
        hk.expand(INFO_CHAIN, &mut new_ck)
            .expect("HKDF expand 32B 不会失败");
        self.ck.zeroize();
        self.ck = new_ck;
        self.next_seq += 1;
        mk
    }
}

/// 接收侧:乱序容忍。
///
/// 每条消息带 seq。若 seq == next_seq 直接 ratchet;若 seq > next_seq,
/// 缓存中间跳过的消息密钥再 ratchet 到目标;若 seq < next_seq,
/// 查跳消息缓存(命中即取,取后删除)。
#[derive(Zeroize, ZeroizeOnDrop)]
pub struct RecvChain {
    chain: Chain,
    /// 跳过待用的消息密钥:seq -> msgkey。
    #[zeroize(skip)]
    skipped: HashMap<u64, [u8; 32]>,
}

impl RecvChain {
    pub fn new(root: &[u8; 32]) -> Self {
        Self {
            chain: Chain::new(root),
            skipped: HashMap::new(),
        }
    }

    pub fn next_seq(&self) -> u64 {
        self.chain.next_seq()
    }

    /// 取出 seq 对应的消息密钥。处理乱序、拒绝重放与超距跳变。
    pub fn msg_key(&mut self, seq: u64) -> Result<[u8; 32], ConduitError> {
        let cur = self.chain.next_seq();

        if seq < cur {
            // 过去消息:只能在缓存里(重放已被取走的会 miss)
            return self
                .skipped
                .remove(&seq)
                .ok_or(ConduitError::ReplayOrStale(seq));
        }

        if seq - cur > MAX_SKIP {
            return Err(ConduitError::SkippedTooFar { got: seq, cur });
        }

        // 缓存中间跳过的密钥
        while self.chain.next_seq() < seq {
            let k = self.chain.ratchet();
            let s = self.chain.next_seq() - 1;
            self.skipped.insert(s, k);
        }
        Ok(self.chain.ratchet())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root() -> [u8; 32] {
        [0x42u8; 32]
    }

    #[test]
    fn send_recv_in_order() {
        let mut send = Chain::new(&root());
        let mut recv = RecvChain::new(&root());
        for seq in 0..10 {
            let mk_s = send.ratchet();
            let mk_r = recv.msg_key(seq).unwrap();
            assert_eq!(mk_s, mk_r, "seq={seq}");
        }
    }

    #[test]
    fn out_of_order_within_skip() {
        let mut send = Chain::new(&root());
        let mut recv = RecvChain::new(&root());
        let keys: Vec<_> = (0..5).map(|_| send.ratchet()).collect();
        // 先收 3,再收 0/1/2,再收 4 —— 乱序
        assert_eq!(recv.msg_key(3).unwrap(), keys[3]);
        assert_eq!(recv.msg_key(0).unwrap(), keys[0]);
        assert_eq!(recv.msg_key(1).unwrap(), keys[1]);
        assert_eq!(recv.msg_key(2).unwrap(), keys[2]);
        assert_eq!(recv.msg_key(4).unwrap(), keys[4]);
    }

    #[test]
    fn replay_rejected() {
        let mut send = Chain::new(&root());
        let mut recv = RecvChain::new(&root());
        let _ = send.ratchet();
        let _ = send.ratchet();
        recv.msg_key(1).unwrap();
        // 再取一次 seq=1 -> 已取走,拒绝
        assert!(matches!(
            recv.msg_key(1),
            Err(ConduitError::ReplayOrStale(1))
        ));
    }

    #[test]
    fn skip_too_far_rejected() {
        let mut recv = RecvChain::new(&root());
        assert!(matches!(
            recv.msg_key(MAX_SKIP + 10),
            Err(ConduitError::SkippedTooFar { .. })
        ));
    }

    #[test]
    fn forward_secrecy_distinct_keys() {
        let mut send = Chain::new(&root());
        let k0 = send.ratchet();
        let k1 = send.ratchet();
        assert_ne!(k0, k1);
    }
}
