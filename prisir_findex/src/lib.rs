//! prisir_findex — Prisir Browser 本机文件搜索索引引擎
//!
//! 自建全盘文件名/路径索引(类 Everything),不依赖外部 es.exe,目标机无 Everything 也能用。
//! 红线:只存元数据(路径/名/目录/扩展名/大小/修改时间),不存文件内容;默认不扫盘,显式 build 才扫。
//!
//! 模块:
//!   index  — walkdir 遍历 + 排除规则 + 批量写 SQLite
//!   query  — 文件名/路径子串检索,按 mtime 倒序
//!   ffi    — C ABI 导出(opaque handle + C 字符串 JSON),仿 prisir_ime

pub mod index;
pub mod query;
pub mod ffi;

pub use index::FindexEngine;
