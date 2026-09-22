//! PrisirAI Tauri 壳 — 替代 Electron 的轻量桌面壳
//!
//! 功能映射 (Electron main.js → Rust):
//!   ① spawn + 看护 Python 后端 (PrisirAI.exe --port 18802 --lan)
//!   ② 系统托盘 (显示/隐藏/退出)
//!   ③ 全局热键 Ctrl+Shift+O 呼出/隐藏
//!   ④ 开机自启 (可配)
//!   ⑤ 输入法 AI 按钮唤起 (监听命名事件 PrisirLingXi_AiToggle_Event)
//!   ⑥ 品牌通知轮询 (每日 GET babelspan.com/updates.json, 新条目弹通知)

use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::path::PathBuf;
use std::fs;
use std::net::TcpStream;
use std::time::Duration;
use std::io::Write;

use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{TrayIconBuilder, TrayIconEvent, MouseButton},
    Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_autostart::ManagerExt as AutostartExt;

mod music;
mod calendar;
mod port_config;
mod config_loader;

// ---------- 配置 ----------
// M3.34(2026-09-19): WEB_PORT / COMPANION_PORT 不再是写死常量。
// 启动时调 port_config::read_port() 拿用户配置的端口(HKCU 注册表 + JSON fallback);
// 子进程 stdout sentinel `PRISIR_WEB_READY port=<n>` 拿到真端口后,
// 会进一步覆盖 _real_web_port(应对冲突 fallback / OS 自动分配场景)。
// (默认常量由 port_config 模块导出,本文件不重复 import)
// M3.30 task #19 / P2.5+12: 日历页入口 — calendar 子进程由 calendar.rs 看护
// URL 不再写死 18880;改为调 calendar::current_url(app) 动态拿实际端口。
// (默认端口由 calendar.rs::CALENDAR_PORT_DEFAULT = 18803 提供)
const HOTKEY: &str = "ctrl+shift+o";
const AI_TOGGLE_EVENT: &str = "PrisirLingXi_AiToggle_Event";
// P2.5+15(2026-09-22):BRAND_* 从 prisIrai_config.yaml 读,默认值走 config_loader 模块;
// 镜像 Python 端 prisIrai_config.py + Electron 端 config_loader.js 的存储契约。
// brand_notify 周期走 brand_interval_ms(),默认 86400s = 24h。
fn brand_updates_url() -> String { config_loader::brand_url() }
fn brand_max_per_run() -> usize { config_loader::brand_max_per_run() as usize }

/// 全局状态
struct AppState {
    web_proc: Mutex<Option<Child>>,
    web_ready: Mutex<bool>,
    /// M3.34(2026-09-19): 子进程 stdout sentinel 解析出的真端口。
    /// None = sentinel 还没到(web 可能还没就绪);Some(n) = 真端口(可能因 fallback 与候选不同)。
    real_web_port: Mutex<Option<u16>>,
    /// M3.34(2026-09-19): 启动时从 HKCU / JSON 读到的候选端口(传给 --port)。
    /// 即便 sentinel 一直不来,也能用作 web_up() 探活的目标。
    configured_web_port: Mutex<u16>,
    companion_proc: Mutex<Option<Child>>,   // M3.27.3
    music_proc: Mutex<Option<Child>>,      // M3.29 music web 子进程
    quitting: Mutex<bool>,
}

impl AppState {
    fn new() -> Self {
        let cfg = port_config::read_web_port();
        Self {
            web_proc: Mutex::new(None),
            web_ready: Mutex::new(false),
            real_web_port: Mutex::new(None),
            configured_web_port: Mutex::new(cfg),
            companion_proc: Mutex::new(None),
            music_proc: Mutex::new(None),
            quitting: Mutex::new(false),
        }
    }
}

/// 当前 web 端口:sentinel 拿到 → 真端口;否则用启动期候选。
fn current_web_port(state: &Arc<AppState>) -> u16 {
    if let Some(p) = *state.real_web_port.lock().unwrap() {
        return p;
    }
    *state.configured_web_port.lock().unwrap()
}

/// 探测后端是否已在监听(用真端口;sentinel 未到时用候选)
fn web_up(state: &Arc<AppState>) -> bool {
    let port = current_web_port(state);
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", port).parse().unwrap(),
        Duration::from_millis(1500),
    )
    .is_ok()
}

/// 解析 PrisirAI.exe 路径(装包后 = 与壳同级;开发态 = ../dist/)
fn resolve_core_exe() -> PathBuf {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    let candidates = vec![
        exe_dir.join("PrisirAI.exe"),                    // 装包后
        exe_dir.join("..").join("dist").join("PrisirAI.exe"),  // 开发态
        exe_dir.join("..").join("PrisirAI.exe"),         // 备选
    ];

    for p in &candidates {
        if p.exists() {
            return p.clone();
        }
    }
    candidates[0].clone() // 默认返回第一个,让 spawn 报错时用户看到路径
}

/// 启动 Python 后端
fn start_backend(state: &Arc<AppState>) {
    let mut proc_guard = state.web_proc.lock().unwrap();
    if proc_guard.is_some() {
        return; // 已在跑
    }

    // 已被别的进程占用端口就直接复用(用当前 web 端口;sentinel 未到时是候选值)
    if web_up(state) {
        let port = current_web_port(state);
        log::info!("[startWeb] port already up, reusing port={}", port);
        *state.web_ready.lock().unwrap() = true;
        return;
    }

    let core_exe = resolve_core_exe();
    let use_exe = core_exe.exists();
    // 双 fallback:PRISIRAGENT_SHELL_NO_LAN 优先,旧名 OIAGENT_SHELL_NO_LAN 兼容
    let want_lan = std::env::var("PRISIRAGENT_SHELL_NO_LAN")
        .or_else(|_| std::env::var("OIAGENT_SHELL_NO_LAN"))
        .is_err();

    // M3.34(2026-09-19): 启动端口从 HKCU 注册表 / JSON fallback / 默认常量逐级取;
    // 不再写死 18802(端口冲突或 OS 自动分配场景下子进程会跑在别的端口,
    // 我们靠 sentinel PRISIR_WEB_READY port=<n> 拿到真端口)
    let start_port = *state.configured_web_port.lock().unwrap();
    log::info!("[startWeb] configured web_port={} (from HKCU/JSON/default)", start_port);

    let (cmd, args): (PathBuf, Vec<String>) = if use_exe {
        let mut a = vec!["--port".to_string(), start_port.to_string()];
        if want_lan {
            a.push("--lan".to_string());
        }
        (core_exe, a)
    } else {
        // 开发态回退: python prisiragent_web.py
        let mut a = vec![
            "prisiragent_web.py".to_string(),
            "--port".to_string(),
            start_port.to_string(),
        ];
        if want_lan {
            a.push("--lan".to_string());
        }
        (PathBuf::from("python"), a)
    };

    log::info!("[startWeb] spawning backend cmd={} args={:?}", cmd.display(), args);

    match Command::new(&cmd)
        .args(&args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(mut child) => {
            log::info!("[webProc] spawned pid={}", child.id());

            // 把 stdout/stderr 接到日志目录
            let log_dir = dirs::data_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join("prisirai-shell")
                .join("logs");
            let _ = fs::create_dir_all(&log_dir);

            // stderr 走老路径(append 到 spawn-stderr.log)
            if let Some(stderr) = child.stderr.take() {
                let log_path = log_dir.join("spawn-stderr.log");
                std::thread::spawn(move || {
                    use std::io::Read;
                    let mut buf = [0u8; 4096];
                    let mut reader = stderr;
                    loop {
                        match reader.read(&mut buf) {
                            Ok(0) => break,
                            Ok(n) => {
                                if let Ok(mut f) = fs::OpenOptions::new()
                                    .create(true)
                                    .append(true)
                                    .open(&log_path)
                                {
                                    let _ = f.write_all(&buf[..n]);
                                }
                            }
                            Err(_) => break,
                        }
                    }
                });
            }

            // M3.34(2026-09-19): stdout 一个线程搞定两件事——
            //   1) 落盘到 spawn-stdout.log(沿用既有逻辑,字节级 append)
            //   2) 行级扫描 sentinel `PRISIR_WEB_READY port=<n>`,命中后写
            //      state.real_web_port,触发 web_up() / web_ready 改用真端口
            //
            // 设计要点:ChildStdout 不是 Clone,BufReader 同步读也只有 owner,
            // 所以 sentinel 扫描由这一个线程串行处理(同时负责落盘)。
            // 替代方案是用 mpsc channel 分两路,但行级解析 + 落盘一起做更省内存。
            if let Some(stdout) = child.stdout.take() {
                let state_for_sentinel = Arc::clone(state);
                let log_path = log_dir.join("spawn-stdout.log");
                std::thread::spawn(move || {
                    use std::io::{BufRead, BufReader, Read};
                    let mut reader = BufReader::new(stdout);
                    let mut pending = Vec::with_capacity(4096);
                    let mut carry = Vec::with_capacity(256); // 行缓冲(可能有跨 read 边界)
                    loop {
                        let mut buf = [0u8; 4096];
                        match reader.read(&mut buf) {
                            Ok(0) => break,
                            Ok(n) => {
                                // 落盘:append 原始字节
                                if let Ok(mut f) = fs::OpenOptions::new()
                                    .create(true)
                                    .append(true)
                                    .open(&log_path)
                                {
                                    let _ = f.write_all(&buf[..n]);
                                }
                                // 行级扫描 sentinel:把 carry + 新字节一起处理
                                carry.extend_from_slice(&buf[..n]);
                                while let Some(pos) = carry.iter().position(|&b| b == b'\n') {
                                    let line: Vec<u8> = carry.drain(..=pos).collect();
                                    let line = &line[..line.len() - 1]; // 去 \n
                                    pending.push(line.to_vec());
                                }
                                // 扫所有攒下来的行,找 sentinel
                                let mut i = 0;
                                while i < pending.len() {
                                    let line = &pending[i];
                                    if let Ok(s) = std::str::from_utf8(line) {
                                        // 匹配 "[prisIragent_web] PRISIR_WEB_READY port=<n>"
                                        if let Some(idx) = s.find("PRISIR_WEB_READY port=") {
                                            let rest = &s[idx + "PRISIR_WEB_READY port=".len()..];
                                            // 取数字部分到第一个非数字
                                            let num_str: String =
                                                rest.chars().take_while(|c| c.is_ascii_digit()).collect();
                                            if let Ok(port) = num_str.parse::<u16>() {
                                                log::info!(
                                                    "[startWeb] sentinel parsed real_port={}",
                                                    port
                                                );
                                                *state_for_sentinel.real_web_port.lock().unwrap() =
                                                    Some(port);
                                                *state_for_sentinel.web_ready.lock().unwrap() = true;
                                            }
                                        }
                                    }
                                    i += 1;
                                }
                                pending.clear();
                            }
                            Err(_) => break,
                        }
                    }
                    // EOF:再处理 carry 里残余
                    if !carry.is_empty() {
                        if let Ok(s) = std::str::from_utf8(&carry) {
                            if let Some(idx) = s.find("PRISIR_WEB_READY port=") {
                                let rest = &s[idx + "PRISIR_WEB_READY port=".len()..];
                                let num_str: String =
                                    rest.chars().take_while(|c| c.is_ascii_digit()).collect();
                                if let Ok(port) = num_str.parse::<u16>() {
                                    log::info!(
                                        "[startWeb] sentinel (EOF) parsed real_port={}",
                                        port
                                    );
                                    *state_for_sentinel.real_web_port.lock().unwrap() = Some(port);
                                    *state_for_sentinel.web_ready.lock().unwrap() = true;
                                }
                            }
                        }
                    }
                });
            }

            *proc_guard = Some(child);

            // 轮询等就绪(后台线程)。M3.34:即便 sentinel 没来,只要 TCP 探到就算 ready。
            let state_clone = Arc::clone(state);
            std::thread::spawn(move || {
                for _ in 0..150 {
                    // 60s / 400ms,放宽到 60s 覆盖冷启动 + LLM 加载
                    if web_up(&state_clone) {
                        *state_clone.web_ready.lock().unwrap() = true;
                        let p = current_web_port(&state_clone);
                        log::info!("[startWeb] backend ready port={}", p);
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                log::error!("[startWeb] backend not ready within 60s");
            });
        }
        Err(e) => {
            log::error!("[startWeb] spawn failed err={}", e);
        }
    }
}

/// 杀掉后端
fn kill_backend(state: &Arc<AppState>) {
    let mut proc_guard = state.web_proc.lock().unwrap();
    if let Some(mut child) = proc_guard.take() {
        let _ = child.kill();
        log::info!("[killBackend] killed pid={}", child.id());
    }
    // P2.5+12: 兜底扫孤儿 python 后端进程
    // (子进程派生的孙子进程 / 上一轮没 kill 干净的残留 / 用户手动启的 dev server)
    kill_orphan_backends("prisiragent_web.py");
}

/// 兜底扫 + 杀孤儿 python 后端进程(参考 Electron main.js:552-567 同款 PowerShell CIM 扫描)
#[cfg(target_os = "windows")]
fn kill_orphan_backends(script_basename: &str) {
    // 用 PowerShell 调 CIM 拿所有 python.exe 的命令行,过滤含目标脚本的进程
    // (避免误杀无关 python:Process Lasso / AnyTXT / 用户的 jupyter 等)
    let ps_cmd = format!(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" \
         | Where-Object {{ $_.CommandLine -like '*{}*' }} \
         | Select-Object -ExpandProperty ProcessId",
        script_basename
    );
    match Command::new("powershell")
        .args(["-NoProfile", "-Command", &ps_cmd])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
    {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let mut killed = 0;
            for line in stdout.lines() {
                let pid: u32 = match line.trim().parse() {
                    Ok(n) => n,
                    Err(_) => continue,
                };
                // taskkill /F /T 杀进程树(覆盖 python 派生的子进程)
                let _ = Command::new("taskkill")
                    .args(["/F", "/T", "/PID", &pid.to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .output();
                killed += 1;
                log::warn!("[killOrphan] killed orphan {} pid={}", script_basename, pid);
            }
            if killed == 0 {
                log::info!(
                    "[killOrphan] no orphan {} found",
                    script_basename
                );
            }
        }
        Err(e) => {
            log::warn!(
                "[killOrphan] powershell scan err for {}: {}",
                script_basename,
                e
            );
        }
    }
}

#[cfg(not(target_os = "windows"))]
fn kill_orphan_backends(_script_basename: &str) {
    // 非 Windows 跳过(参考 Electron main.js 的 Windows-only 实现)
    log::info!("[killOrphan] non-Windows: skip orphan scan");
}

/// 探测 companion 是否已在监听(从 port_config 读,默认 18850)
fn companion_up() -> bool {
    let port = port_config::read_companion_port();
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", port).parse().unwrap(),
        Duration::from_millis(1500),
    )
    .is_ok()
}

/// 启动 companion(陪聊)服务
fn start_companion(state: &Arc<AppState>) -> Result<(), String> {
    let mut proc_guard = state.companion_proc.lock().unwrap();
    if proc_guard.is_some() {
        return Ok(()); // 已在跑
    }

    // 端口已被别的进程占用 → 直接复用,不开新进程
    if companion_up() {
        let port = port_config::read_companion_port();
        log::info!("[startCompanion] port already up, reusing port={}", port);
        return Ok(());
    }

    let companion_port = port_config::read_companion_port();

    // 找 companion 脚本路径:
    //   开发态(从 src-tauri 跑)= ../companion/prisiragent-companion-web.py
    //   装包后(壳 exe 同级)= ../companion/prisiragent-companion-web.py
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    let candidates = vec![
        exe_dir.join("..").join("companion").join("prisiragent-companion-web.py"),
        exe_dir.join("companion").join("prisiragent-companion-web.py"),
    ];
    let script = candidates
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| {
            format!(
                "找不到 companion/prisiragent-companion-web.py(尝试过 {} 个路径)",
                2
            )
        })?;

    log::info!(
        "[startCompanion] spawning script={} port={}",
        script.display(),
        companion_port
    );

    let mut child = Command::new("python")
        .arg("-B")
        .arg(script.to_string_lossy().to_string())
        .arg("--port")
        .arg(companion_port.to_string())
        .arg("--host")
        .arg("127.0.0.1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn companion err: {}", e))?;

    log::info!("[companion] spawned pid={}", child.id());

    // stdout/stderr 接日志文件(简单 append)
    let log_dir = dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("prisirai-shell")
        .join("logs");
    let _ = fs::create_dir_all(&log_dir);

    if let Some(stdout) = child.stdout.take() {
        let log_path = log_dir.join("companion-stdout.log");
        std::thread::spawn(move || {
            use std::io::Read;
            let mut buf = [0u8; 4096];
            let mut reader = stdout;
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut f) = fs::OpenOptions::new()
                            .create(true)
                            .append(true)
                            .open(&log_path)
                        {
                            let _ = f.write_all(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let log_path = log_dir.join("companion-stderr.log");
        std::thread::spawn(move || {
            use std::io::Read;
            let mut buf = [0u8; 4096];
            let mut reader = stderr;
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut f) = fs::OpenOptions::new()
                            .create(true)
                            .append(true)
                            .open(&log_path)
                        {
                            let _ = f.write_all(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }

    *proc_guard = Some(child);
    log::info!("[companion] started port={}", companion_port);
    Ok(())
}

/// 杀掉 companion
fn kill_companion(state: &Arc<AppState>) {
    let mut proc_guard = state.companion_proc.lock().unwrap();
    if let Some(mut child) = proc_guard.take() {
        let _ = child.kill();
        let _ = child.wait();
        log::info!("[killCompanion] killed");
    }
    // P2.5+12: 兜底扫孤儿 companion / music 子进程
    kill_orphan_backends("prisiragent-companion-web.py");
    kill_orphan_backends("prisiragent-music-web.py");
}

/// 输入法 AI 按钮唤起监听 (Windows 命名事件)
#[cfg(target_os = "windows")]
fn start_ai_toggle_listener(app_handle: tauri::AppHandle) {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use windows::Win32::Foundation::WAIT_OBJECT_0;
    use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};

    std::thread::spawn(move || {
        let event_name: Vec<u16> = OsStr::new(AI_TOGGLE_EVENT)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();

        unsafe {
            let handle = CreateEventW(
                None,
                false, // auto-reset
                false, // initial state
                windows::core::PCWSTR(event_name.as_ptr()),
            );

            match handle {
                Ok(h) => {
                    log::info!("[aiToggle] listener started event={}", AI_TOGGLE_EVENT);
                    loop {
                        let result = WaitForSingleObject(h, 0xFFFFFFFF); // INFINITE
                        if result == WAIT_OBJECT_0 {
                            log::info!("[aiToggle] event -> bringToFront");
                            if let Some(win) = app_handle.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                                let _ = win.unminimize();
                            }
                        }
                    }
                }
                Err(e) => {
                    log::error!("[aiToggle] CreateEventW failed err={}", e);
                }
            }
        }
    });
}

#[cfg(not(target_os = "windows"))]
fn start_ai_toggle_listener(_app_handle: tauri::AppHandle) {
    // 非 Windows 跳过
}

/// 品牌通知:每日轮询 babelspan.com/updates.json
fn start_brand_notify(app_handle: tauri::AppHandle) {
    std::thread::spawn(move || {
        // 检查是否被禁用
        let disabled_path = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("prisirai-shell")
            .join("brand-notify-disabled");
        if disabled_path.exists() {
            log::info!("[brandNotify] disabled by user");
            return;
        }

        let seen_path = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("prisirai-shell")
            .join("brand-notify-seen.json");

        loop {
            // 拉取更新
            let items: Vec<serde_json::Value> = match reqwest::blocking::get(brand_updates_url()) {
                Ok(resp) => {
                    if resp.status().is_success() {
                        match resp.json::<serde_json::Value>() {
                            Ok(data) => data
                                .get("items")
                                .and_then(|v| v.as_array())
                                .cloned()
                                .unwrap_or_default(),
                            Err(_) => vec![],
                        }
                    } else {
                        vec![]
                    }
                }
                Err(_) => vec![],
            };

            if !items.is_empty() {
                // 加载已见 id
                let mut seen: Vec<String> = fs::read_to_string(&seen_path)
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();
                let seen_set: std::collections::HashSet<&String> = seen.iter().collect();

                let fresh: Vec<_> = items
                    .iter()
                    .filter(|it| {
                        it.get("id")
                            .and_then(|v| v.as_str())
                            .map(|id| !seen_set.contains(&id.to_string()))
                            .unwrap_or(false)
                    })
                    .take(brand_max_per_run())
                    .collect();

                for item in fresh {
                    let title = item
                        .get("title")
                        .and_then(|v| v.as_str())
                        .unwrap_or("更新");
                    let body = item
                        .get("body")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let _ = app_handle
                        .notification()
                        .builder()
                        .title(format!("Prisir · {}", &title[..title.len().min(80)]))
                        .body(&body[..body.len().min(200)])
                        .show();

                    if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                        seen.push(id.to_string());
                    }
                }

                // 截断 seen
                while seen.len() > 100 {
                    seen.remove(0);
                }
                let _ = fs::write(&seen_path, serde_json::to_string(&seen).unwrap_or_default());
            }

            // 每日 — P2.5+15 周期走 prisIrai_config.yaml brand.interval_sec(默认 86400s)
            let _ms = config_loader::brand_interval_ms();
            std::thread::sleep(Duration::from_millis(_ms.max(60_000)));
        }
    });
}

/// Tauri 命令: 获取壳信息
#[tauri::command]
fn shell_info(state: tauri::State<Arc<AppState>>) -> serde_json::Value {
    let ready = *state.web_ready.lock().unwrap();
    let port = current_web_port(&state);
    let web_url = format!("http://127.0.0.1:{}", port);
    let companion_port = port_config::read_companion_port();
    let companion_url = format!("http://127.0.0.1:{}", companion_port);
    serde_json::json!({
        "webUrl": web_url,
        "webPort": port,
        "webReady": ready,
        "companionUrl": companion_url,
        "companionPort": companion_port,
        "version": env!("CARGO_PKG_VERSION"),
    })
}

/// Tauri 命令: 切换窗口显示/隐藏
#[tauri::command]
fn shell_toggle(app: tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        if win.is_visible().unwrap_or(false) && win.is_focused().unwrap_or(false) {
            let _ = win.hide();
        } else {
            let _ = win.show();
            let _ = win.set_focus();
        }
    }
}

/// Tauri 命令: 打开外部链接(白名单)
#[tauri::command]
fn shell_open_external(url: String) -> serde_json::Value {
    if !url.starts_with("https://") {
        return serde_json::json!({"ok": false, "error": "https-only"});
    }
    let host = url::Url::parse(&url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
        .unwrap_or_default();
    if host != "bbs.babelspan.com" && host != "babelspan.com" && host != "www.babelspan.com" {
        return serde_json::json!({"ok": false, "error": "host not in allowlist"});
    }
    match open::that(&url) {
        Ok(_) => serde_json::json!({"ok": true}),
        Err(e) => serde_json::json!({"ok": false, "error": e.to_string()}),
    }
}

/// M3.27.3 Tauri 命令: 代启 companion(顶栏「📞 陪聊」按钮 / 托盘「启动陪聊」调用)
#[tauri::command]
fn start_companion_cmd(
    state: tauri::State<Arc<AppState>>,
) -> serde_json::Value {
    match start_companion(&state) {
        Ok(()) => {
            let port = port_config::read_companion_port();
            serde_json::json!({
                "ok": true,
                "url": format!("http://127.0.0.1:{}", port),
                "port": port,
            })
        }
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

// ===== M3.29 music Tauri commands =====

/// 代启 music web 子进程
#[tauri::command]
fn start_music_cmd(state: tauri::State<Arc<AppState>>) -> serde_json::Value {
    match music::start_music(&state) {
        Ok(port) => serde_json::json!({
            "ok": true,
            "port": port,
            "url": format!("http://127.0.0.1:{}", port),
        }),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

/// 打开/唤起歌词透明窗
#[tauri::command]
fn open_lyrics_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match music::open_lyrics_window(&app) {
        Ok(()) => serde_json::json!(music::music_status()),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

/// 隐藏歌词窗
#[tauri::command]
fn close_lyrics_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match music::close_lyrics_window(&app) {
        Ok(()) => serde_json::json!({"ok": true}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

/// music 状态查询
#[tauri::command]
fn music_status_cmd() -> serde_json::Value {
    music::music_status()
}

// ===== P2.5+12 calendar Tauri command =====

/// 代启 calendar 子进程(顶栏「📅 日历」按钮 / 托盘「📅 打开日历」调用)
#[tauri::command]
async fn start_calendar_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match calendar::start(&app).await {
        Ok(port) => serde_json::json!({
            "ok": true,
            "port": port,
            "url": format!("http://127.0.0.1:{}/prisiragent/calendar", port),
        }),
        Err(e) => serde_json::json!({"ok": false, "error": e.to_string()}),
    }
}

/// calendar 状态查询
#[tauri::command]
fn calendar_status_cmd(app: tauri::AppHandle) -> serde_json::Value {
    let url = calendar::current_url(&app);
    serde_json::json!({
        "running": calendar::is_running(&app),
        "port": calendar::current_port(&app),
        "url": url,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = Arc::new(AppState::new());

    tauri::Builder::default()
        // P2.5+12: single-instance — 第二个进程启动时拦下,焦点放回已有窗口
        // (双开场景在 Windows 上会撞 18802 端口 + 跑两个托盘,体验差)
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            log::warn!("[singleInstance] second instance blocked, focusing main window");
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build())
        .plugin(tauri_plugin_autostart::Builder::new().build())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![shell_info, shell_toggle, shell_open_external, start_companion_cmd, start_music_cmd, open_lyrics_cmd, close_lyrics_cmd, music_status_cmd, start_calendar_cmd, calendar_status_cmd])
        .setup(move |app| {
            let app_handle = app.handle().clone();
            let state_clone = Arc::clone(&state);

            // ---------- ① 启动后端 ----------
            start_backend(&state_clone);

            // ---------- ② 系统托盘 ----------
            let show_item = MenuItemBuilder::with_id("show", "打开 PrisirAI").build(app)?;
            let autostart_item = MenuItemBuilder::with_id("autostart", "开机自启").build(app)?;
            let companion_item = MenuItemBuilder::with_id("companion", "启动陪聊").build(app)?;
            let music_item = MenuItemBuilder::with_id("music", "启动音乐播放器").build(app)?;
            // M3.30 task #19: 右键菜单追加「打开日历」
            let calendar_item = MenuItemBuilder::with_id("open_calendar", "📅 打开日历").build(app)?;
            let lyrics_item = MenuItemBuilder::with_id("lyrics", "桌面歌词(开/关)").build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "退出").build(app)?;
            let menu = MenuBuilder::new(app)
                .item(&show_item)
                .item(&companion_item)
                .item(&music_item)
                .item(&calendar_item)
                .item(&lyrics_item)
                .item(&autostart_item)
                .separator()
                .item(&quit_item)
                .build()?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .tooltip("Prisir(湃睿思) AI")
                .menu(&menu)
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "show" => {
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                        "autostart" => {
                            let autostart = app.autolaunch();
                            let enabled = autostart.is_enabled().unwrap_or(false);
                            if enabled {
                                let _ = autostart.disable();
                            } else {
                                let _ = autostart.enable();
                            }
                        }
                        "companion" => {
                            // M3.27.3: 托盘「启动陪聊」→ 代启 companion 服务
                            let state = app.state::<Arc<AppState>>();
                            if let Err(e) = start_companion(&state) {
                                log::error!("[companion] start err: {}", e);
                            }
                            // 启完弹窗让用户点开
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                        "music" => {
                            // M3.29.4: 托盘「启动音乐播放器」→ 代启 music web
                            let state = app.state::<Arc<AppState>>();
                            match music::start_music(&state) {
                                Ok(port) => log::info!("[music] started port={}", port),
                                Err(e) => log::error!("[music] start err: {}", e),
                            }
                            // 同时打开歌词窗
                            let app_handle = app.clone();
                            std::thread::spawn(move || {
                                std::thread::sleep(Duration::from_millis(800));
                                if let Err(e) = music::open_lyrics_window(&app_handle) {
                                    log::warn!("[music] open lyrics err: {}", e);
                                }
                            });
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                        "lyrics" => {
                            // 托盘「桌面歌词(开/关)」→ toggle lyrics window
                            if let Some(win) = app.get_webview_window("lyrics-window") {
                                let visible = win.is_visible().unwrap_or(false);
                                if visible {
                                    let _ = win.hide();
                                } else {
                                    let _ = music::open_lyrics_window(app);
                                }
                            } else {
                                let _ = music::open_lyrics_window(app);
                            }
                        }
                        "open_calendar" => {
                            // P2.5+12: 托盘「📅 打开日历」— 代启 calendar 子进程 + 打开浏览器
                            let app_clone = app.clone();
                            std::thread::spawn(move || {
                                // 1) spawn calendar 子进程(若未起),等就绪后拿真端口
                                let port = match tauri::async_runtime::block_on(async {
                                    calendar::start(&app_clone).await
                                }) {
                                    Ok(p) => p,
                                    Err(e) => {
                                        log::error!("[calendar] start err: {}", e);
                                        return;
                                    }
                                };
                                // 2) 等健康检查通过(calendar::start 内已轮询,这里再保险兜底)
                                if !calendar::is_running(&app_clone) {
                                    log::warn!("[calendar] not running yet, skip open");
                                    return;
                                }
                                // 3) 在主窗口里打开 calendar 页(走应用内 WebView 而非外部浏览器)
                                let url = format!(
                                    "http://127.0.0.1:{}/prisiragent/calendar",
                                    port
                                );
                                log::info!("[calendar] opening in-app {}", url);
                                if let Some(win) = app_clone.get_webview_window("main") {
                                    let _ = win.show();
                                    let _ = win.set_focus();
                                    let _ = win.eval(&format!("window.location.href = '{}';", url));
                                }
                            });
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, .. } = event {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            if win.is_visible().unwrap_or(false) && win.is_focused().unwrap_or(false) {
                                let _ = win.hide();
                            } else {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            // ---------- ③ 全局热键 ----------
            let shortcut: Shortcut = HOTKEY.parse().unwrap();
            app.global_shortcut().on_shortcut(shortcut, move |app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    if let Some(win) = app.get_webview_window("main") {
                        if win.is_visible().unwrap_or(false) && win.is_focused().unwrap_or(false) {
                            let _ = win.hide();
                        } else {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                }
            })?;

            // ---------- ⑤ 输入法 AI 按钮唤起 ----------
            start_ai_toggle_listener(app_handle.clone());

            // ---------- ⑥ 品牌通知 ----------
            start_brand_notify(app_handle.clone());

            // ---------- 窗口事件: 关闭 → 最小化到托盘 ----------
            if let Some(win) = app.get_webview_window("main") {
                let state_for_close = Arc::clone(&state);
                let win_clone = win.clone();
                win.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        if !*state_for_close.quitting.lock().unwrap() {
                            api.prevent_close();
                            let _ = win_clone.hide();
                        }
                    }
                });
            }

            // M3.34(2026-09-19): 等后端就绪后加载 URL + 显式 set_icon + show
            // 黑窗修复:不显示空窗口直到 sentinel 拿到真端口;只有 web_ready 才 show
            // 图标修复:在 show() 前调 set_icon(),避免 visible:false 阶段创建窗口丢图标
            let state_for_load = Arc::clone(&state);
            let app_for_load = app.handle().clone();
            std::thread::spawn(move || {
                // 1) 在 show() 之前先把图标绑上(无论 web_ready 与否,先绑图标更稳)
                if let Some(win) = app_for_load.get_webview_window("main") {
                    if let Ok(icon_bytes) =
                        std::fs::read("../icons/icon.ico").or_else(|_| std::fs::read("icons/icon.ico"))
                    {
                        if let Ok(img) = tauri::image::Image::from_bytes(&icon_bytes) {
                            let _ = win.set_icon(img);
                            log::info!("[startup] main window icon bound ({} bytes)", icon_bytes.len());
                        }
                    }
                }

                // 2) 等 sentinel ready(60s 上限)
                let mut shown = false;
                for _ in 0..150 {
                    if *state_for_load.web_ready.lock().unwrap() {
                        let port = current_web_port(&state_for_load);
                        let url = format!("http://127.0.0.1:{}/", port);
                        if let Some(win) = app_for_load.get_webview_window("main") {
                            let _ = win.eval(&format!("window.location.href = '{}';", url));
                            let _ = win.show();
                            let _ = win.set_focus();
                            log::info!("[startup] main window shown url={}", url);
                            shown = true;
                        }
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                // 60s 还没 ready → 仍 show 窗口(避免完全不弹),
                // 但前端已用 api/port_status 端点,连接失败时会显示「连接失败」而不是黑窗
                if !shown {
                    log::error!("[startWeb] 60s 超时仍未 ready,兜底 show 空窗");
                    if let Some(win) = app_for_load.get_webview_window("main") {
                        // 兜底用 configured 端口(可能是 fallback 后的真端口)
                        let cfg_port = current_web_port(&state_for_load);
                        let url = format!("http://127.0.0.1:{}/", cfg_port);
                        let _ = win.eval(&format!("window.location.href = '{}';", url));
                        let _ = win.show();
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|_window, _event| {
            // handled in setup
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::ExitRequested { .. } => {
                    let state = app_handle.state::<Arc<AppState>>();
                    *state.quitting.lock().unwrap() = true;
                    kill_backend(&state);
                    kill_companion(&state);   // M3.27.3
                    music::kill_music(&state); // M3.29
                    // P2.5+12: 优雅 kill calendar 子进程(若有)
                    let app_for_cal = app_handle.clone();
                    tauri::async_runtime::block_on(async move {
                        calendar::stop(&app_for_cal).await;
                    });
                }
                _ => {}
            }
        });
}

// 辅助: open URL in system browser
mod open {
    pub fn that(url: &str) -> Result<(), Box<dyn std::error::Error>> {
        #[cfg(target_os = "windows")]
        {
            std::process::Command::new("cmd")
                .args(["/c", "start", "", url])
                .spawn()?;
        }
        #[cfg(target_os = "macos")]
        {
            std::process::Command::new("open").arg(url).spawn()?;
        }
        #[cfg(target_os = "linux")]
        {
            std::process::Command::new("xdg-open").arg(url).spawn()?;
        }
        Ok(())
    }
}
