//! crypto-conduit:通用 E2E 加密覆盖层(Capability-04)。
//!
//! 独立于任何 IM 的第二层端到端加密。明文进、不透明字节包出;
//! IM 对它是纯管道,它对 IM 是纯载荷。
//!
//! 设计文档:`Documents/oiagent-os-integration/capability-04-e2e-overlay.md`
//!
//! 模块:
//! - [`identity`] 跨 IM 可移植身份(Ed25519,TOFU+指纹)
//! - [`ratchet`]  方案 B 单向 KDF 链棘轮 + 跳消息缓存
//! - [`session`]  混合 KEM 握手 + 双向链,seal/open 整合点
//! - [`wire`]     自描述 wire 格式 + 长度填充(抗尺寸侧信道)
//! - [`error`]    错误类型 + FFI 错误码
//! - [`ffi`]      C ABI 边界(win32 内存纪律:Rust 分配、Rust 释放)

pub mod error;
pub mod ffi;
pub mod identity;
pub mod ratchet;
pub mod session;
pub mod wire;
