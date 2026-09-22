//! config_loader.rs — Tauri 壳侧 PrisirAI 三端配置加载器(2026-09-22, P2.5+15)
//!
//! 镜像 Python 端 [`prisIrai_config.py`] + Electron 端
//! [`prisiragent-shell/config_loader.js`] 的存储契约:
//!   1) 模块默认(代码内置)
//!   2) YAML 文件(同级目录 / $INSTDIR / cwd / 用户数据目录 四候选)
//!
//! YAML 子集:key: value / 嵌套缩进 2 空格 / # 注释。零外部依赖(不引入 serde_yaml)。
//!
//! 字段(必须与 Python/Electron 端字段名严格对齐):
//!   ports.web / companion / music / calendar
//!   brand.url / max_per_run / interval_sec / seen_cap
//!   forum.url / board / hint

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::PathBuf;
use std::sync::OnceLock;

/// 模块内置默认(必须与 Python 端 prisIrai_config.py 一致)
fn defaults() -> &'static HashMap<String, String> {
    static DEFAULTS: OnceLock<HashMap<String, String>> = OnceLock::new();
    DEFAULTS.get_or_init(|| {
        let mut m = HashMap::new();
        m.insert("ports.web".to_string(), "18802".to_string());
        m.insert("ports.companion".to_string(), "18850".to_string());
        m.insert("ports.music".to_string(), "0".to_string());
        m.insert("ports.calendar".to_string(), "18803".to_string());
        m.insert(
            "brand.url".to_string(),
            "https://www.babelspan.com/updates.json".to_string(),
        );
        m.insert("brand.max_per_run".to_string(), "3".to_string());
        m.insert("brand.interval_sec".to_string(), "86400".to_string());
        m.insert("brand.seen_cap".to_string(), "100".to_string());
        m.insert(
            "forum.url".to_string(),
            "https://bbs.babelspan.com/forum.html".to_string(),
        );
        m.insert("forum.board".to_string(), "browser/shell".to_string());
        m.insert("forum.hint".to_string(), "prisirai".to_string());
        m
    })
}

/// YAML 文件路径候选(顺序:开发态同级 > $INSTDIR > 用户数据目录 > cwd)
fn yaml_candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();

    // 1) 跟当前 crate 同级(开发态:Cargo.toml 在 src-tauri/,yml 在 prisiragent-tauri/)
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            let mut p = parent.to_path_buf();
            p.push("prisIrai_config.yaml");
            if p.exists() {
                out.push(p);
            }
            // cargo run:target/debug/...  → 3 级回溯到仓库根
            let mut back = parent.to_path_buf();
            for _ in 0..4 {
                back.pop();
                let mut q = back.clone();
                q.push("prisIrai_config.yaml");
                if q.exists() && !out.contains(&q) {
                    out.push(q);
                }
            }
        }
    }

    // 2) $INSTDIR(NSIS 装包后)
    if let Ok(instdir) = env::var("INSTDIR") {
        let mut p = PathBuf::from(instdir);
        p.push("prisIrai_config.yaml");
        out.push(p);
    }

    // 3) 用户数据目录(Win:%APPDATA%/prisirai-shell/prisIrai_config.yaml)
    if let Some(mut d) = dirs::data_dir() {
        d.push("prisirai-shell");
        d.push("prisIrai_config.yaml");
        out.push(d);
    }

    // 4) cwd
    let cwd_rel = PathBuf::from("prisIrai_config.yaml");
    out.push(cwd_rel);

    out
}

/// 极简 YAML 解析:section: / 2 空格缩进 key: value。空行 + # 注释跳过。
/// 不会去 quote("foo"/'foo'),由 get() 端处理。
fn parse_yaml(text: &str) -> HashMap<String, String> {
    let mut flat: HashMap<String, String> = HashMap::new();
    let mut cur_sec = String::new();
    for line in text.lines() {
        let s = line.trim_end();
        if s.trim().is_empty() || s.trim().starts_with('#') {
            continue;
        }
        // section: foo
        if let Some(name) = s
            .strip_suffix(':')
            .and_then(|s| s.trim_start().strip_prefix(|c: char| {
                c.is_ascii_alphabetic() || c == '_'
            }))
        {
            // s 是去掉冒号的形式;但我们要的是整段名。重新解析整行
            if let Some(rest) = s.strip_suffix(':') {
                let name2 = rest.trim();
                if name2
                    .chars()
                    .next()
                    .map(|c| c.is_ascii_alphabetic() || c == '_')
                    .unwrap_or(false)
                {
                    cur_sec = name2.to_string();
                    continue;
                }
            }
            let _ = name;
        }
        // 2 空格缩进 key: value
        if let Some(rest) = s.strip_prefix("  ") {
            // 拆 key: value(允许行尾注释)
            if let Some(colon_pos) = rest.find(':') {
                let key = rest[..colon_pos].trim();
                let raw_value = rest[colon_pos + 1..].trim();
                // 去行尾注释(以 # 开头,但不在引号内 — 极简不做引号状态)
                let value = match raw_value.find(" #") {
                    Some(i) => &raw_value[..i],
                    None => match raw_value.find('\t') {
                        Some(i) => &raw_value[..i],
                        None => raw_value,
                    },
                };
                let value = value.trim().trim_matches('"').trim_matches('\'');
                if !cur_sec.is_empty() && !key.is_empty() {
                    flat.insert(format!("{}.{}", cur_sec, key), value.to_string());
                }
            }
        }
    }
    flat
}

/// 全局缓存(进程级单例)
fn flat() -> &'static HashMap<String, String> {
    static FLAT: OnceLock<HashMap<String, String>> = OnceLock::new();
    FLAT.get_or_init(|| {
        let mut merged = defaults().clone();
        for p in yaml_candidates() {
            if let Ok(text) = fs::read_to_string(&p) {
                let parsed = parse_yaml(&text);
                if !parsed.is_empty() {
                    for (k, v) in parsed {
                        merged.insert(k, v);
                    }
                    break; // 第一个有效 yaml 胜出
                }
            }
        }
        merged
    })
}

/// 公共 getter:取 string 字段(找不到或 yaml 解析失败返默认)
pub fn get(key: &str) -> String {
    flat()
        .get(key)
        .cloned()
        .unwrap_or_else(|| defaults().get(key).cloned().unwrap_or_default())
}

/// 公共 getter:取 u16 端口(0..=65535),越界返 0)
pub fn get_u16(key: &str) -> u16 {
    let s = get(key);
    s.parse::<u32>()
        .ok()
        .and_then(|n| u16::try_from(n).ok())
        .unwrap_or(0)
}

/// 公共 getter:取 u32(看 cast,提升 max_per_run / interval_sec / seen_cap)
pub fn get_u32(key: &str) -> u32 {
    let s = get(key);
    s.parse::<u32>().unwrap_or(0)
}

/// 便捷封装:端口默认(注意真端口优先级链走 port_config::read_port;
/// 这里只返 yaml 覆盖后的模块默认,不查 HKCU/JSON)
pub fn web_port_default() -> u16 {
    get_u16("ports.web")
}
pub fn companion_port_default() -> u16 {
    get_u16("ports.companion")
}
pub fn music_port_default() -> u16 {
    get_u16("ports.music")
}
pub fn calendar_port_default() -> u16 {
    get_u16("ports.calendar")
}

/// 品牌 / 论坛便捷封装
pub fn brand_url() -> String {
    get("brand.url")
}
pub fn brand_max_per_run() -> u32 {
    get_u32("brand.max_per_run")
}
pub fn brand_interval_ms() -> u64 {
    get_u32("brand.interval_sec") as u64 * 1000
}
pub fn brand_seen_cap() -> u32 {
    get_u32("brand.seen_cap")
}
pub fn forum_url() -> String {
    get("forum.url")
}
pub fn forum_board() -> String {
    get("forum.board")
}
pub fn forum_hint() -> String {
    get("forum.hint")
}
pub fn forum_full_url() -> String {
    format!(
        "{}#board={}&hint={}",
        forum_url(),
        forum_board(),
        forum_hint()
    )
}

// =============================================================================
// 单元测试
// =============================================================================
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_python() {
        assert_eq!(web_port_default(), 18802);
        assert_eq!(companion_port_default(), 18850);
        assert_eq!(music_port_default(), 0);
        assert_eq!(calendar_port_default(), 18803);
        assert_eq!(brand_max_per_run(), 3);
        assert_eq!(brand_interval_ms(), 86_400_000);
        assert_eq!(brand_seen_cap(), 100);
        assert_eq!(forum_board(), "browser/shell");
        assert_eq!(forum_hint(), "prisirai");
        assert!(forum_url().starts_with("https://bbs.babelspan.com"));
    }

    #[test]
    fn parse_yaml_simple() {
        let txt = "\
ports:
  web: 19000
  calendar: 19001
brand:
  url: https://example.com/feed.json
  max_per_run: 5
forum:
  board: my-board
  hint: my-hint
";
        let parsed = parse_yaml(txt);
        assert_eq!(parsed.get("ports.web"), Some(&"19000".to_string()));
        assert_eq!(parsed.get("ports.calendar"), Some(&"19001".to_string()));
        assert_eq!(
            parsed.get("brand.url"),
            Some(&"https://example.com/feed.json".to_string())
        );
        assert_eq!(parsed.get("brand.max_per_run"), Some(&"5".to_string()));
        assert_eq!(parsed.get("forum.board"), Some(&"my-board".to_string()));
        assert_eq!(parsed.get("forum.hint"), Some(&"my-hint".to_string()));
    }

    #[test]
    fn parse_yaml_quoted() {
        let txt = "\
forum:
  url: \"https://example.com/f.html\"
  hint: 'foo-bar'
";
        let parsed = parse_yaml(txt);
        assert_eq!(
            parsed.get("forum.url"),
            Some(&"https://example.com/f.html".to_string())
        );
        assert_eq!(parsed.get("forum.hint"), Some(&"foo-bar".to_string()));
    }

    #[test]
    fn parse_yaml_comments_and_blank_lines() {
        let txt = "\
# top comment
ports:
  # inner comment
  web: 18802
  music: 0
";
        let parsed = parse_yaml(txt);
        assert_eq!(parsed.get("ports.web"), Some(&"18802".to_string()));
        assert_eq!(parsed.get("ports.music"), Some(&"0".to_string()));
    }

    #[test]
    fn yaml_candidates_non_empty() {
        let v = yaml_candidates();
        assert!(!v.is_empty(), "至少有一个 yaml 路径候选");
    }
}