// SecureDM 本地壳 — 桌面端(Tauri + WebView2)
// 设计见 Documents/prisiragent-os-integration/securedm-shell-design.md
// 壳 = 能力增强的浏览器:加载现有 securedm_web.py 前端的 HTTP 端点,零改动复用。

use std::path::PathBuf;
use tauri::{WebviewUrl, WebviewWindowBuilder};

/// 原生「打开文件」对话框 → 返回所选文件的绝对路径(取消返回空串)。
/// 为什么需要:WebView2 里 <input type=file> 出于安全只给 File 对象,
/// 拿不到绝对路径;而 SimpleX 发文件走 daemon 进程按路径读盘,必须要绝对路径。
/// 故由壳用 rfd(Rusty File Dialogs)选文件,把路径回填到页面输入框。
#[tauri::command]
fn pick_file() -> String {
    rfd::FileDialog::new()
        .set_title("选择要发送的文件")
        .pick_file()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default() // 用户取消 → 空串
}

/// 原生「选择文件夹」对话框 → 返回所选目录绝对路径(取消返回空串)。
/// 用途:接收方自定义下载保存目录(默认下载在 simplex 数据目录,用户嫌找不到)。
#[tauri::command]
fn pick_folder() -> String {
    rfd::FileDialog::new()
        .set_title("选择下载保存目录")
        .pick_folder()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// 读 L4 token(与后端 _auth 比对的同一文件)。
/// 默认 ~/.local/share/aureon/l4_token;可用 SECUREDM_TOKEN_FILE 覆盖,或
/// SECUREDM_TOKEN 直接给 token 串(便于测试/多实例)。
fn read_token() -> String {
    if let Ok(t) = std::env::var("SECUREDM_TOKEN") {
        return t.trim().to_string();
    }
    let path: PathBuf = std::env::var("SECUREDM_TOKEN_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("USERPROFILE")
                .or_else(|_| std::env::var("HOME"))
                .unwrap_or_default();
            PathBuf::from(home)
                .join(".local")
                .join("share")
                .join("aureon")
                .join("l4_token")
        });
    std::fs::read_to_string(&path)
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|e| {
            eprintln!("[securedm-shell] 读 token 失败 {:?}: {}", path, e);
            String::new()
        })
}

/// 后端 HTTP base。默认本机 18801(prisiragent 实例);可用 SECUREDM_BASE 覆盖
/// (如指向 bob 实例 18802,或远程中继)。
fn base_url() -> String {
    std::env::var("SECUREDM_BASE").unwrap_or_else(|_| "http://127.0.0.1:18801".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 能力探针模式:加载 public/probe.html(App 协议),测原生能力。
    // 正常模式:External 直连本地后端,前端拿到 ?token= 自存 localStorage,
    // 之后同源 /dm/api/* 自动带 X-L4-Token(securedm_web.py:340)。
    let probe = std::env::var("SECUREDM_PROBE").is_ok();

    // 多实例:SECUREDM_INSTANCE 给实例名(如 prisiragent/bob)。
    // ① 独立 user-data-folder —— 隔离 localStorage/cookie,更关键:隔离 WebView2
    //    媒体设备锁。单进程多窗口会互抢摄像头,独立进程+独立数据目录才能多开视频通话。
    // ② 窗口标题带实例名,同屏多开时区分身份。
    let instance = std::env::var("SECUREDM_INSTANCE").unwrap_or_else(|_| "default".to_string());

    let (url, title) = if probe {
        println!("[securedm-shell][{}] 能力探针模式 → probe.html", instance);
        (WebviewUrl::App("probe.html".into()), format!("SecureDM 探针·{}", instance))
    } else {
        let token = read_token();
        let base = base_url();
        let s = if token.is_empty() {
            eprintln!("[securedm-shell][{}] 警告:无 token,页面将 401。", instance);
            base.clone()
        } else {
            format!("{}/?token={}", base, token)
        };
        println!("[securedm-shell][{}] 加载 {}", instance, base);
        (
            WebviewUrl::External(s.parse().expect("无效起始 URL")),
            format!("SecureDM·{}", instance),
        )
    };

    // 独立数据目录:%LOCALAPPDATA%/securedm-shell/<instance>/
    let data_dir = std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("securedm-shell")
        .join(&instance);
    let _ = std::fs::create_dir_all(&data_dir);
    println!("[securedm-shell][{}] 数据目录 {:?}", instance, data_dir);

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![pick_file, pick_folder])
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", url.clone())
                .title(&title)
                .data_directory(data_dir.clone())
                .inner_size(980.0, 720.0)
                .min_inner_size(680.0, 480.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("运行 SecureDM 壳出错");
}
