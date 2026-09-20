//! M3.32 calendar 子模块:PrisirAI 主后端 calendar 子进程看护
//!
//! 设计:
//! - calendar 后端 = python prisiragent_web.py --port <auto> --host 127.0.0.1
//!   (与主后端同一个脚本,通过不同端口共存于不同进程;主后端负责对话,
//!   calendar 进程专门服务于日历 / 行程 / 店铺等只读日历视图)
//! - 端口由 calendar 子进程自己动态分配(本任务先打通壳接入,沿用默认端口
//!   CALENDAR_PORT_DEFAULT;后续若要真动态端口需要改 prisiragent_web.py 让其
//!   向 stdout 打印 "Listening on port N" 给本模块 parse,task #25 E2E 时再并入)
//! - 子进程 Child 句柄 + 端口号 + Ready 标志都注入 AppHandle
//!   (CalendarChild / CalendarPort / CalendarReady),lib.rs 通过
//!   app.state::<CalendarChild>() 取用、graceful kill
//! - 本模块提供 3 个公开 API(供 lib.rs / task #19 接入):
//!     start(app)        - spawn 子进程 + 等健康 + 返回端口
//!     stop(app)         - 优雅 kill 子进程
//!     is_running(app)   - 检查子进程是否还活着
//!
//! 设计参考:prisIragent-tauri/src-tauri/src/music.rs(同模式:std::process::Command +
//!           stdout/stderr 接 logs 文件 + std::thread::spawn 轮询就绪)。
//!
//! 依赖策略:本文件只用 tauri / serde / reqwest(blocking)/ dirs / log / std,
//!           不引入新 crate(避免改 Cargo.toml)。

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::path::PathBuf;
use std::time::Duration;
use std::fs;
use std::io::{Read, Write};

use serde::Serialize;
use tauri::{AppHandle, Manager};

/// calendar 默认 fallback 端口(实际由子进程 OS 动态分配;此值仅 spawn 后被覆盖)。
const CALENDAR_PORT_DEFAULT: u16 = 18803;

/// 健康检查超时:HTTP 探测单次 2s,整体轮询 10s(50 * 200ms)。
const HEALTH_TIMEOUT: Duration = Duration::from_secs(2);
const HEALTH_TOTAL_BUDGET: Duration = Duration::from_secs(10);

/// AppHandle state:子进程 Child 句柄(可空:未启动 / 已被 kill)。
#[derive(Default)]
pub struct CalendarChild(pub Mutex<Option<Child>>);

/// AppHandle state:实际绑定的端口号(子进程启动后填)。
#[derive(Default, Clone, Copy, Serialize)]
pub struct CalendarPort(pub u16);

/// AppHandle state:健康检查是否通过(只在 start() 末尾写一次)。
#[derive(Default, Clone, Copy, Serialize)]
pub struct CalendarReady(pub bool);

/// 探测给定端口是否在监听(健康检查底层原语)
fn tcp_alive(port: u16) -> bool {
    use std::net::TcpStream;
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", port).parse().unwrap(),
        Duration::from_millis(1500),
    )
    .is_ok()
}

/// HTTP 健康检查:GET /prisiragent/api/info
///
/// 200 + JSON body 即视为 OK;任何网络/超时/非 200 → 不健康。
///
/// 用 reqwest::blocking(已在 Cargo.toml 启用),放到 spawn_blocking 里跑避免阻塞
/// tokio runtime。
fn http_health_blocking(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{}/prisiragent/api/info", port);
    match reqwest::blocking::Client::builder()
        .timeout(HEALTH_TIMEOUT)
        .build()
    {
        Ok(client) => client.get(&url).send().map(|r| r.status().is_success()).unwrap_or(false),
        Err(_) => false,
    }
}

/// 找 calendar 脚本路径(prisiragent_web.py),与 music.rs 同样的候选列表逻辑:
///   开发态(从 src-tauri 跑)= ../prisiragent_web.py
///   装包后(壳 exe 同级)= ../prisiragent_web.py
fn resolve_calendar_script() -> Result<PathBuf, String> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    let candidates = vec![
        exe_dir.join("..").join("prisiragent_web.py"),
        exe_dir.join("prisiragent_web.py"),
        PathBuf::from("prisiragent_web.py"),
    ];
    candidates
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| {
            "找不到 prisiragent_web.py(尝试 ../prisiragent_web.py、同级、cwd)".to_string()
        })
}

/// 把子进程 stdout / stderr 落到 logs 目录(music.rs 同模式)
fn spawn_log_dir() -> PathBuf {
    let d = dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("prisirai-shell")
        .join("logs");
    let _ = fs::create_dir_all(&d);
    d
}

fn pipe_to_log<R: Read + Send + 'static>(mut reader: R, log_path: PathBuf) {
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
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

/// 启动 calendar 子进程,等健康检查通过,返回实际端口号。
///
/// 行为:
///   1. 若 state 已有 Child 且未退出 → 直接返回已存端口(幂等)
///   2. spawn python prisiragent_web.py --port <default> --host 127.0.0.1
///      (本任务先用默认端口;ture 动态端口留给 task #25)
///   3. 子进程 stdout/stderr → logs/calendar-std{out,err}.log
///   4. 轮询 http_health(port)直到 OK 或超时(HEALTH_TOTAL_BUDGET)
///   5. 把 Child / Port / Ready 写入 AppHandle state
pub async fn start(app: &AppHandle) -> Result<u16, Box<dyn std::error::Error>> {
    // 幂等:已启动 + 子进程未退出 → 直接返回
    if let Some(state) = app.try_state::<CalendarChild>() {
        let mut guard = state.0.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            // 还活着?
            let still_alive = matches!(child.try_wait(), Ok(None));
            if still_alive {
                let port = app
                    .try_state::<CalendarPort>()
                    .map(|p| p.0)
                    .unwrap_or(CALENDAR_PORT_DEFAULT);
                if port != 0 {
                    return Ok(port);
                }
                return Ok(CALENDAR_PORT_DEFAULT);
            }
        }
    }

    let script = resolve_calendar_script()?;
    log::info!("[startCalendar] spawning script={}", script.display());

    // 阻塞 spawn 包到 spawn_blocking,免得卡 tokio runtime
    let script_str = script.to_string_lossy().to_string();
    let spawn_result = tauri::async_runtime::spawn_blocking(move || {
        Command::new("python")
            .arg("-B")
            .arg(&script_str)
            .arg("--port")
            .arg(CALENDAR_PORT_DEFAULT.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
    })
    .await
    .map_err(|e| format!("join err spawning calendar: {}", e))?;

    let mut child = match spawn_result {
        Ok(c) => c,
        Err(e) => return Err(format!("spawn calendar err: {}", e).into()),
    };

    log::info!("[calendar] spawned pid={}", child.id());

    // stdout / stderr → logs/calendar-std{out,err}.log
    let log_dir = spawn_log_dir();
    if let Some(stdout) = child.stdout.take() {
        pipe_to_log(stdout, log_dir.join("calendar-stdout.log"));
    }
    if let Some(stderr) = child.stderr.take() {
        pipe_to_log(stderr, log_dir.join("calendar-stderr.log"));
    }

    // 写入 AppHandle state(music.rs 是往 Arc<AppState> 里写,这里走 Tauri 2 的
    // app.manage() 模式;lib.rs 接入时不用关心具体内部字段,只调 start/stop/is_running)
    app.manage(CalendarChild(Mutex::new(Some(child))));
    app.manage(CalendarPort(CALENDAR_PORT_DEFAULT));
    app.manage(CalendarReady(false));

    // 等健康检查通过:每次 health check 用 spawn_blocking 包 reqwest::blocking;
    // 中间用 std::thread::sleep(在 spawn_blocking 里 sleep)避免占 tokio 线程。
    let port = CALENDAR_PORT_DEFAULT;
    let health_check = tauri::async_runtime::spawn_blocking(move || {
        let max_iters = HEALTH_TOTAL_BUDGET.as_millis() / 200;
        for _ in 0..max_iters {
            if http_health_blocking(port) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        false
    });

    let ready = health_check
        .await
        .map_err(|e| format!("join err health check: {}", e))?;

    if ready {
        log::info!("[startCalendar] calendar ready port={}", port);
    } else {
        log::error!(
            "[startCalendar] calendar not ready within {:?} (port={})",
            HEALTH_TOTAL_BUDGET,
            port
        );
        // 仍然返回端口,让上层决定怎么处理(避免一次性吃掉子进程)
    }

    Ok(port)
}

/// 优雅杀掉 calendar 子进程。
///
/// 行为:从 state 取 Child,kill + wait,清空 Option 槽。
pub async fn stop(app: &AppHandle) {
    let Some(state) = app.try_state::<CalendarChild>() else {
        return;
    };
    let mut guard = state.0.lock().unwrap();
    let Some(mut child) = guard.take() else {
        return;
    };
    log::info!("[stopCalendar] killing pid={}", child.id());
    let _ = child.kill();
    // wait 不阻塞 tokio:放 spawn_blocking
    let _ = tauri::async_runtime::spawn_blocking(move || child.wait()).await;
    log::info!("[stopCalendar] killed");
}

/// 子进程是否还活着。
///
/// 优先级:进程句柄 alive → 端口 TCP 可达。两者皆否 → false。
pub fn is_running(app: &AppHandle) -> bool {
    // 1. Child 句柄是否还在(用 try_wait 不阻塞)
    if let Some(state) = app.try_state::<CalendarChild>() {
        let mut guard = state.0.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            match child.try_wait() {
                Ok(Some(_status)) => {
                    // 已退出 → 清空
                    *guard = None;
                    return false;
                }
                Ok(None) => {
                    // 还活着
                    let port = app
                        .try_state::<CalendarPort>()
                        .map(|p| p.0)
                        .unwrap_or(CALENDAR_PORT_DEFAULT);
                    return tcp_alive(port);
                }
                Err(_) => return false,
            }
        }
    }
    // 2. 没 Child 但端口在监听 → 视为外部进程在跑(共享端口场景)
    if let Some(port) = app.try_state::<CalendarPort>() {
        if tcp_alive(port.0) {
            return true;
        }
    }
    false
}