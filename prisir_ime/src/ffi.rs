//! C ABI 导出(为 Chromium FFI 集成 / Python ctypes 验证用,对齐 prisir_asr FFI 形态)
//!
//! 用法(C 侧):
//!   void* h = prisir_ime_load(".../ciku.db");
//!   char* json = prisir_ime_query(h, "nihao");            // [{"word":"你好","weight":123},...]
//!   char* sent = prisir_ime_smart_sentence(h, "nihaoshijie");
//!   prisir_ime_free_string(json); prisir_ime_free_string(sent);
//!   prisir_ime_free(h);

use crate::engine::ImeEngine;
use std::ffi::{c_char, c_void, CStr, CString};

fn cstr(ptr: *const c_char) -> Option<&'static str> {
    if ptr.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(ptr) }.to_str().ok()
}

fn to_c_string(s: String) -> *mut c_char {
    CString::new(s).map(CString::into_raw).unwrap_or(std::ptr::null_mut())
}

fn get<'a>(handle: *mut c_void) -> Option<&'a ImeEngine> {
    if handle.is_null() {
        return None;
    }
    Some(unsafe { &*(handle as *const ImeEngine) })
}

/// 加载词库,返回 opaque 句柄(失败返回 null)。build_index!=0 时加载内存 Trie 索引:
/// 优先读 `<db>.idx` 持久化缓存(指纹对上则秒开),否则首次构建并落盘,之后启动直接加载。
#[no_mangle]
pub extern "C" fn prisir_ime_load(db_path: *const c_char, build_index: i32) -> *mut c_void {
    let Some(path) = cstr(db_path) else {
        return std::ptr::null_mut();
    };
    let mut engine = match ImeEngine::new(path) {
        Ok(e) => e,
        Err(_) => return std::ptr::null_mut(),
    };
    // 模糊音规则:读配置文件(2026-09-04 模糊音设置功能,替代硬编码)。
    // 规则见 engine.rs fuzzy_map。文件不存在=默认三组平翘舌;存在但空=关闭模糊音。
    engine.set_fuzzy_rules(load_fuzzy_rules(path));
    if build_index != 0 {
        let (_ok, src) = engine.load_or_build_index(path);
        eprintln!("[prisir_ime] index source: {src}");
    }
    Box::into_raw(Box::new(engine)) as *mut c_void
}

/// 读模糊音配置(2026-09-04)。配置文件 = 词库同目录的 `fuzzy.txt`
/// (ciku.db → fuzzy.txt),每行一个规则名,`#` 开头为注释。
///   - 文件不存在 → 默认三组平翘舌 ["z_zh","c_ch","s_sh"](对齐外挂 ime_config)
///   - 文件存在但无任何有效行 → 关闭模糊音(返空 Vec)
///   - 否则 → 文件里列出的规则(非法规则名会被 fuzzy_map 忽略,不致命)
/// 引擎句柄 OnceLock 只建一次,故启动时读一次即可,无需热重载。
fn load_fuzzy_rules(db_path: &str) -> Vec<String> {
    let cfg = std::path::Path::new(db_path).with_file_name("fuzzy.txt");
    let Ok(text) = std::fs::read_to_string(&cfg) else {
        // 文件不存在 → 默认三组平翘舌。
        return vec!["z_zh".into(), "c_ch".into(), "s_sh".into()];
    };
    let mut rules = Vec::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        rules.push(t.to_string());
    }
    eprintln!("[prisir_ime] fuzzy rules from {:?}: {:?}", cfg, rules);
    rules
}

/// 候选查询,返回 JSON 数组 [{"word":"...","weight":N},...](调用方须 free_string)
#[no_mangle]
pub extern "C" fn prisir_ime_query(handle: *mut c_void, input: *const c_char) -> *mut c_char {
    let Some(inp) = cstr(input) else {
        return std::ptr::null_mut();
    };
    let Some(engine) = get(handle) else {
        return std::ptr::null_mut();
    };
    let cands = engine.query(inp);
    let arr: Vec<serde_json::Value> = cands
        .iter()
        .map(|(w, wt)| serde_json::json!({"word": w, "weight": wt}))
        .collect();
    to_c_string(serde_json::to_string(&arr).unwrap_or_else(|_| "[]".to_string()))
}

/// 五笔候选查询(wubi86),返回 JSON 数组 [{"word":"...","weight":N},...](调用方须 free_string)
#[no_mangle]
pub extern "C" fn prisir_ime_query_wubi(handle: *mut c_void, input: *const c_char) -> *mut c_char {
    let Some(inp) = cstr(input) else {
        return std::ptr::null_mut();
    };
    let Some(engine) = get(handle) else {
        return std::ptr::null_mut();
    };
    let cands = engine.query_wubi(inp);
    let arr: Vec<serde_json::Value> = cands
        .iter()
        .map(|(w, wt)| serde_json::json!({"word": w, "weight": wt}))
        .collect();
    to_c_string(serde_json::to_string(&arr).unwrap_or_else(|_| "[]".to_string()))
}

/// 整句智能首选,返回拼接整句(无路径返回空串;调用方须 free_string)
#[no_mangle]
pub extern "C" fn prisir_ime_smart_sentence(handle: *mut c_void, input: *const c_char) -> *mut c_char {
    let Some(inp) = cstr(input) else {
        return std::ptr::null_mut();
    };
    let Some(engine) = get(handle) else {
        return std::ptr::null_mut();
    };
    to_c_string(engine.smart_sentence(inp).unwrap_or_default())
}

/// 学习用户选择(更新词频)
#[no_mangle]
pub extern "C" fn prisir_ime_learn(handle: *mut c_void, input: *const c_char, selected: *const c_char) {
    let (Some(inp), Some(sel)) = (cstr(input), cstr(selected)) else {
        return;
    };
    if let Some(engine) = get(handle) {
        engine.learn(inp, sel);
    }
}

/// 释放 query/smart_sentence 返回的字符串
#[no_mangle]
pub extern "C" fn prisir_ime_free_string(s: *mut c_char) {
    if !s.is_null() {
        unsafe { drop(CString::from_raw(s)) };
    }
}

/// 释放引擎句柄
#[no_mangle]
pub extern "C" fn prisir_ime_free(handle: *mut c_void) {
    if !handle.is_null() {
        unsafe { drop(Box::from_raw(handle as *mut ImeEngine)) };
    }
}

// ============================================================
// 学习词库管理(Step 2 词库管理窗口用,2026-09-04)
// 走独立 user.db(不碰只读主库)。user.db 打开失败时这些方法静默无效,
// 由调用方按返回(JSON 空数组 / false)处理。字符串一律 UTF-8。
// ============================================================

/// 列出全部学习词 → JSON 数组 [{"key":"nihao","value":"你好","weight":100000,"seq":2},...]
/// 按 seq(学习先后)升序。调用方须 `prisir_ime_free_string` 释放。
#[no_mangle]
pub extern "C" fn prisir_ime_user_list(handle: *mut c_void) -> *mut c_char {
    let Some(engine) = get(handle) else {
        return to_c_string("[]".to_string());
    };
    let rows = engine.user_list();
    let arr: Vec<serde_json::Value> = rows
        .iter()
        .map(|(k, v, w, s)| serde_json::json!({"key": k, "value": v, "weight": w, "seq": s}))
        .collect();
    to_c_string(serde_json::to_string(&arr).unwrap_or_else(|_| "[]".to_string()))
}

/// 手动加词(走学成置顶固定权重)。返回 1=成功, 0=失败。
#[no_mangle]
pub extern "C" fn prisir_ime_user_add(handle: *mut c_void, pinyin: *const c_char, word: *const c_char) -> i32 {
    let (Some(p), Some(w)) = (cstr(pinyin), cstr(word)) else {
        return 0;
    };
    match get(handle) {
        Some(engine) if engine.user_add(p, w).is_ok() => 1,
        _ => 0,
    }
}

/// 删除某条学习词。返回删除行数(0=不存在/失败)。
#[no_mangle]
pub extern "C" fn prisir_ime_user_remove(handle: *mut c_void, pinyin: *const c_char, word: *const c_char) -> i32 {
    let (Some(p), Some(w)) = (cstr(pinyin), cstr(word)) else {
        return 0;
    };
    match get(handle) {
        Some(engine) => engine.user_remove(p, w).unwrap_or(0) as i32,
        None => 0,
    }
}

/// 清空全部学习记录。返回 1=成功, 0=失败。
#[no_mangle]
pub extern "C" fn prisir_ime_user_clear(handle: *mut c_void) -> i32 {
    match get(handle) {
        Some(engine) if engine.user_clear().is_ok() => 1,
        _ => 0,
    }
}

/// 搜索主词库(只读)→ JSON 数组 [{"key":"..","value":"..","weight":N,"source":"phrase|pinyin"},...]
/// 词库管理窗口「全部词库」查询用。主库只读,只能查不能改。调用方须 `prisir_ime_free_string` 释放。
#[no_mangle]
pub extern "C" fn prisir_ime_dict_search(handle: *mut c_void, term: *const c_char) -> *mut c_char {
    let Some(t) = cstr(term) else {
        return to_c_string("[]".to_string());
    };
    let Some(engine) = get(handle) else {
        return to_c_string("[]".to_string());
    };
    let rows = engine.search_main_dict(t, 200);
    let arr: Vec<serde_json::Value> = rows
        .iter()
        .map(|(k, v, w, s)| serde_json::json!({"key": k, "value": v, "weight": w, "source": s}))
        .collect();
    to_c_string(serde_json::to_string(&arr).unwrap_or_else(|_| "[]".to_string()))
}

/// 分页搜索主词库(2026-09-04 灵犀式分页)→ 同 dict_search 的 JSON 数组。
/// offset 跳过前 N 条;空 term 返回全表 top(按权重)。调用方须 `prisir_ime_free_string` 释放。
#[no_mangle]
pub extern "C" fn prisir_ime_dict_search_page(
    handle: *mut c_void,
    term: *const c_char,
    limit: i32,
    offset: i32,
) -> *mut c_char {
    let Some(t) = cstr(term) else {
        return to_c_string("[]".to_string());
    };
    let Some(engine) = get(handle) else {
        return to_c_string("[]".to_string());
    };
    let lim = if limit <= 0 { 200 } else { limit as usize };
    let off = if offset < 0 { 0 } else { offset as usize };
    let rows = engine.search_main_dict_page(t, lim, off);
    let arr: Vec<serde_json::Value> = rows
        .iter()
        .map(|(k, v, w, s)| serde_json::json!({"key": k, "value": v, "weight": w, "source": s}))
        .collect();
    to_c_string(serde_json::to_string(&arr).unwrap_or_else(|_| "[]".to_string()))
}

/// 主库匹配总条数(分页「共 N 条」)。空 term = 全表总数。
#[no_mangle]
pub extern "C" fn prisir_ime_dict_count(handle: *mut c_void, term: *const c_char) -> i64 {
    let Some(t) = cstr(term) else {
        return 0;
    };
    match get(handle) {
        Some(engine) => engine.main_dict_count(t) as i64,
        None => 0,
    }
}

// ============================================================
// 主库可写(2026-09-04,恢复外挂式删/加/改权重,用户拍板)
// 走独立短时可写连接(见 db.rs),运行时只读引擎不受影响。
// ============================================================

/// 主库删除词条。返回删除行数(0=不存在/失败)。
#[no_mangle]
pub extern "C" fn prisir_ime_main_delete(handle: *mut c_void, pinyin: *const c_char, word: *const c_char) -> i32 {
    let (Some(p), Some(w)) = (cstr(pinyin), cstr(word)) else {
        return 0;
    };
    match get(handle) {
        Some(engine) => engine.main_delete_word(p, w).unwrap_or(0) as i32,
        None => 0,
    }
}

/// 主库 upsert 词条(加词/改权重)。返回 1=成功, 0=失败。
#[no_mangle]
pub extern "C" fn prisir_ime_main_upsert(handle: *mut c_void, pinyin: *const c_char, word: *const c_char, weight: i64) -> i32 {
    let (Some(p), Some(w)) = (cstr(pinyin), cstr(word)) else {
        return 0;
    };
    match get(handle) {
        Some(engine) => match engine.main_upsert_word(p, w, weight) {
            Ok(true) => 1,
            _ => 0,
        },
        None => 0,
    }
}

/// 同 key 主库 phrase 最高词频(改权重参考)。返回权重(0=无)。
#[no_mangle]
pub extern "C" fn prisir_ime_main_max_weight(handle: *mut c_void, pinyin: *const c_char) -> i64 {
    let Some(p) = cstr(pinyin) else {
        return 0;
    };
    match get(handle) {
        Some(engine) => engine.main_max_weight(p),
        None => 0,
    }
}
