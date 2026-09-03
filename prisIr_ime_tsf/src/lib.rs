//! prisir_ime_tsf — Prisir 输入法 TSF COM 实现 (Windows)
//!
//! 模块清单:
//!   ffi                 — C ABI FFI 桥(LoadLibrary prisIr_ime.dll + 5 个 prisir_tsf_*)
//!   tsf_input_processor — ITfTextInputProcessor + ITfSource COM 接口(T2 骨架)
//!   tsf_text_store      — ITextStoreACP 24 个方法 vtable(T3 实现)
//!   keystroke           — PinyinBuffer 状态机(纯 Rust,无 TSF 依赖)
//!                         T7: 加 method 字段 + 全局 active_method + query_candidates 分发
//!   hotkey              — T7: ALLOWED_VKS + normalize_vk + InputMethod 枚举 + parse_method
//!                         + PINYIN_TRIGGER_KEY / WUBI_TRIGGER_KEY(沿用 voice_input 数值)
//!   com_class_factory   — IClassFactory + DllGetClassObject(DLL 入口)
//!   register            — CTF HKCU 注册表写入(纯函数 + do_register/unregister/status/enable/disable) — T4/T5
//!   daemon              — TSF 守护进程:WTS 轮询 + DLL 真热重载 + watchdog — T5
//!   ipc                 — stdio JSON-RPC server(7 个 method:version/status/register/unregister/enable/disable/query) — T6
//!   langbar             — T20: ITfLangBarItem + ITfLangBarItemButton 按钮(中/英切换)
//!   elevate             — T24 P2: ShellExecuteExW "runas" 自动 UAC 提升
//!   register_export     — T24 P3: 标准 COM DllRegisterServer / DllUnregisterServer

pub mod ffi;
pub mod tsf_input_processor;
pub mod edit_session;
pub mod candidate_window;
pub mod panels;
pub mod conversion_mode;
pub mod status_bar;
pub mod tsf_text_store;
pub mod keystroke;
pub mod hotkey;
pub mod com_class_factory;
pub mod register;
pub mod daemon;
pub mod ipc;
pub mod about;
pub mod langbar;
pub mod elevate;
pub mod register_export;

pub use tsf_input_processor::TsfInputProcessor;
pub use tsf_text_store::TsfTextStore;
pub use keystroke::{PinyinBuffer, Candidate};
pub use hotkey::{InputMethod, PINYIN_TRIGGER_KEY, WUBI_TRIGGER_KEY, parse_method, normalize_vk};
pub use langbar::PrisirLangBarItem;