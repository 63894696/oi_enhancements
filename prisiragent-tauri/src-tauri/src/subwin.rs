//! P2.5+19(2026-09-22)4 子窗独立 WebviewWindow 模型。
//!
//! 跟 Electron main.js childWindows Map 复用语义对齐:
//!   - close → hide(保留 WebviewWindow 句柄,JS heap 保留,下次秒开)
//!   - show 前 unminimize 避免最小化态卡死
//!   - 端口动态解析,首次 show 时 eval 重设 URL(覆盖占位 URL)
//!
//! 子窗清单(跟 lib.rs tray on_menu_event 同款映射):
//!   companion-window / music-window / calendar-window / workflow-window
//!
//! 配套的 4 子窗预声明在 tauri.conf.json app.windows[] 里(visible:false,
//! url 用默认端口占位;首次 show 时被 wins.eval 重设到真实端口)。

use std::sync::Arc;

use tauri::{AppHandle, Manager, WindowEvent};

use crate::calendar;
use crate::port_config;
use crate::AppState;

/// 4 子窗 label 列表(白名单,防止任意 label 注入)
pub const SUBWINDOW_LABELS: [&str; 4] = [
    "companion-window",
    "music-window",
    "calendar-window",
    "workflow-window",
];

/// 4 子窗 label → 端口构造逻辑(返回完整 URL)
///
/// 端口解析优先级跟 Electron main.js openXxxWindow 同款:
///   - companion:port_config::read_companion_port()(默认 18850)
///   - music:port_config::read_music_port()(默认 0=未启动)
///   - calendar:calendar::current_url(app)(动态,默认 18803)
///   - workflow:主 web 端口 + URL fragment(没独立后端)
fn url_for_label(app: &AppHandle, label: &str) -> Option<String> {
    match label {
        "companion-window" => {
            // 1) 代启 companion 子进程(若未起);装包后体验关键
            if let Some(state) = app.try_state::<Arc<AppState>>() {
                let st: Arc<AppState> = state.inner().clone();
                if let Err(e) = crate::start_companion(&st) {
                    log::warn!("[companion-window] start_companion err: {}", e);
                }
            }
            let port = port_config::read_companion_port();
            Some(format!("http://127.0.0.1:{}/", port))
        }
        "music-window" => {
            // music 端口 0 = 动态分配;启动 music web 后端口由它写 HKCU
            let port = port_config::read_music_port();
            if port == 0 {
                return None; // music web 没起,前端 fallback
            }
            Some(format!("http://127.0.0.1:{}/", port))
        }
        "calendar-window" => Some(calendar::current_url(app)),
        "workflow-window" => {
            // 主 web 端口,从 AppState 拿(走 current_web_port:sentinel 真端口 > 启动候选)
            if let Some(state) = app.try_state::<Arc<AppState>>() {
                let st: Arc<AppState> = state.inner().clone();
                let p = crate::current_web_port(&st);
                Some(format!("http://127.0.0.1:{}/#wfmodal", p))
            } else {
                let p = port_config::read_web_port();
                Some(format!("http://127.0.0.1:{}/#wfmodal", p))
            }
        }
        _ => None,
    }
}

/// 复用 / 创建 / 显示一个子窗。
///
/// 流程:
///   1. url_for_label 解析真端口(可能跟 tauri.conf.json 占位不同)
///   2. get_webview_window(label) — 预声明的 WebviewWindow 句柄
///   3. eval 重设 URL(端口可能因 sentinel / dynamic allocation 变了)
///   4. unminimize + show + set_focus 三件套
///
/// 复用语义:webview 句柄常驻(close handler 走 hide),JS heap 保留 → 秒级响应。
pub fn open_window(app: &AppHandle, label: &'static str) -> Result<(), String> {
    if !SUBWINDOW_LABELS.contains(&label) {
        return Err(format!("unknown subwindow label: {}", label));
    }

    let url = url_for_label(app, label).ok_or_else(|| {
        format!("{}: port not ready (music web may not be running)", label)
    })?;

    let win = app.get_webview_window(label).ok_or_else(|| {
        format!("{} not configured — check tauri.conf.json app.windows[]", label)
    })?;

    // 1) 重设 URL(端口可能因 sentinel / dynamic allocation 变了)
    let _ = win.eval(&format!("window.location.href = '{}';", url));

    // 2) show 三件套:unminimize + show + focus
    let _ = win.unminimize();
    let _ = win.show();
    let _ = win.set_focus();
    log::info!("[openWindow] {} url={}", label, url);
    Ok(())
}

/// 给单个子窗挂 close → hide 复用 handler(setup 阶段调)。
///
/// 复用 lib.rs 主窗 close-to-tray 模板(line 999-1007):
///   - WindowEvent::CloseRequested + api.prevent_close() + window.hide()
///   - quitting 守卫:真退出时不拦,让进程正常结束
pub fn bind_close_to_tray(app: &AppHandle, label: &'static str) {
    let Some(win) = app.get_webview_window(label) else {
        log::warn!("[closeToTray] {} not configured, skip", label);
        return;
    };
    let win_clone = win.clone();
    let app_handle = app.clone();
    win.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            let quitting = if let Some(state) = app_handle.try_state::<Arc<AppState>>() {
                *state.quitting.lock().unwrap()
            } else {
                false
            };
            if !quitting {
                api.prevent_close();
                let _ = win_clone.hide();
                log::info!("[closeToTray] {} hidden", label);
            }
        }
    });
}

/// 关闭所有子窗(托盘「关闭所有子窗口」聚合 / 前端关闭所有子窗口按钮)。
///
/// 仅 hide,不销毁;下次同 label 复用秒开。
pub fn hide_all(app: &AppHandle) {
    for label in SUBWINDOW_LABELS.iter() {
        if let Some(w) = app.get_webview_window(label) {
            let _ = w.hide();
        }
    }
    log::info!("[hideAllWindows] 4 child windows hidden");
}

/// 取 4 子窗状态(给前端查询用,跟 Electron main.js 模式对齐)
#[tauri::command]
pub fn subwindows_status_cmd(app: tauri::AppHandle) -> serde_json::Value {
    let mut items = serde_json::Map::new();
    for label in SUBWINDOW_LABELS.iter() {
        let (visible, url) = match app.get_webview_window(label) {
            Some(w) => {
                let v = w.is_visible().unwrap_or(false);
                let url = url_for_label(&app, label).unwrap_or_default();
                (v, url)
            }
            None => (false, String::new()),
        };
        items.insert(
            label.to_string(),
            serde_json::json!({"visible": visible, "url": url}),
        );
    }
    serde_json::json!({"ok": true, "windows": items})
}

/// 4 子窗 Rust commands(给前端顶栏按钮调用,装包后 Tauri 注入 __TAURI_INTERNALS__)

#[tauri::command]
pub fn open_companion_window_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match open_window(&app, "companion-window") {
        Ok(()) => serde_json::json!({"ok": true, "label": "companion-window"}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

#[tauri::command]
pub fn open_music_window_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match open_window(&app, "music-window") {
        Ok(()) => serde_json::json!({"ok": true, "label": "music-window"}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

#[tauri::command]
pub fn open_calendar_window_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match open_window(&app, "calendar-window") {
        Ok(()) => serde_json::json!({"ok": true, "label": "calendar-window"}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

#[tauri::command]
pub fn open_workflow_window_cmd(app: tauri::AppHandle) -> serde_json::Value {
    match open_window(&app, "workflow-window") {
        Ok(()) => serde_json::json!({"ok": true, "label": "workflow-window"}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

#[tauri::command]
pub fn close_all_child_windows_cmd(app: tauri::AppHandle) -> serde_json::Value {
    hide_all(&app);
    serde_json::json!({"ok": true})
}