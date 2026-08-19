//! C ABI FFI 边界。
//!
//! ⚠️ 内存纪律(吸取 libsimplex win32 GHC 堆 msvcrt.free 崩溃根因):
//!   - 所有输出 buffer 由 Rust 用 `Vec`/`Box` 分配,所有权随指针交出
//!   - **必须由本库的 `cc_buffer_free` 释放**,调用方(Python/Go)绝不可 free
//!   - 句柄是不透明 `*mut` 指针,配对出现 `*_destroy`
//!   - 传空指针 = FfiCode::NullPtr,不 panic(FFI 边界不 unwind)
//!
//! 错误约定:返回 i32 错误码(0=成功),输出经 out-param。

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;

use crate::error::{ConduitError, FfiCode};
use crate::identity::Identity;
use crate::session::{HandshakeMsg, LocalKeys, PeerPublic, Session};

/// 调用方持有、用完交给 `cc_buffer_free` 的字节 buffer。
#[repr(C)]
pub struct CcBuffer {
    pub ptr: *mut u8,
    pub len: usize,
}

impl CcBuffer {
    fn from_vec(mut v: Vec<u8>) -> Self {
        let ptr = v.as_mut_ptr();
        let len = v.len();
        std::mem::forget(v); // 所有权交出,由 cc_buffer_free 回收
        CcBuffer { ptr, len }
    }
    fn empty() -> Self {
        CcBuffer {
            ptr: ptr::null_mut(),
            len: 0,
        }
    }
}

/// 释放 `CcBuffer`。配对 `from_vec`,重建 Vec 后 drop。
///
/// # Safety
/// 只能对本库返回的 buffer 调用一次。
#[no_mangle]
pub unsafe extern "C" fn cc_buffer_free(buf: CcBuffer) {
    if !buf.ptr.is_null() && buf.len > 0 {
        drop(Vec::from_raw_parts(buf.ptr, buf.len, buf.len));
    }
}

fn ok_buf(v: Vec<u8>, out: *mut CcBuffer) -> i32 {
    if out.is_null() {
        return FfiCode::NullPtr as i32;
    }
    unsafe { *out = CcBuffer::from_vec(v) };
    FfiCode::Ok as i32
}

fn fail(e: ConduitError, out: *mut CcBuffer) -> i32 {
    if !out.is_null() {
        unsafe { *out = CcBuffer::empty() };
    }
    FfiCode::from(&e) as i32
}

/// 统一 panic 兜底:FFI 不跨边界 unwind。
fn guard(f: impl FnOnce() -> i32) -> i32 {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(FfiCode::Internal as i32)
}

// ---------- 身份 ----------

/// 生成身份,返回 32 字节种子(调用方负责加密持久化)。
#[no_mangle]
pub unsafe extern "C" fn cc_identity_generate(out_seed: *mut CcBuffer) -> i32 {
    guard(|| {
        let id = Identity::generate();
        ok_buf(id.to_seed().to_vec(), out_seed)
    })
}

/// 身份指纹(人眼可读,hex 分组字符串)。
#[no_mangle]
pub unsafe extern "C" fn cc_identity_fingerprint(
    seed: *const u8,
    seed_len: usize,
    out_fp: *mut CcBuffer,
) -> i32 {
    guard(|| {
        if seed.is_null() || seed_len != 32 {
            return fail(ConduitError::NullPtr, out_fp);
        }
        let mut s = [0u8; 32];
        s.copy_from_slice(slice::from_raw_parts(seed, 32));
        let id = Identity::from_seed(s);
        ok_buf(id.fingerprint().into_bytes(), out_fp)
    })
}

// ---------- 密钥材料与握手 ----------

/// 生成完整密钥材料(身份+ML-KEM+X25519),返回 128B 种子供持久化。
/// 配对 `cc_localkeys_public`(取公钥) 与握手函数(种子直接传入)。
#[no_mangle]
pub unsafe extern "C" fn cc_localkeys_generate(out_seed: *mut CcBuffer) -> i32 {
    guard(|| {
        let keys = LocalKeys::generate(Identity::generate());
        match keys.to_seed() {
            Some(s) => ok_buf(s, out_seed),
            None => fail(ConduitError::Internal("to_seed".into()), out_seed),
        }
    })
}

/// 从 128B 种子导出对端公钥材料(PeerPublic 序列化),供带外分发给对端。
#[no_mangle]
pub unsafe extern "C" fn cc_localkeys_public(
    seed: *const u8,
    seed_len: usize,
    out_pub: *mut CcBuffer,
) -> i32 {
    guard(|| {
        if seed.is_null() || seed_len != 128 {
            return fail(ConduitError::NullPtr, out_pub);
        }
        let b = slice::from_raw_parts(seed, seed_len);
        match LocalKeys::from_seed(b) {
            Ok(k) => ok_buf(k.public().to_bytes(), out_pub),
            Err(e) => fail(e, out_pub),
        }
    })
}

/// 发起方建会话。输入我方种子 + 对端公钥,输出会话句柄 + 握手消息(发给对端)。
/// out_session / out_handshake 均不可为 null。
#[no_mangle]
pub unsafe extern "C" fn cc_session_initiate(
    my_seed: *const u8,
    my_seed_len: usize,
    peer_pub: *const u8,
    peer_pub_len: usize,
    out_session: *mut *mut CcSession,
    out_handshake: *mut CcBuffer,
) -> i32 {
    guard(|| {
        if out_session.is_null() {
            return fail(ConduitError::NullPtr, out_handshake);
        }
        unsafe { *out_session = ptr::null_mut() };
        if my_seed.is_null() || my_seed_len != 128 || peer_pub.is_null() {
            return fail(ConduitError::NullPtr, out_handshake);
        }
        let my = match LocalKeys::from_seed(slice::from_raw_parts(my_seed, my_seed_len)) {
            Ok(k) => k,
            Err(e) => return fail(e, out_handshake),
        };
        let peer = match PeerPublic::from_bytes(slice::from_raw_parts(peer_pub, peer_pub_len)) {
            Ok(p) => p,
            Err(e) => return fail(e, out_handshake),
        };
        match Session::initiate(&my.identity, &peer) {
            Ok((sess, hs)) => {
                unsafe { *out_session = Box::into_raw(Box::new(CcSession { sess })) };
                ok_buf(hs.to_bytes(), out_handshake)
            }
            Err(e) => fail(e, out_handshake),
        }
    })
}

/// 接收方建会话。输入我方种子 + 收到的握手消息,输出会话句柄。
#[no_mangle]
pub unsafe extern "C" fn cc_session_accept(
    my_seed: *const u8,
    my_seed_len: usize,
    handshake: *const u8,
    handshake_len: usize,
    out_session: *mut *mut CcSession,
) -> i32 {
    guard(|| {
        if out_session.is_null() {
            return FfiCode::NullPtr as i32;
        }
        unsafe { *out_session = ptr::null_mut() };
        if my_seed.is_null() || my_seed_len != 128 || handshake.is_null() {
            return FfiCode::NullPtr as i32;
        }
        let my = match LocalKeys::from_seed(slice::from_raw_parts(my_seed, my_seed_len)) {
            Ok(k) => k,
            Err(e) => return FfiCode::from(&e) as i32,
        };
        let hs = match HandshakeMsg::from_bytes(slice::from_raw_parts(handshake, handshake_len)) {
            Ok(h) => h,
            Err(e) => return FfiCode::from(&e) as i32,
        };
        match Session::accept(&my, &hs) {
            Ok(sess) => {
                unsafe { *out_session = Box::into_raw(Box::new(CcSession { sess })) };
                FfiCode::Ok as i32
            }
            Err(e) => FfiCode::from(&e) as i32,
        }
    })
}

// ---------- 会话 seal/open ----------

/// 不透明会话句柄。
pub struct CcSession {
    sess: Session,
}

#[no_mangle]
pub unsafe extern "C" fn cc_session_destroy(h: *mut CcSession) {
    if !h.is_null() {
        drop(Box::from_raw(h));
    }
}

/// seal:plaintext -> wire blob。
#[no_mangle]
pub unsafe extern "C" fn cc_seal(
    h: *mut CcSession,
    plain: *const u8,
    plain_len: usize,
    out: *mut CcBuffer,
) -> i32 {
    guard(|| {
        if h.is_null() {
            return fail(ConduitError::BadHandle, out);
        }
        if plain.is_null() && plain_len > 0 {
            return fail(ConduitError::NullPtr, out);
        }
        let data = if plain_len == 0 {
            &[]
        } else {
            slice::from_raw_parts(plain, plain_len)
        };
        match (*h).sess.seal(data) {
            Ok(blob) => ok_buf(blob, out),
            Err(e) => fail(e, out),
        }
    })
}

/// open:wire blob -> plaintext。
#[no_mangle]
pub unsafe extern "C" fn cc_open(
    h: *mut CcSession,
    blob: *const u8,
    blob_len: usize,
    out: *mut CcBuffer,
) -> i32 {
    guard(|| {
        if h.is_null() {
            return fail(ConduitError::BadHandle, out);
        }
        if blob.is_null() || blob_len == 0 {
            return fail(ConduitError::NullPtr, out);
        }
        let data = slice::from_raw_parts(blob, blob_len);
        match (*h).sess.open(data) {
            Ok(plain) => ok_buf(plain, out),
            Err(e) => fail(e, out),
        }
    })
}
