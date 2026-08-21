//! C ABI 导出:opaque handle + C 字符串 JSON,仿 prisir_ime。
//! Python 壳(ctypes)经此调用;字符串由调用方 findex_free_string 释放。

use crate::index::FindexEngine;
use serde::Deserialize;
use serde_json::json;
use std::ffi::{c_char, c_void, CStr, CString};

fn cstr<'a>(p: *const c_char) -> &'a str {
    if p.is_null() {
        return "";
    }
    unsafe { CStr::from_ptr(p) }.to_str().unwrap_or("")
}

fn to_c_string(s: String) -> *mut c_char {
    CString::new(s).map(CString::into_raw).unwrap_or(std::ptr::null_mut())
}

fn err_json(msg: &str) -> *mut c_char {
    to_c_string(json!({"ok": false, "error": msg}).to_string())
}

fn get<'a>(h: *mut c_void) -> Option<&'a FindexEngine> {
    if h.is_null() {
        None
    } else {
        Some(unsafe { &*(h as *const FindexEngine) })
    }
}

#[derive(Deserialize, Default)]
struct BuildArgs {
    #[serde(default)]
    roots: Vec<String>,
    #[serde(default)]
    exclude: Vec<String>,
}

/// 开/建库(不扫盘)。返回 opaque handle,失败回 null。
#[no_mangle]
pub extern "C" fn findex_open(db_path: *const c_char) -> *mut c_void {
    match FindexEngine::new(cstr(db_path)) {
        Ok(e) => Box::into_raw(Box::new(e)) as *mut c_void,
        Err(_) => std::ptr::null_mut(),
    }
}

/// 首扫/重建索引(同步,调用方放线程)。args_json: {"roots":[...], "exclude":[...]}
/// 回 {"ok":true,"scanned":N} 或 {"ok":false,"error":...}。
#[no_mangle]
pub extern "C" fn findex_build(handle: *mut c_void, args_json: *const c_char) -> *mut c_char {
    let eng = match get(handle) {
        Some(e) => e,
        None => return err_json("null_handle"),
    };
    let args: BuildArgs = serde_json::from_str(cstr(args_json)).unwrap_or_default();
    if args.roots.is_empty() {
        return err_json("no_roots");
    }
    match eng.build(&args.roots, &args.exclude) {
        Ok(n) => to_c_string(json!({"ok": true, "scanned": n}).to_string()),
        Err(e) => err_json(&e),
    }
}

/// 搜索。回 {"ok":true,"hits":[...],"total":N}(带 is_dir,匹配度排序,offset 分页)。
#[no_mangle]
pub extern "C" fn findex_query(handle: *mut c_void, query: *const c_char, limit: u32, offset: u32) -> *mut c_char {
    let eng = match get(handle) {
        Some(e) => e,
        None => return err_json("null_handle"),
    };
    match eng.query(cstr(query), limit, offset) {
        Ok(res) => to_c_string(json!({"ok": true, "hits": res.hits, "total": res.total}).to_string()),
        Err(e) => err_json(&e),
    }
}

/// 状态:enabled/indexed_count/last_scan/building/scanned。
#[no_mangle]
pub extern "C" fn findex_status(handle: *mut c_void) -> *mut c_char {
    let eng = match get(handle) {
        Some(e) => e,
        None => return err_json("null_handle"),
    };
    to_c_string(
        json!({
            "ok": true,
            "enabled": eng.is_enabled(),
            "indexed_count": eng.indexed_count(),
            "last_scan": eng.last_scan(),
            "building": eng.is_building(),
            "scanned": eng.scanned(),
        })
        .to_string(),
    )
}

/// 安全体检:最近 since_unix 秒内改动过的可执行/脚本文件。
/// args_json = {"since_unix":i64, "exts":["exe",...], "limit":u32}。纯元数据,不读内容。
#[no_mangle]
pub extern "C" fn findex_recent_exec(handle: *mut c_void, args_json: *const c_char) -> *mut c_char {
    let eng = match get(handle) {
        Some(e) => e,
        None => return err_json("null_handle"),
    };
    let v: serde_json::Value = match serde_json::from_str(cstr(args_json)) {
        Ok(v) => v,
        Err(e) => return err_json(&format!("bad_json: {e}")),
    };
    let since = v.get("since_unix").and_then(|x| x.as_i64()).unwrap_or(0);
    let exts: Vec<String> = v
        .get("exts")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().filter_map(|e| e.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    let limit = v.get("limit").and_then(|x| x.as_u64()).unwrap_or(500) as u32;
    match eng.query_recent_exec(since, &exts, limit) {
        Ok(res) => to_c_string(json!({"ok": true, "hits": res.hits, "total": res.total}).to_string()),
        Err(e) => err_json(&e),
    }
}

/// 清空索引(关闭功能)。
#[no_mangle]
pub extern "C" fn findex_clear(handle: *mut c_void) -> *mut c_char {
    let eng = match get(handle) {
        Some(e) => e,
        None => return err_json("null_handle"),
    };
    match eng.clear() {
        Ok(_) => to_c_string(json!({"ok": true}).to_string()),
        Err(e) => err_json(&e),
    }
}

/// 释放 handle。
#[no_mangle]
pub extern "C" fn findex_free(handle: *mut c_void) {
    if !handle.is_null() {
        drop(unsafe { Box::from_raw(handle as *mut FindexEngine) });
    }
}

/// 释放返回的 C 字符串。
#[no_mangle]
pub extern "C" fn findex_free_string(s: *mut c_char) {
    if !s.is_null() {
        drop(unsafe { CString::from_raw(s) });
    }
}
