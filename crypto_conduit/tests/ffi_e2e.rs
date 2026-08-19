//! FFI 层端到端测试:模拟双进程,经 C ABI 走完整 建身份→握手→seal/open。
//! 验证 win32 内存纪律(cc_buffer_free)与句柄生命周期。

use crypto_conduit::ffi::*;
use std::ptr;

fn buf_vec(b: CcBuffer) -> Vec<u8> {
    if b.ptr.is_null() || b.len == 0 {
        return Vec::new();
    }
    let v = unsafe { std::slice::from_raw_parts(b.ptr, b.len).to_vec() };
    unsafe { cc_buffer_free(b) };
    v
}

fn gen_seed() -> Vec<u8> {
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_localkeys_generate(&mut out) };
    assert_eq!(rc, 0, "cc_localkeys_generate failed rc={rc}");
    buf_vec(out)
}

fn public_of(seed: &[u8]) -> Vec<u8> {
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_localkeys_public(seed.as_ptr(), seed.len(), &mut out) };
    assert_eq!(rc, 0, "cc_localkeys_public failed rc={rc}");
    buf_vec(out)
}

fn seal(h: *mut CcSession, data: &[u8]) -> Vec<u8> {
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_seal(h, data.as_ptr(), data.len(), &mut out) };
    assert_eq!(rc, 0, "cc_seal failed rc={rc}");
    buf_vec(out)
}

fn open(h: *mut CcSession, blob: &[u8]) -> Vec<u8> {
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_open(h, blob.as_ptr(), blob.len(), &mut out) };
    assert_eq!(rc, 0, "cc_open failed rc={rc}");
    buf_vec(out)
}

struct Party {
    seed: Vec<u8>,
    session: *mut CcSession,
}

impl Party {
    fn new() -> Self {
        Party {
            seed: gen_seed(),
            session: ptr::null_mut(),
        }
    }
    fn initiate(&mut self, peer_pub: &[u8]) -> Vec<u8> {
        let mut hs = CcBuffer {
            ptr: ptr::null_mut(),
            len: 0,
        };
        let rc = unsafe {
            cc_session_initiate(
                self.seed.as_ptr(),
                self.seed.len(),
                peer_pub.as_ptr(),
                peer_pub.len(),
                &mut self.session,
                &mut hs,
            )
        };
        assert_eq!(rc, 0, "cc_session_initiate failed rc={rc}");
        buf_vec(hs)
    }
    fn accept(&mut self, handshake: &[u8]) {
        let rc = unsafe {
            cc_session_accept(
                self.seed.as_ptr(),
                self.seed.len(),
                handshake.as_ptr(),
                handshake.len(),
                &mut self.session,
            )
        };
        assert_eq!(rc, 0, "cc_session_accept failed rc={rc}");
    }
}

impl Drop for Party {
    fn drop(&mut self) {
        if !self.session.is_null() {
            unsafe { cc_session_destroy(self.session) };
        }
    }
}

#[test]
fn ffi_full_handshake_and_messaging() {
    let mut alice = Party::new();
    let mut bob = Party::new();

    let bob_pub = public_of(&bob.seed);
    let handshake = alice.initiate(&bob_pub);
    bob.accept(&handshake);

    // alice -> bob
    let b1 = seal(alice.session, b"hello over ffi");
    assert_eq!(open(bob.session, &b1), b"hello over ffi");

    // bob -> alice
    let b2 = seal(bob.session, b"reply over ffi");
    assert_eq!(open(alice.session, &b2), b"reply over ffi");
}

#[test]
fn ffi_out_of_order_and_replay() {
    let mut alice = Party::new();
    let mut bob = Party::new();
    let bob_pub = public_of(&bob.seed);
    let hs = alice.initiate(&bob_pub);
    bob.accept(&hs);

    let m0 = seal(alice.session, b"m0");
    let m1 = seal(alice.session, b"m1");
    let m2 = seal(alice.session, b"m2");
    // 乱序:2 先到
    assert_eq!(open(bob.session, &m2), b"m2");
    assert_eq!(open(bob.session, &m0), b"m0");
    assert_eq!(open(bob.session, &m1), b"m1");

    // 重放 m0 -> cc_open 应返回非 0(ReplayOrStale)
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_open(bob.session, m0.as_ptr(), m0.len(), &mut out) };
    assert_ne!(rc, 0, "replay should be rejected");
}

#[test]
fn ffi_tamper_rejected() {
    let mut alice = Party::new();
    let mut bob = Party::new();
    let bob_pub = public_of(&bob.seed);
    let hs = alice.initiate(&bob_pub);
    bob.accept(&hs);

    let mut blob = seal(alice.session, b"integrity check");
    let n = blob.len();
    blob[n - 1] ^= 0x01;
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_open(bob.session, blob.as_ptr(), blob.len(), &mut out) };
    assert_ne!(rc, 0, "tampered blob must be rejected");
}

#[test]
fn ffi_null_safety() {
    // 空指针不 panic,返回错误码
    let mut out = CcBuffer {
        ptr: ptr::null_mut(),
        len: 0,
    };
    let rc = unsafe { cc_localkeys_public(ptr::null(), 0, &mut out) };
    assert_ne!(rc, 0);
    let rc2 = unsafe { cc_seal(ptr::null_mut(), b"x".as_ptr(), 1, &mut out) };
    assert_ne!(rc2, 0);
    let rc3 = unsafe { cc_open(ptr::null_mut(), b"x".as_ptr(), 1, &mut out) };
    assert_ne!(rc3, 0);
}

#[test]
fn ffi_repeated_sessions_no_leak_crash() {
    // 反复建/毁会话 + 加解密,验证 win32 内存纪律(不泄漏不崩)
    for _ in 0..20 {
        let mut a = Party::new();
        let mut b = Party::new();
        let bp = public_of(&b.seed);
        let hs = a.initiate(&bp);
        b.accept(&hs);
        for i in 0..5 {
            let msg = format!("iter {i}");
            let blob = seal(a.session, msg.as_bytes());
            assert_eq!(open(b.session, &blob), msg.as_bytes());
        }
        // Party drop 自动 cc_session_destroy
    }
}
