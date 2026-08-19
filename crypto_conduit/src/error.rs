//! 错误类型。FFI 边界会把这些映射成整数错误码。

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ConduitError {
    #[error("wire 包过短或格式非法: {0}")]
    MalformedWire(&'static str),

    #[error("未知版本字节: {0:#04x}")]
    UnknownVersion(u8),

    #[error("未知套件 ID: {0:#04x}")]
    UnknownSuite(u8),

    #[error("握手未完成,不能 seal/open")]
    HandshakeNotDone,

    #[error("对端签名验证失败(可能中间人)")]
    BadSignature,

    #[error("AEAD 解密失败(密钥错/被篡改)")]
    DecryptFailed,

    #[error("棘轮:乱序超前过多,跳消息缓存拒绝(seq={got}, 当前={cur})")]
    SkippedTooFar { got: u64, cur: u64 },

    #[error("棘轮:重复或过期消息(seq={0})")]
    ReplayOrStale(u64),

    #[error("FFI:空指针参数")]
    NullPtr,

    #[error("FFI:无效句柄")]
    BadHandle,

    #[error("内部: {0}")]
    Internal(String),
}

/// FFI 错误码。0 = 成功,非 0 对应上面各分支。
/// 保持小而稳定,Python/Go 侧据此判断。
#[repr(i32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FfiCode {
    Ok = 0,
    MalformedWire = 1,
    UnknownVersion = 2,
    UnknownSuite = 3,
    HandshakeNotDone = 4,
    BadSignature = 5,
    DecryptFailed = 6,
    SkippedTooFar = 7,
    ReplayOrStale = 8,
    NullPtr = 9,
    BadHandle = 10,
    Internal = 255,
}

impl From<&ConduitError> for FfiCode {
    fn from(e: &ConduitError) -> Self {
        match e {
            ConduitError::MalformedWire(_) => FfiCode::MalformedWire,
            ConduitError::UnknownVersion(_) => FfiCode::UnknownVersion,
            ConduitError::UnknownSuite(_) => FfiCode::UnknownSuite,
            ConduitError::HandshakeNotDone => FfiCode::HandshakeNotDone,
            ConduitError::BadSignature => FfiCode::BadSignature,
            ConduitError::DecryptFailed => FfiCode::DecryptFailed,
            ConduitError::SkippedTooFar { .. } => FfiCode::SkippedTooFar,
            ConduitError::ReplayOrStale(_) => FfiCode::ReplayOrStale,
            ConduitError::NullPtr => FfiCode::NullPtr,
            ConduitError::BadHandle => FfiCode::BadHandle,
            ConduitError::Internal(_) => FfiCode::Internal,
        }
    }
}
