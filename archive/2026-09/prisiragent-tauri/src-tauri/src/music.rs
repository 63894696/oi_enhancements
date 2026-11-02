//! M3.29 music 子模块:music web 子进程 + 桌面歌词透明窗
//!
//! 设计:
//! - music web 子进程 = python prisiragent-music-web.py(独立进程,PrisirAI 主后端不直接管)
//! - 端口由 music web 自己动态分配,写 HKCU\Software\PrisirAI\music_port
//! - M3.34(2026-09-19): 改用统一 port_config::read_music_port()(替代本地 read_music_port),
//!   字段名与 Python 端 port_config.py 严格对齐(HKCU `music_port` + JSON `ports.json['music']`)
//! - lyrics-window Tauri 配置(transparent + decorations:false + alwaysOnTop)由 tauri.conf.json 定义
//! - 本模块提供 4 个 Tauri command 给前端调用:
//!     start_music_cmd   - 代启 music web 子进程
//!     open_lyrics_cmd   - 打开/唤起歌词透明窗
//!     close_lyrics_cmd  - 隐藏歌词窗
//!     music_status_cmd  - 返回 music web URL/port/歌词窗状态

use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::path::PathBuf;
use std::time::Duration;
use std::fs;
use std::io::Write;
use std::io::Read;
use std::net::TcpStream;

use tauri::Manager;

use crate::port_config;

const MUSIC_PORT_DEFAULT: u16 = 18880; // 后端 fallback(实际由 music web 动态分配)

/// 探测 music web 是否在监听
fn music_up() -> bool {
    // M3.34:music web 启动后写 HKCU `music_port`,壳读这里拿端口。
    // 注意 music_port 可能是 0(动态分配场景下还没写),此时返回 false 让上层继续等。
    let port = port_config::read_music_port();
    if port == 0 {
        return false;
    }
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", port).parse().unwrap(),
        Duration::from_millis(1500),
    )
    .is_ok()
}

/// 启动 music web(陪聊以外的独立进程)
pub fn start_music(state: &Arc<crate::AppState>) -> Result<u16, String> {
    let mut proc_guard = state.music_proc.lock().unwrap();
    if proc_guard.is_some() {
        // 已在跑,返回端口
        let port = port_config::read_music_port();
        if port == 0 {
            return Err("music running but port not yet registered".to_string());
        }
        return Ok(port);
    }

    // 端口已被别的进程占用 → 直接复用
    if music_up() {
        let port = port_config::read_music_port();
        log::info!("[startMusic] port already up, reusing port={}", port);
        if port == 0 {
            return Err("music up but port unknown".to_string());
        }
        return Ok(port);
    }

    // 找 music_web.py 脚本路径:
    //   开发态(从 src-tauri 跑)= ../companion/prisiragent-music-web.py
    //   装包后(壳 exe 同级)= ../companion/prisiragent-music-web.py
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    let candidates = vec![
        exe_dir.join("..").join("companion").join("prisiragent-music-web.py"),
        exe_dir.join("companion").join("prisiragent-music-web.py"),
    ];
    let script = candidates
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| "找不到 companion/prisiragent-music-web.py".to_string())?;

    log::info!("[startMusic] spawning script={}", script.display());

    let mut child = Command::new("python")
        .arg("-B")
        .arg(script.to_string_lossy().to_string())
        .arg("--port").arg("0")  // 动态分配
        .arg("--host").arg("127.0.0.1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn music web err: {}", e))?;

    log::info!("[music] spawned pid={}", child.id());

    // stdout/stderr 接日志文件
    let log_dir = dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("prisirai-shell")
        .join("logs");
    let _ = fs::create_dir_all(&log_dir);

    if let Some(stdout) = child.stdout.take() {
        let log_path = log_dir.join("music-stdout.log");
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            let mut reader = stdout;
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&log_path) {
                            let _ = f.write_all(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let log_path = log_dir.join("music-stderr.log");
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            let mut reader = stderr;
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&log_path) {
                            let _ = f.write_all(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }

    *proc_guard = Some(child);

    // 轮询等就绪(后台线程)
    let state_clone = Arc::clone(state);
    std::thread::spawn(move || {
        for _ in 0..50 {
            if music_up() {
                log::info!("[startMusic] music web ready port={}", port_config::read_music_port());
                return;
            }
            std::thread::sleep(Duration::from_millis(300));
        }
        log::error!("[startMusic] music web not ready within 15s");
    });

    let port = port_config::read_music_port();
    if port == 0 {
        Err("music spawned but port not yet registered".to_string())
    } else {
        Ok(port)
    }
}

/// 杀掉 music web
pub fn kill_music(state: &Arc<crate::AppState>) {
    let mut proc_guard = state.music_proc.lock().unwrap();
    if let Some(mut child) = proc_guard.take() {
        let _ = child.kill();
        let _ = child.wait();
        log::info!("[killMusic] killed");
    }
}

/// 打开/唤起歌词透明窗(music web 必须先起)
pub fn open_lyrics_window(app: &tauri::AppHandle) -> Result<(), String> {
    let port = port_config::read_music_port();
    if port == 0 {
        return Err("music web not running".to_string());
    }
    let url = format!("http://127.0.0.1:{}/lyrics", port);

    if let Some(win) = app.get_webview_window("lyrics-window") {
        // 已有 → 重设 URL(端口可能变了)+ 显示
        let _ = win.eval(&format!("window.location.href = '{}';", url));
        let _ = win.show();
        let _ = win.set_focus();
        let _ = win.set_always_on_top(true);
        log::info!("[openLyrics] re-focused lyrics window url={}", url);
        return Ok(());
    }

    Err("lyrics-window not configured — check tauri.conf.json".into())
}

/// 关闭歌词窗
pub fn close_lyrics_window(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("lyrics-window") {
        let _ = win.hide();
        log::info!("[closeLyrics] hidden");
        Ok(())
    } else {
        Err("lyrics-window not configured".into())
    }
}

/// music 状态(给前端查询)
pub fn music_status() -> serde_json::Value {
    let port = port_config::read_music_port();
    let alive = music_up();
    let port_opt: Option<u16> = if port == 0 { None } else { Some(port) };
    serde_json::json!({
        "ok": true,
        "running": alive,
        "port": port_opt,
        "url": port_opt.map(|p| format!("http://127.0.0.1:{}", p)),
        "lyrics_url": port_opt.map(|p| format!("http://127.0.0.1:{}/lyrics", p)),
    })
}
