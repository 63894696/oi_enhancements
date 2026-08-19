//! C ABI 导出(为浏览器 Chromium FFI 集成做准备,P5 用)
//!
//! 用法(C 侧):
//!   void* h = prisir_asr_load(".../sensevoice-small");
//!   char* text = prisir_asr_transcribe_wav(h, "a.wav", 1, 14);
//!   prisir_asr_free_string(text);
//!   prisir_asr_free(h);

use crate::SenseVoiceASR;
use std::ffi::{c_char, c_int, c_void, CStr, CString};

fn cstr(ptr: *const c_char) -> Option<&'static str> {
    if ptr.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(ptr) }.to_str().ok()
}

/// 加载模型,返回 opaque 句柄(失败返回 null)
#[no_mangle]
pub extern "C" fn prisir_asr_load(model_dir: *const c_char) -> *mut c_void {
    let Some(dir) = cstr(model_dir) else {
        return std::ptr::null_mut();
    };
    match SenseVoiceASR::load(dir) {
        Ok(asr) => Box::into_raw(Box::new(asr)) as *mut c_void,
        Err(_) => std::ptr::null_mut(),
    }
}

/// 识别 16kHz mono 16-bit wav 文件,返回 UTF-8 字符串(调用方须用 prisir_asr_free_string 释放)
#[no_mangle]
pub extern "C" fn prisir_asr_transcribe_wav(
    handle: *mut c_void,
    wav_path: *const c_char,
    language: c_int,
    textnorm: c_int,
) -> *mut c_char {
    if handle.is_null() {
        return std::ptr::null_mut();
    }
    let Some(path) = cstr(wav_path) else {
        return std::ptr::null_mut();
    };
    let asr = unsafe { &mut *(handle as *mut SenseVoiceASR) };
    match asr.transcribe_wav(path, language, textnorm) {
        Ok(text) => CString::new(text)
            .map(CString::into_raw)
            .unwrap_or(std::ptr::null_mut()),
        Err(_) => std::ptr::null_mut(),
    }
}

/// 识别 16kHz mono f32 PCM(调用方须用 prisir_asr_free_string 释放)
#[no_mangle]
pub extern "C" fn prisir_asr_transcribe_pcm(
    handle: *mut c_void,
    pcm: *const f32,
    len: usize,
    language: c_int,
    textnorm: c_int,
) -> *mut c_char {
    if handle.is_null() || pcm.is_null() {
        return std::ptr::null_mut();
    }
    let asr = unsafe { &mut *(handle as *mut SenseVoiceASR) };
    let samples = unsafe { std::slice::from_raw_parts(pcm, len) };
    match asr.transcribe_pcm(samples, language, textnorm) {
        Ok(text) => CString::new(text)
            .map(CString::into_raw)
            .unwrap_or(std::ptr::null_mut()),
        Err(_) => std::ptr::null_mut(),
    }
}

/// 释放 transcribe 返回的字符串
#[no_mangle]
pub extern "C" fn prisir_asr_free_string(s: *mut c_char) {
    if !s.is_null() {
        unsafe { drop(CString::from_raw(s)) };
    }
}

/// 释放引擎句柄
#[no_mangle]
pub extern "C" fn prisir_asr_free(handle: *mut c_void) {
    if !handle.is_null() {
        unsafe { drop(Box::from_raw(handle as *mut SenseVoiceASR)) };
    }
}
