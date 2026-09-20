//! port_config.rs — Tauri 壳侧的端口配置(2026-09-19)
//!
//! 镜像 Python 端 [`companion/music/port_config.py`] 的存储契约:
//!   1) HKCU\Software\PrisirAI\<name>_port  (DWORD,主通道)
//!   2) <data_dir>/prisirai-shell/_prisir_registry/ports.json  (跨平台 fallback)
//!   3) 模块默认(代码内置)
//!
//! Rust 端只读(写入由 Python 端 `notify_port_changed` 负责),与既有音乐 web 流程保持一致:
//!   - Python 端 `prisiragent-music-web.py` 启动后写回 HKCU `music_port`
//!   - Rust 端 `port_config::read_port("music", 0)` 读取
//!
//! 字段名严格对齐 Python 端,跨语言双端解读一致:
//!   web_port / companion_port / music_port  (HKCU 值名)
//!   ports.json  (JSON 文件名)
//!   _prisir_registry  (目录名,与 port_registry.py 复用)

use std::fs;
use std::path::PathBuf;

#[cfg(target_os = "windows")]
use winreg::enums::HKEY_CURRENT_USER;
#[cfg(target_os = "windows")]
use winreg::RegKey;

// 模块默认(与 Python 端 port_config.py DEFAULT_* 一致)
pub const DEFAULT_WEB_PORT: u16 = 18802;       // prisIragent_web.py 主面板
pub const DEFAULT_COMPANION_PORT: u16 = 18850; // prisiragent-companion-web.py
pub const DEFAULT_MUSIC_PORT: u16 = 0;         // music web 动态分配

// Windows 注册表路径
const REG_KEY_PATH: &str = "Software\\PrisirAI";
const REG_VAL_SUFFIX: &str = "_port"; // web_port / companion_port / music_port

// 旧 music 端口注册表项(Python 端迁移用,Rust 端只读不删)
// 注:Rust 端首次 read_port("music") 时若 HKCU 还残留旧 music_port,
//     会一次性写到 JSON 然后删除旧值,与 Python 端 _migrate_legacy_music_port 同款行为。
const LEGACY_MUSIC_REG_VAL: &str = "music_port";

// 跨平台 fallback JSON 文件路径候选(顺序:用户数据目录 > cwd)
const _JSON_FILENAME: &str = "ports.json";
const _JSON_DIRNAME: &str = "_prisir_registry";
const _APP_DIRNAME: &str = "prisirai-shell";

/// 端口名 → HKCU 值名
fn reg_val_name(name: &str) -> String {
    format!("{}{}", name.to_lowercase(), REG_VAL_SUFFIX)
}

/// JSON 文件路径候选(沿用 Python 端路径约定)
fn json_path_candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();

    // 1) 用户数据目录(Win: %APPDATA%/prisirai-shell/_prisir_registry/ports.json)
    if let Some(mut d) = dirs::data_dir() {
        d.push(_APP_DIRNAME);
        d.push(_JSON_DIRNAME);
        d.push(_JSON_FILENAME);
        out.push(d);
    }

    // 2) cwd 下的 _prisir_registry/ports.json(开发态,Python 端 _REG_DIR 同样支持)
    let cwd_rel = PathBuf::from(_JSON_DIRNAME).join(_JSON_FILENAME);
    out.push(cwd_rel);

    out
}

/// 读 JSON fallback(name → port)
fn read_json(name: &str) -> Option<u16> {
    let data: serde_json::Value = {
        let mut parsed: Option<serde_json::Value> = None;
        for path in json_path_candidates() {
            if let Ok(s) = fs::read_to_string(&path) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                    parsed = Some(v);
                    break;
                }
            }
        }
        parsed?
    };
    data.get(name)
        .and_then(|v| v.as_u64())
        .and_then(|n| u16::try_from(n).ok())
}

/// 写 JSON fallback(给旧 music_port 迁移用,Rust 端不主动写新端口)
#[allow(dead_code)]
fn write_json(name: &str, port: u16) -> bool {
    for path in json_path_candidates() {
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        // 读已有 → 合并 → 写回
        let mut data: serde_json::Value = fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| serde_json::json!({}));
        if let Some(obj) = data.as_object_mut() {
            obj.insert(name.to_string(), serde_json::json!(port));
            if let Ok(s) = serde_json::to_string_pretty(&data) {
                if fs::write(&path, s).is_ok() {
                    return true;
                }
            }
        }
    }
    false
}

/// 读 HKCU 注册表(name → port)
#[cfg(target_os = "windows")]
fn read_winreg(name: &str) -> Option<u16> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let key = hkcu.open_subkey(REG_KEY_PATH).ok()?;
    let val_name = reg_val_name(name);
    match key.get_value::<u32, _>(&val_name) {
        Ok(v) => u16::try_from(v).ok(),
        Err(_) => None,
    }
}

#[cfg(not(target_os = "windows"))]
fn read_winreg(_name: &str) -> Option<u16> {
    None
}

/// 删除 HKCU 注册表项(给旧 music_port 迁移用)
#[cfg(target_os = "windows")]
#[allow(dead_code)]
fn delete_winreg(name: &str) -> bool {
    use winreg::enums::KEY_SET_VALUE;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    match hkcu.open_subkey_with_flags(REG_KEY_PATH, KEY_SET_VALUE) {
        Ok(key) => match key.delete_value(name) {
            Ok(()) => true,
            Err(_) => false,
        },
        Err(_) => true, // key 不存在 = 目标达成
    }
}

#[cfg(not(target_os = "windows"))]
#[allow(dead_code)]
fn delete_winreg(_name: &str) -> bool {
    false
}

/// 旧 music_port 迁移:Rust 端只迁移一次,与 Python 端 _migrate_legacy_music_port 行为一致。
/// 通过 JSON 标记位 `__music_legacy_migrated__` 保证幂等。
fn migrate_legacy_music_port() {
    // 若 JSON 已标记迁移过,跳过
    for path in json_path_candidates() {
        if let Ok(s) = fs::read_to_string(&path) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                if v.get("__music_legacy_migrated__").is_some() {
                    return;
                }
            }
        }
    }
    // 读旧 HKCU music_port
    #[cfg(target_os = "windows")]
    {
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        if let Ok(key) = hkcu.open_subkey(REG_KEY_PATH) {
            if let Ok(v) = key.get_value::<u32, _>(LEGACY_MUSIC_REG_VAL) {
                if let Ok(port) = u16::try_from(v) {
                    // 写 JSON(若 JSON 还没有 music 字段,避免覆盖新值)
                    for path in json_path_candidates() {
                        if let Some(parent) = path.parent() {
                            let _ = fs::create_dir_all(parent);
                        }
                        let mut data: serde_json::Value = fs::read_to_string(&path)
                            .ok()
                            .and_then(|s| serde_json::from_str(&s).ok())
                            .unwrap_or_else(|| serde_json::json!({}));
                        if let Some(obj) = data.as_object_mut() {
                            if !obj.contains_key("music") {
                                obj.insert("music".to_string(), serde_json::json!(port));
                            }
                            obj.insert(
                                "__music_legacy_migrated__".to_string(),
                                serde_json::json!(true),
                            );
                            if let Ok(s) = serde_json::to_string_pretty(&data) {
                                if fs::write(&path, s).is_ok() {
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    // 删旧 HKCU music_port
    let _ = delete_winreg(LEGACY_MUSIC_REG_VAL);
}

/// 读用户配置的端口。
///
/// 优先级:HKCU 注册表 → JSON fallback → 默认。
/// 端口值合法(1..=65535)才算,否则按 fallback 降级。
///
/// `name` ∈ {"web", "companion", "music"} 等;对 "music" 触发一次性旧注册表项迁移。
pub fn read_port(name: &str, default: u16) -> u16 {
    if name == "music" {
        migrate_legacy_music_port();
    }

    if let Some(p) = read_winreg(name) {
        if (1..=65535).contains(&p) {
            return p;
        }
    }

    if let Some(p) = read_json(name) {
        if (1..=65535).contains(&p) {
            return p;
        }
    }

    default
}

/// 便捷封装:读 web 端口(默认 18802)
pub fn read_web_port() -> u16 {
    read_port("web", DEFAULT_WEB_PORT)
}

/// 便捷封装:读 companion 端口(默认 18850)
pub fn read_companion_port() -> u16 {
    read_port("companion", DEFAULT_COMPANION_PORT)
}

/// 便捷封装:读 music 端口(默认 0,由 music web 自己动态分配)
pub fn read_music_port() -> u16 {
    read_port("music", DEFAULT_MUSIC_PORT)
}

// =============================================================================
// 单元测试
// =============================================================================
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reg_val_name_format() {
        assert_eq!(reg_val_name("web"), "web_port");
        assert_eq!(reg_val_name("companion"), "companion_port");
        assert_eq!(reg_val_name("music"), "music_port");
        assert_eq!(reg_val_name("WEB"), "web_port"); // 大小写归一
    }

    #[test]
    fn default_values_match_python() {
        // 必须与 Python 端 port_config.py DEFAULT_* 完全一致
        assert_eq!(DEFAULT_WEB_PORT, 18802);
        assert_eq!(DEFAULT_COMPANION_PORT, 18850);
        assert_eq!(DEFAULT_MUSIC_PORT, 0);
    }

    #[test]
    fn json_path_candidates_non_empty() {
        let c = json_path_candidates();
        assert!(!c.is_empty(), "至少有一个 JSON 路径候选");
    }
}