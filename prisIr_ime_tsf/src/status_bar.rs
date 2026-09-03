//! 可拖动多功能工具条 — 对标搜狗输入法悬浮栏形态。
//!
//! **布局**(自左到右,单像素分隔):
//!   [图标(点击=菜单)] [中/英] [。/.(标点)] [写(手写)] [符(符号大全)] [词(词库)]
//!
//! **拖动(2026-09-01 搜狗对标)**: 整栏任意位置都能拖动。实现 = WM_NCHITTEST 对
//! 非按钮区返回 HTCAPTION(系统接管拖动,最稳);按钮区返回 HTCLIENT 交给我们自绘
//! 命中。不再有专属把手区 — 把手改成图标按钮(点击弹输入法菜单)。
//!
//! **按钮行为**:
//!   - 图标: 点击弹输入法菜单(中/英、中英标点、手写/符号/词库入口)。
//!   - 中/英: 翻 mode_ref + pending_mode_change,并联动标点(搜狗:中→中文标点,英→英文标点)。
//!   - 。/.: 翻 punct_ref(中/英标点),按钮显示 。(中)/.(英)。
//!   - 写/符/词: 入口占位(log),功能逐个实现。
//!
//! **mode→punct 联动**: set_mode() 时把 punct_ref 对齐 mode(中=true/英=false),
//! 对齐搜狗「切英文自动英文标点、切中文自动中文标点」。用户仍可按 。/. 按钮单独覆盖。

use std::cell::RefCell;
use std::sync::{Arc, Mutex, OnceLock};
use windows::core::*;
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::LibraryLoader::{
    GetModuleHandleExW, GetModuleHandleW, GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
    GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
};
use windows::Win32::System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject};
use windows::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture};
use windows::Win32::UI::WindowsAndMessaging::*;

// ── 布局常量 ────────────────────────────────────────────────────────────────
const BAR_CLASS: &str = "PrisirStatusBar";
/// 栏高(2026-09-03 苹果风:更宽松)。
const BAR_H: i32 = 34;
/// 图标按钮宽(点击弹菜单)。
const ICON_W: i32 = 34;
/// 每个功能按钮宽。
const BTN_W: i32 = 38;
/// 功能按钮数量(中/英, 标点, 符, emoji, 写, 词)。
const N_BTNS: i32 = 6;
const BAR_W: i32 = ICON_W + BTN_W * N_BTNS;

// 苹果风配色(BGR 0x00BBGGRR),与候选窗一致。
const BAR_COL_BG: u32 = 0x00FAF9F7;      // 暖白底
const BAR_COL_SEP: u32 = 0x00ECE9E4;     // 分隔线浅灰
const BAR_COL_ACCENT: u32 = 0x00A9750A;  // 苹果蓝 #0A84FF(主按钮:中/标点)
const BAR_COL_TEXT: u32 = 0x005A544D;    // 次要按钮暖灰

/// 功能按钮索引(在图标之后)。
const BTN_MODE: i32 = 0;   // 中/英
const BTN_PUNCT: i32 = 1;  // 。/.(中/英标点)
const BTN_SYM: i32 = 2;    // 符(符号大全)
const BTN_EMOJI: i32 = 3;  // 😀(emoji 面板)
const BTN_HAND: i32 = 4;   // 写(手写,兜底刚需)
const BTN_VOCAB: i32 = 5;  // 词(词库)
// 栏位可勾选(搜狗式菜单打钩)排后续;当前默认全显。

/// 本 DLL 内嵌主图标资源 ID(winres set_icon 第一个 = IDI_ICON 1)。
const IDI_PRISIR_ICON: u16 = 1;

/// 菜单命令 ID。
const IDM_MODE: u32 = 1001;
const IDM_PUNCT: u32 = 1002;
const IDM_HAND: u32 = 1003;
const IDM_SYM: u32 = 1004;
const IDM_VOCAB: u32 = 1005;
const IDM_EMOJI: u32 = 1006;

// ── 共享状态 ────────────────────────────────────────────────────────────────
pub(crate) struct StatusBarState {
    pub hwnd: Option<HWND>,
    pub is_chinese: bool,
    pub is_chinese_punct: bool,
    pub visible: bool,
    pub pos: Option<(i32, i32)>,
}

impl StatusBarState {
    pub fn new() -> Self {
        Self {
            hwnd: None,
            is_chinese: true,
            is_chinese_punct: true,
            visible: false,
            pos: None,
        }
    }
}

static BAR_STATE: OnceLock<Arc<Mutex<RefCell<StatusBarState>>>> = OnceLock::new();
unsafe impl Send for StatusBarState {}
unsafe impl Sync for StatusBarState {}

pub(crate) fn global_bar_state() -> Arc<Mutex<RefCell<StatusBarState>>> {
    BAR_STATE
        .get_or_init(|| Arc::new(Mutex::new(RefCell::new(StatusBarState::new()))))
        .clone()
}

// ── 模式/标点切换通道(TsfInputProcessor 注入) ─────────────────────────────
struct Toggles {
    mode_ref: Arc<Mutex<bool>>,
    pending_mode_change: Arc<Mutex<bool>>,
    punct_ref: Arc<Mutex<bool>>,
}
static TOGGLES: OnceLock<Toggles> = OnceLock::new();
unsafe impl Send for Toggles {}
unsafe impl Sync for Toggles {}

// ── 全局单一条所有权(命名互斥锁) ───────────────────────────────────────────
// 用户实测:多个应用同时激活 Prisir 时各建一条(explorer 一条、notepad 一条…),
// 且 explorer 那条常驻关不掉。对齐搜狗「全局只有一条」:
//   激活时非阻塞尝试拿 `Global\PrisirStatusBarOwner`,拿到才显示,拿不到就跳过
//   (别的进程已在显示)。进程持有互斥锁直到 deactivate / 失焦,或进程退出(系统自动释放)。
//   拖动位置 / 标点状态都在拥有者进程内,天然自洽。
// 句柄按 usize 存(HANDLE 非 Send,不能进 static Mutex;usize 可)。
static BAR_OWNER_MUTEX: Mutex<Option<usize>> = Mutex::new(None);
const BAR_OWNER_NAME: &str = r"Global\PrisirStatusBarOwner";

/// 尝试成为状态条唯一拥有者。已是拥有者→true;拿到→true;被别人占→false。
fn try_acquire_bar_ownership() -> bool {
    {
        let held = BAR_OWNER_MUTEX.lock().unwrap();
        if held.is_some() {
            return true; // 本进程已是拥有者
        }
    }
    let name_w: Vec<u16> = BAR_OWNER_NAME.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        // CreateMutexW:若已存在,返回已存在互斥锁的 handle + GetLastError=ERROR_ALREADY_EXISTS。
        // 我们用 WaitForSingleObject(0) 非阻塞探测能否立即占有。
        let h = match CreateMutexW(None, false, PCWSTR(name_w.as_ptr())) {
            Ok(h) if !h.is_invalid() => h,
            _ => return false,
        };
        match WaitForSingleObject(h, 0) {
            r if r == WAIT_OBJECT_0 || r == WAIT_ABANDONED => {
                // 拿到所有权(或前拥有者崩溃遗弃)。存 handle,进程持有。
                *BAR_OWNER_MUTEX.lock().unwrap() = Some(h.0 as usize);
                true
            }
            _ => {
                // 被别的进程持有 → 关掉我们这边的 handle 引用,不显示。
                let _ = CloseHandle(h);
                false
            }
        }
    }
}

/// 释放状态条所有权(deactivate / 失焦时调),让其它进程接管。
fn release_bar_ownership() {
    let h = BAR_OWNER_MUTEX.lock().unwrap().take();
    if let Some(raw) = h {
        let h = HANDLE(raw as *mut _);
        unsafe {
            let _ = ReleaseMutex(h);
            let _ = CloseHandle(h);
        }
    }
}

/// 注入中/英 + 中/英标点 切换通道(activate 时调一次,幂等)。
pub(crate) fn bind_toggles(
    mode_ref: Arc<Mutex<bool>>,
    pending_mode_change: Arc<Mutex<bool>>,
    punct_ref: Arc<Mutex<bool>>,
) {
    let _ = TOGGLES.set(Toggles {
        mode_ref,
        pending_mode_change,
        punct_ref,
    });
}

// ── 窗口类注册 / 创建 ───────────────────────────────────────────────────────
fn register_class() -> Result<()> {
    let hinst = unsafe { GetModuleHandleW(None) }?;
    let class_name: Vec<u16> = BAR_CLASS.encode_utf16().chain(std::iter::once(0)).collect();
    let mut existing = WNDCLASSEXW::default();
    if unsafe { GetClassInfoExW(hinst, PCWSTR(class_name.as_ptr()), &mut existing) }.is_ok() {
        return Ok(());
    }
    let wc = WNDCLASSEXW {
        cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
        style: CS_HREDRAW | CS_VREDRAW,
        lpfnWndProc: Some(status_bar_wnd_proc),
        hInstance: hinst.into(),
        hCursor: unsafe { LoadCursorW(None, IDC_ARROW) }?,
        hbrBackground: HBRUSH((COLOR_WINDOW.0 + 1) as *mut _),
        lpszClassName: PCWSTR(class_name.as_ptr()),
        ..Default::default()
    };
    let atom = unsafe { RegisterClassExW(&wc) };
    if atom == 0 {
        return Err(Error::from_win32());
    }
    Ok(())
}

fn ensure_window(state: &Arc<Mutex<RefCell<StatusBarState>>>) -> Result<HWND> {
    let existing = state.lock().unwrap().borrow().hwnd;
    if let Some(hwnd) = existing {
        if unsafe { IsWindow(hwnd) }.as_bool() {
            return Ok(hwnd);
        }
        state.lock().unwrap().borrow_mut().hwnd = None;
    }
    register_class()?;
    let hinst = unsafe { GetModuleHandleW(None) }?;
    let class_name: Vec<u16> = BAR_CLASS.encode_utf16().chain(std::iter::once(0)).collect();
    let title: Vec<u16> = "PrisirBar".encode_utf16().chain(std::iter::once(0)).collect();
    let hwnd = unsafe {
        CreateWindowExW(
            WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            PCWSTR(class_name.as_ptr()),
            PCWSTR(title.as_ptr()),
            WS_POPUP,
            0, 0, BAR_W, BAR_H,
            None, None, hinst, None,
        )
    }?;
    state.lock().unwrap().borrow_mut().hwnd = Some(hwnd);
    #[cfg(feature = "dllentry_log")]
    crate::com_class_factory::log_dll_entry(&format!("StatusBar: created hwnd={:p}", hwnd.0));
    Ok(hwnd)
}

fn default_pos() -> (i32, i32) {
    let sw = unsafe { GetSystemMetrics(SM_CXSCREEN) };
    (sw - BAR_W - 24, 40)
}

// ── 对外 API ────────────────────────────────────────────────────────────────
pub(crate) fn show(
    state: &Arc<Mutex<RefCell<StatusBarState>>>,
    is_chinese: bool,
    is_chinese_punct: bool,
) {
    {
        let g = state.lock().unwrap();
        let mut s = g.borrow_mut();
        s.is_chinese = is_chinese;
        s.is_chinese_punct = is_chinese_punct;
    }
    // 全局单一条:拿不到所有权就不显示(别的进程已在显示)。
    if !try_acquire_bar_ownership() {
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry("StatusBar: skip show (another process owns bar)");
        return;
    }
    if let Ok(hwnd) = ensure_window(state) {
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("StatusBar: show using hwnd={:p}", hwnd.0));
        let (x, y) = {
            let g = state.lock().unwrap();
            let s = g.borrow();
            s.pos.unwrap_or_else(default_pos)
        };
        unsafe {
            let _ = SetWindowPos(hwnd, HWND_TOPMOST, x, y, BAR_W, BAR_H, SWP_NOACTIVATE | SWP_SHOWWINDOW);
            // 苹果式圆角:裁剪窗口为圆角矩形区域(与候选窗一致)。
            let rgn = CreateRoundRectRgn(0, 0, BAR_W + 1, BAR_H + 1, 16, 16);
            let _ = SetWindowRgn(hwnd, rgn, true);
            let _ = InvalidateRect(hwnd, None, true);
        }
        state.lock().unwrap().borrow_mut().visible = true;
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("StatusBar: show chinese={} punct={}", is_chinese, is_chinese_punct));
    }
}

pub(crate) fn hide(state: &Arc<Mutex<RefCell<StatusBarState>>>) {
    // 2026-09-01 修孤儿栏:之前 hide 只 ShowWindow(SW_HIDE) 不销毁窗口,
    // 失焦后窗口仍存活;一旦 ownership 与 hwnd 生命周期错位(hide 释放锁后,
    // 本进程又 activate→show→拿不到锁→skip,但残留窗口被某处重新 SWP_SHOWWINDOW)
    // 就成孤儿,且 Deactivate 时 state 错位报 hwnd=None 清不掉 → 越叠越多。
    // 改成 hide 也销毁窗口(对齐 Deactivate 的 destroy)。跨线程用 WM_CLOSE
    // 送到窗口自己的线程销毁(直接 DestroyWindow 跨线程返 err=5)。
    let hwnd = state.lock().unwrap().borrow_mut().hwnd.take();
    if let Some(hwnd) = hwnd {
        unsafe {
            let _ = ShowWindow(hwnd, SW_HIDE);
            let _ = SendMessageW(hwnd, WM_CLOSE, WPARAM(0), LPARAM(0));
        }
    }
    state.lock().unwrap().borrow_mut().visible = false;
    // 失焦/切走 → 释放所有权,让下一个激活的进程接管显示。
    release_bar_ownership();
}

/// 模式/标点翻转后重绘(仅可见时)。
pub(crate) fn refresh(
    state: &Arc<Mutex<RefCell<StatusBarState>>>,
    is_chinese: bool,
    is_chinese_punct: bool,
) {
    let visible = {
        let g = state.lock().unwrap();
        let mut s = g.borrow_mut();
        s.is_chinese = is_chinese;
        s.is_chinese_punct = is_chinese_punct;
        s.visible
    };
    if visible {
        let hwnd = state.lock().unwrap().borrow().hwnd;
        if let Some(hwnd) = hwnd {
            unsafe { let _ = InvalidateRect(hwnd, None, true); }
        }
    }
}

pub(crate) fn destroy(state: &Arc<Mutex<RefCell<StatusBarState>>>) {
    let hwnd = state.lock().unwrap().borrow_mut().hwnd.take();
    if let Some(hwnd) = hwnd {
        // 跨线程 DestroyWindow 返 err=5;送 WM_CLOSE 到窗口自己的线程销毁(见 WndProc)。
        unsafe { let _ = SendMessageW(hwnd, WM_CLOSE, WPARAM(0), LPARAM(0)); }
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("StatusBar: destroy(WM_CLOSE) hwnd={:p} still_alive={}", hwnd.0, unsafe { IsWindow(hwnd).as_bool() }));
    } else {
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry("StatusBar: destroy but hwnd=None (nothing to destroy)");
    }
    state.lock().unwrap().borrow_mut().visible = false;
    release_bar_ownership();
}

// ── 命中测试: 客户端 x → 区域 ───────────────────────────────────────────────
enum Hit {
    Icon,
    Button(i32),
    None,
}
fn hit_test(client_x: i32) -> Hit {
    if client_x < 0 || client_x >= BAR_W {
        return Hit::None;
    }
    if client_x < ICON_W {
        Hit::Icon
    } else {
        let idx = (client_x - ICON_W) / BTN_W;
        Hit::Button(idx.clamp(0, N_BTNS - 1))
    }
}

// ── 模式/标点切换核心(按钮 + 菜单共用) ─────────────────────────────────────
fn toggle_mode() {
    if let Some(t) = TOGGLES.get() {
        let new_mode = { let mut m = t.mode_ref.lock().unwrap(); *m = !*m; *m };
        *t.pending_mode_change.lock().unwrap() = true;
        // 搜狗联动:中→中文标点,英→英文标点。
        { *t.punct_ref.lock().unwrap() = new_mode; }
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("StatusBar: mode -> chinese={} (punct 联动)", new_mode));
        refresh(&global_bar_state(), new_mode, new_mode);
    }
}

fn toggle_punct() {
    if let Some(t) = TOGGLES.get() {
        let new_punct = { let mut p = t.punct_ref.lock().unwrap(); *p = !*p; *p };
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("StatusBar: punct -> chinese_punct={}", new_punct));
        let mode = *t.mode_ref.lock().unwrap();
        refresh(&global_bar_state(), mode, new_punct);
    }
}

fn log_entry(name: &str) {
    #[cfg(feature = "dllentry_log")]
    crate::com_class_factory::log_dll_entry(&format!("StatusBar: {} clicked (TODO 实现面板)", name));
}

// ── 按钮行为 ────────────────────────────────────────────────────────────────
fn on_button(hwnd: HWND, idx: i32) {
    match idx {
        BTN_MODE => toggle_mode(),
        BTN_PUNCT => toggle_punct(),
        BTN_SYM => crate::panels::toggle_panel(crate::panels::PanelKind::Symbols, Some(hwnd)),
        BTN_EMOJI => crate::panels::toggle_panel(crate::panels::PanelKind::Emoji, Some(hwnd)),
        BTN_HAND => log_entry("手写入口(ort 推理待接入)"),
        BTN_VOCAB => log_entry("词库入口"),
        _ => {}
    }
    let _ = hwnd;
}

/// 图标点击 → 弹输入法菜单。
fn show_menu(hwnd: HWND) {
    #[cfg(feature = "dllentry_log")]
    crate::com_class_factory::log_dll_entry("Menu: show_menu ENTER");
    unsafe {
        let menu = match CreatePopupMenu() {
            Ok(m) => m,
            Err(e) => {
                #[cfg(feature = "dllentry_log")]
                crate::com_class_factory::log_dll_entry(&format!("Menu: CreatePopupMenu FAIL {:?}", e));
                return;
            }
        };
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry("Menu: CreatePopupMenu OK");
        let (is_chinese, is_punct) = {
            let g = global_bar_state();
            let g = g.lock().unwrap();
            let s = g.borrow();
            (s.is_chinese, s.is_chinese_punct)
        };
        let mode_label: Vec<u16> = (if is_chinese { "切换到英文\t✓中" } else { "切换到中文\t✓英" })
            .encode_utf16().chain(std::iter::once(0)).collect();
        let punct_label: Vec<u16> = (if is_punct { "中文标点(。、)\t✓" } else { "英文标点(.,)\t✓" })
            .encode_utf16().chain(std::iter::once(0)).collect();
        let hand_label: Vec<u16> = "手写输入".encode_utf16().chain(std::iter::once(0)).collect();
        let sym_label: Vec<u16> = "符号大全".encode_utf16().chain(std::iter::once(0)).collect();
        let emoji_label: Vec<u16> = "Emoji 表情".encode_utf16().chain(std::iter::once(0)).collect();
        let vocab_label: Vec<u16> = "词库管理".encode_utf16().chain(std::iter::once(0)).collect();
        let _ = AppendMenuW(menu, MF_STRING, IDM_MODE as usize, PCWSTR(mode_label.as_ptr()));
        let _ = AppendMenuW(menu, MF_STRING, IDM_PUNCT as usize, PCWSTR(punct_label.as_ptr()));
        let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
        let _ = AppendMenuW(menu, MF_STRING, IDM_HAND as usize, PCWSTR(hand_label.as_ptr()));
        let _ = AppendMenuW(menu, MF_STRING, IDM_SYM as usize, PCWSTR(sym_label.as_ptr()));
        let _ = AppendMenuW(menu, MF_STRING, IDM_EMOJI as usize, PCWSTR(emoji_label.as_ptr()));
        let _ = AppendMenuW(menu, MF_STRING, IDM_VOCAB as usize, PCWSTR(vocab_label.as_ptr()));

        // 菜单定位在图标按钮上方。
        // 2026-09-03 修图标崩溃:删掉 SetForegroundWindow(hwnd) —— 悬浮栏是 WS_EX_NOACTIVATE
        // 自绘窗,强行前置会扰乱 TSF 焦点判断;且 TrackPopupMenu 模态循环在 IME DLL 里与
        // ctfmon 消息泵交互脆弱。改用光标坐标定位 + TPM_RETURNCMD 即可,无需前置窗口。
        let mut rc = RECT::default();
        let _ = GetWindowRect(hwnd, &mut rc);
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("Menu: TrackPopupMenu ENTER at ({},{})", rc.left, rc.top));
        let cmd = TrackPopupMenu(
            menu,
            TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
            rc.left,
            rc.top,
            0,
            hwnd,
            None,
        );
        #[cfg(feature = "dllentry_log")]
        crate::com_class_factory::log_dll_entry(&format!("Menu: TrackPopupMenu RETURN cmd={}", cmd.0));
        let _ = DestroyMenu(menu);
        match cmd.0 as u32 {
            IDM_MODE => toggle_mode(),
            IDM_PUNCT => toggle_punct(),
            IDM_HAND => log_entry("手写入口(菜单)"),
            IDM_SYM => crate::panels::toggle_panel(crate::panels::PanelKind::Symbols, Some(hwnd)),
            IDM_EMOJI => crate::panels::toggle_panel(crate::panels::PanelKind::Emoji, Some(hwnd)),
            IDM_VOCAB => log_entry("词库入口(菜单)"),
            _ => {}
        }
    }
}

// ── WndProc ─────────────────────────────────────────────────────────────────
// 搜狗式点击/拖动共用(2026-09-01 实测修正):
//   条上 6 个区(图标+5按钮)占满整条,**没有空白区** → 旧 Hit::None 拖动分支永远到不了。
//   搜狗:按下记录起点(SetCapture),MouseMove 超 DRAG_THRESHOLD 判定为拖动
//   (整窗跟手 + 变十字 + 不再触发点击),未超阈值松手判定为点击(触发按下那个按钮)。
//   悬停一律手型(IDC_HAND),只有拖动开始后才变十字(IDC_SIZEALL)——与搜狗一致。
const DRAG_THRESHOLD: i32 = 4;

struct PressState {
    down: bool,
    dragging: bool,
    hit_icon: bool,
    hit_btn: i32,
    start_cursor: (i32, i32),
    start_pos: (i32, i32),
}
static PRESS: Mutex<RefCell<PressState>> = Mutex::new(RefCell::new(PressState {
    down: false,
    dragging: false,
    hit_icon: false,
    hit_btn: -1,
    start_cursor: (0, 0),
    start_pos: (0, 0),
}));

unsafe extern "system" fn status_bar_wnd_proc(
    hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM,
) -> LRESULT {
    match msg {
        WM_PAINT => { paint_bar(hwnd); LRESULT(0) }
        // 2026-09-01 修孤儿栏:destroy/hide 在 TSF 回调线程,跨线程 DestroyWindow
        // 返 err=5 拒绝访问。改由它们 SendMessageW(WM_CLOSE) 进来,这里在窗口自己
        // 的线程里 DestroyWindow,线程匹配 → 销毁成功。
        WM_CLOSE => { let _ = DestroyWindow(hwnd); LRESULT(0) }
        WM_NCHITTEST => LRESULT(HTCLIENT as isize),
        // 悬停一律手型(搜狗:按钮/空白都手指),拖动开始后才在 MOUSEMOVE 里变十字。
        WM_SETCURSOR => {
            let dragging = { PRESS.lock().unwrap().borrow().dragging };
            let cur = if dragging { IDC_SIZEALL } else { IDC_HAND };
            let _ = SetCursor(LoadCursorW(None, cur).unwrap_or_default());
            LRESULT(1)
        }
        WM_LBUTTONDOWN => {
            let cx = (lparam.0 & 0xFFFF) as i16 as i32;
            let (hit_icon, hit_btn) = match hit_test(cx) {
                Hit::Icon => (true, -1),
                Hit::Button(i) => (false, i),
                Hit::None => (false, -1),
            };
            let mut pt = POINT::default();
            let _ = GetCursorPos(&mut pt);
            let mut rc = RECT::default();
            let _ = GetWindowRect(hwnd, &mut rc);
            {
                let g = PRESS.lock().unwrap();
                let mut p = g.borrow_mut();
                p.down = true;
                p.dragging = false;
                p.hit_icon = hit_icon;
                p.hit_btn = hit_btn;
                p.start_cursor = (pt.x, pt.y);
                p.start_pos = (rc.left, rc.top);
            }
            SetCapture(hwnd);
            LRESULT(0)
        }
        WM_MOUSEMOVE => {
            let (down, dragging, sc, sp) = {
                let g = PRESS.lock().unwrap();
                let p = g.borrow();
                (p.down, p.dragging, p.start_cursor, p.start_pos)
            };
            if !down {
                return DefWindowProcW(hwnd, msg, wparam, lparam);
            }
            let mut pt = POINT::default();
            let _ = GetCursorPos(&mut pt);
            let dx = pt.x - sc.0;
            let dy = pt.y - sc.1;
            if !dragging {
                // 未进入拖动:超阈值才判定为拖动。
                if dx.abs() < DRAG_THRESHOLD && dy.abs() < DRAG_THRESHOLD {
                    return LRESULT(0);
                }
                {
                    let g = PRESS.lock().unwrap();
                    g.borrow_mut().dragging = true;
                }
                let _ = SetCursor(LoadCursorW(None, IDC_SIZEALL).unwrap_or_default());
            }
            // 拖动中:整窗跟手。
            let nx = sp.0 + dx;
            let ny = sp.1 + dy;
            let _ = SetWindowPos(hwnd, HWND_TOPMOST, nx, ny, 0, 0, SWP_NOACTIVATE | SWP_NOSIZE | SWP_SHOWWINDOW);
            global_bar_state().lock().unwrap().borrow_mut().pos = Some((nx, ny));
            LRESULT(0)
        }
        WM_LBUTTONUP => {
            #[cfg(feature = "dllentry_log")]
            crate::com_class_factory::log_dll_entry("Menu: WM_LBUTTONUP ENTER");
            let (was_down, was_dragging, hit_icon, hit_btn) = {
                let g = PRESS.lock().unwrap();
                let mut p = g.borrow_mut();
                let r = (p.down, p.dragging, p.hit_icon, p.hit_btn);
                p.down = false;
                p.dragging = false;
                r
            };
            let _ = ReleaseCapture();
            // 未拖动 → 判定为点击,触发按下时命中的那个按钮。
            if was_down && !was_dragging {
                #[cfg(feature = "dllentry_log")]
                crate::com_class_factory::log_dll_entry(&format!("Menu: click dispatch hit_icon={} hit_btn={}", hit_icon, hit_btn));
                if hit_icon {
                    show_menu(hwnd);
                } else if hit_btn >= 0 {
                    on_button(hwnd, hit_btn);
                }
            }
            LRESULT(0)
        }
        WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

// ── 图标加载(复用内嵌 pinyin.ico) ───────────────────────────────────────────
fn dll_hmodule() -> Option<HMODULE> {
    let mut hmod = HMODULE::default();
    let ok = unsafe {
        GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            PCWSTR(dll_hmodule as *const u16),
            &mut hmod,
        )
    };
    if ok.is_ok() && !hmod.0.is_null() {
        Some(hmod)
    } else {
        None
    }
}

fn load_bar_icon() -> HICON {
    if let Some(hmod) = dll_hmodule() {
        let h = unsafe { LoadIconW(hmod, PCWSTR(IDI_PRISIR_ICON as usize as *const u16)) };
        if let Ok(h) = h {
            if !h.0.is_null() {
                return h;
            }
        }
    }
    unsafe { LoadIconW(None, IDI_APPLICATION) }.unwrap_or(HICON(std::ptr::null_mut()))
}

// ── 自绘 ────────────────────────────────────────────────────────────────────
fn paint_bar(hwnd: HWND) {
    let state = global_bar_state();
    let (is_chinese, is_punct) = {
        let g = state.lock().unwrap();
        let s = g.borrow();
        (s.is_chinese, s.is_chinese_punct)
    };

    let mut ps = PAINTSTRUCT::default();
    unsafe {
        let hdc = BeginPaint(hwnd, &mut ps);
        let mut rc = RECT::default();
        let _ = GetClientRect(hwnd, &mut rc);

        // 苹果风:暖白底铺满(圆角由 SetWindowRgn 裁剪,无需画边框)。
        let bg = CreateSolidBrush(COLORREF(BAR_COL_BG));
        FillRect(hdc, &rc, bg);
        let _ = DeleteObject(bg);

        SetBkMode(hdc, TRANSPARENT);

        // 图标按钮(点击=菜单)。
        let icon = load_bar_icon();
        let icon_size = 18;
        let ix = (ICON_W - icon_size) / 2;
        let iy = (rc.bottom - icon_size) / 2;
        let _ = DrawIconEx(hdc, ix, iy, icon, icon_size, icon_size, 0, HBRUSH::default(), DI_NORMAL);

        // 分隔线(图标 | 按钮,苹果浅灰)。
        let sep = CreatePen(PS_SOLID, 1, COLORREF(BAR_COL_SEP));
        let old = SelectObject(hdc, sep);
        let _ = MoveToEx(hdc, ICON_W, 6, None);
        let _ = LineTo(hdc, ICON_W, rc.bottom - 6);
        SelectObject(hdc, old);
        let _ = DeleteObject(sep);

        // 按钮文本。标点按钮用 。/. 直观显示当前状态(搜狗式)。与候选窗统一 YaHei UI -17。
        let face: Vec<u16> = "Microsoft YaHei UI".encode_utf16().chain(std::iter::once(0)).collect();
        let btn_font = CreateFontW(
            -17, 0, 0, 0, FW_NORMAL.0 as i32, 0, 0, 0,
            DEFAULT_CHARSET.0 as u32, OUT_DEFAULT_PRECIS.0 as u32, CLIP_DEFAULT_PRECIS.0 as u32,
            CLEARTYPE_QUALITY.0 as u32, DEFAULT_PITCH.0 as u32, PCWSTR(face.as_ptr()),
        );
        let old_font = if !btn_font.is_invalid() { Some(SelectObject(hdc, btn_font)) } else { None };
        // 苹果风:主按钮(中/标点)= 苹果蓝,次要按钮 = 暖灰。
        let labels: [(&str, u32); N_BTNS as usize] = [
            (if is_chinese { "中" } else { "英" }, if is_chinese { BAR_COL_ACCENT } else { BAR_COL_TEXT }),
            (if is_punct { "。" } else { "." }, BAR_COL_ACCENT),
            ("符", BAR_COL_TEXT),
            ("😀", BAR_COL_TEXT),
            ("写", BAR_COL_TEXT),
            ("词", BAR_COL_TEXT),
        ];
        for (i, (text, color)) in labels.iter().enumerate() {
            let left = ICON_W + (i as i32) * BTN_W;
            let mut trc = RECT { left, top: 0, right: left + BTN_W, bottom: rc.bottom };
            SetTextColor(hdc, COLORREF(*color));
            let mut wide: Vec<u16> = text.encode_utf16().collect();
            DrawTextW(hdc, &mut wide, &mut trc, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        if let Some(old) = old_font {
            SelectObject(hdc, old);
            let _ = DeleteObject(btn_font);
        }

        let _ = EndPaint(hwnd, &ps);
    }
}
