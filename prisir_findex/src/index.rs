//! 索引引擎:walkdir 遍历 + 排除规则 + 批量写 SQLite(WAL)+ FTS5 子串索引。
//! v2:目录也入库(is_dir);检索带匹配度排序与分页;FTS5 加速子串到毫秒级。
//! v3:FTS 改「应用层分词 + unicode61」。中文文件名 trigram/unicode61 都不切 CJK 单字,
//!    故建库时把 name 切成「ASCII 词 + 中文 unigram + 中文 bigram」存进 name_tok 列,
//!    查询用同一套分词 + AND 连接走索引 —— 中英子串统一毫秒级(Everything 同源 n-gram 思路)。
//! 红线不变:只存元数据(路径/名/目录/扩展名/大小/修改时间/是否目录),不读文件内容。

use rusqlite::{params, Connection};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use walkdir::WalkDir;

/// 默认排除的目录名(不递归进入)。用户可在 build 时通过 roots_json 的 exclude 追加。
const DEFAULT_EXCLUDE: &[&str] = &[
    "$Recycle.Bin", "System Volume Information", "Windows", "Program Files",
    "Program Files (x86)", "ProgramData", "node_modules", ".git", "__pycache__",
    ".pytest_cache", "target", "AppData", "Recovery", "PerfLogs",
];

/// 是否 CJK 统一表意文字(常用区)。中文名靠它切 unigram/bigram。
#[inline]
fn is_cjk(c: char) -> bool {
    ('\u{4e00}'..='\u{9fff}').contains(&c)
}

/// 把文件名切成检索 token:
///   - ASCII 连续字母数字段 → 小写词
///   - 每个 CJK 字 → unigram(单字)
///   - 相邻 CJK 字两两 → bigram(双子串,支撑 2 字及以上中文子串查询)
/// 建库与查询必须用同一套,保证可匹配。返回空格分隔的 token 串。
pub fn tokenize(s: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut cjk_run: Vec<char> = Vec::new();
    let flush_ascii = |buf: &mut String, out: &mut Vec<String>| {
        if !buf.is_empty() {
            out.push(std::mem::take(buf));
            // ASCII 词自身也作为整体入(已是整体),无需再切
        }
    };
    let flush_cjk = |cjk_run: &mut Vec<char>, out: &mut Vec<String>| {
        if cjk_run.is_empty() {
            return;
        }
        for &c in cjk_run.iter() {
            out.push(c.to_string()); // unigram
        }
        for w in cjk_run.windows(2) {
            out.push(w.iter().collect()); // bigram
        }
        cjk_run.clear();
    };
    for c in s.chars() {
        if is_cjk(c) {
            flush_ascii(&mut buf, &mut out);
            cjk_run.push(c);
        } else if c.is_alphanumeric() {
            flush_cjk(&mut cjk_run, &mut out);
            buf.push(c.to_ascii_lowercase());
        } else {
            flush_ascii(&mut buf, &mut out);
            flush_cjk(&mut cjk_run, &mut out);
        }
    }
    flush_ascii(&mut buf, &mut out);
    flush_cjk(&mut cjk_run, &mut out);
    out.join(" ")
}

pub struct FindexEngine {
    pub conn: Mutex<Connection>,
    /// 首扫进度:已扫描条目数(供预计时长)。
    pub scanned: Arc<AtomicU64>,
    /// 首扫是否进行中。
    pub building: Arc<Mutex<bool>>,
    /// 上次完成扫描的 unix 秒。
    pub last_scan: Arc<AtomicU64>,
}

fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn sys_mtime(md: &std::fs::Metadata) -> u64 {
    md.modified().ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs()).unwrap_or(0)
}

impl FindexEngine {
    /// 开/建 SQLite 库(不扫盘)。WAL + 主表 + FTS5 trigram 索引。
    pub fn new(db_path: &str) -> Result<Self, String> {
        if let Some(parent) = Path::new(db_path).parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
        }
        let conn = Connection::open(db_path).map_err(|e| format!("open db: {e}"))?;
        // files_fts 仅存分词后的 name_tok(不存原文):外部内容表 content='files' 仅存 rowid 映射,
        // 但这里我们不让它回查原文(name_tok 是自含的分词结果),故用普通 FTS 表 + 手动 rowid 对齐。
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS files(
               id INTEGER PRIMARY KEY,
               path TEXT NOT NULL UNIQUE,
               name TEXT NOT NULL,
               dir  TEXT NOT NULL,
               ext  TEXT NOT NULL,
               size INTEGER NOT NULL,
               mtime INTEGER NOT NULL,
               is_dir INTEGER NOT NULL DEFAULT 0
             );
             CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
             CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
             -- NOCASE 索引:让通配/前缀查询(报告% / read%)走 name>? AND name<? 区间下推,
             -- 否则 BINARY 序下 LIKE 前缀只能扫整个索引(中文前缀 348ms → NOCASE 0.01ms)。
             CREATE INDEX IF NOT EXISTS idx_files_name_nocase ON files(name COLLATE NOCASE);
             -- ext 索引:让 *.docx 这类纯后缀通配直通 ext='docx' 等值查(全表扫 1551ms → 0.2ms)。
             CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext);
             CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
               name_tok,
               tokenize='unicode61'
             );
             CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);",
        ).map_err(|e| format!("init db: {e}"))?;
        Ok(Self {
            conn: Mutex::new(conn),
            scanned: Arc::new(AtomicU64::new(0)),
            building: Arc::new(Mutex::new(false)),
            last_scan: Arc::new(AtomicU64::new(0)),
        })
    }

    /// 是否已建过索引(meta.built=1 且 files 非空)。
    pub fn is_enabled(&self) -> bool {
        let conn = self.conn.lock().unwrap();
        let built: Option<String> = conn
            .query_row("SELECT v FROM meta WHERE k='built'", [], |r| r.get(0))
            .ok();
        if built.as_deref() != Some("1") {
            return false;
        }
        let n: i64 = conn.query_row("SELECT COUNT(*) FROM files", [], |r| r.get(0)).unwrap_or(0);
        n > 0
    }

    pub fn indexed_count(&self) -> u64 {
        let conn = self.conn.lock().unwrap();
        conn.query_row("SELECT COUNT(*) FROM files", [], |r| r.get::<_, i64>(0))
            .unwrap_or(0).max(0) as u64
    }

    /// 首扫/重建索引。roots = 要扫的根目录列表;extra_exclude = 追加排除目录名。
    /// 同步执行(调用方/壳负责放线程)。返回扫描条目数。
    pub fn build(&self, roots: &[String], extra_exclude: &[String]) -> Result<u64, String> {
        {
            let mut b = self.building.lock().unwrap();
            if *b {
                return Err("already_building".to_string());
            }
            *b = true;
        }
        self.scanned.store(0, Ordering::SeqCst);
        let result = self.build_inner(roots, extra_exclude);
        *self.building.lock().unwrap() = false;
        if result.is_ok() {
            self.last_scan.store(now_unix(), Ordering::SeqCst);
        }
        result
    }

    fn build_inner(&self, roots: &[String], extra_exclude: &[String]) -> Result<u64, String> {
        let mut exclude: Vec<String> = DEFAULT_EXCLUDE.iter().map(|s| s.to_string()).collect();
        exclude.extend(extra_exclude.iter().cloned());

        // 清库重建(首扫语义:全量重建,保证新鲜)。
        {
            let conn = self.conn.lock().unwrap();
            conn.execute_batch("DELETE FROM files; DELETE FROM files_fts;")
                .map_err(|e| format!("clear: {e}"))?;
        }

        let mut total: u64 = 0;
        for root in roots {
            let root_path = Path::new(root);
            if !root_path.exists() {
                continue;
            }
            let walker = WalkDir::new(root).follow_links(false).into_iter();
            // (path, name, dir, ext, size, mtime, is_dir)
            let mut batch: Vec<(String, String, String, String, i64, i64, i64)> = Vec::with_capacity(1024);
            for entry in walker.filter_entry(|e| !Self::is_excluded(e, &exclude)) {
                let entry = match entry {
                    Ok(e) => e,
                    Err(_) => continue, // 权限/损坏条目跳过,不中断
                };
                let is_file = entry.file_type().is_file();
                let is_dir = entry.file_type().is_dir();
                if !is_file && !is_dir {
                    continue; // 符号链接/其他跳过
                }
                // 根目录自身跳过(不入库),只入其子项
                if entry.depth() == 0 {
                    continue;
                }
                let path = entry.path();
                let full = path.to_string_lossy().to_string();
                let name = path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                let dir = path.parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
                let ext = if is_file {
                    path.extension().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default()
                } else {
                    String::new()
                };
                let (size, mtime) = entry.metadata().map(|md| (md.len() as i64, sys_mtime(&md) as i64)).unwrap_or((0, 0));
                batch.push((full, name, dir, ext, size, mtime, if is_dir { 1 } else { 0 }));
                total += 1;
                self.scanned.store(total, Ordering::SeqCst);
                if batch.len() >= 1024 {
                    self.flush_batch(&mut batch)?;
                }
            }
            if !batch.is_empty() {
                self.flush_batch(&mut batch)?;
            }
        }

        let conn = self.conn.lock().unwrap();
        conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('built','1')", [])
            .map_err(|e| format!("meta: {e}"))?;
        conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_scan',?1)", params![now_unix() as i64])
            .map_err(|e| format!("meta: {e}"))?;
        Ok(total)
    }

    fn is_excluded(entry: &walkdir::DirEntry, exclude: &[String]) -> bool {
        if entry.file_type().is_dir() {
            if let Some(name) = entry.file_name().to_str() {
                return exclude.iter().any(|e| e.eq_ignore_ascii_case(name));
            }
        }
        false
    }

    fn flush_batch(&self, batch: &mut Vec<(String, String, String, String, i64, i64, i64)>) -> Result<(), String> {
        let mut conn = self.conn.lock().unwrap();
        let tx = conn.transaction().map_err(|e| format!("tx: {e}"))?;
        {
            let mut ins = tx.prepare_cached(
                "INSERT OR REPLACE INTO files(path,name,dir,ext,size,mtime,is_dir) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            ).map_err(|e| format!("prepare: {e}"))?;
            // files_fts 是普通 FTS 表,rowid 与 files.id 对齐(靠 path 反查 id)。
            let mut fts = tx.prepare_cached(
                "INSERT INTO files_fts(rowid,name_tok) VALUES((SELECT id FROM files WHERE path=?1),?2)",
            ).map_err(|e| format!("prepare fts: {e}"))?;
            for (path, name, dir, ext, size, mtime, is_dir) in batch.iter() {
                ins.execute(params![path, name, dir, ext, size, mtime, is_dir])
                    .map_err(|e| format!("insert: {e}"))?;
                // 存分词串而非原文,中文才能子串命中(unicode61 不自切 CJK)。
                let tok = tokenize(name);
                fts.execute(params![path, tok])
                    .map_err(|e| format!("insert fts: {e}"))?;
            }
        }
        tx.commit().map_err(|e| format!("commit: {e}"))?;
        batch.clear();
        Ok(())
    }

    /// 清空索引(关闭功能)。删 files + files_fts + meta.built。
    pub fn clear(&self) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch("DELETE FROM files; DELETE FROM files_fts; DELETE FROM meta WHERE k='built';")
            .map_err(|e| format!("clear: {e}"))?;
        self.last_scan.store(0, Ordering::SeqCst);
        Ok(())
    }

    pub fn last_scan(&self) -> u64 {
        self.last_scan.load(Ordering::SeqCst)
    }

    pub fn is_building(&self) -> bool {
        *self.building.lock().unwrap()
    }

    pub fn scanned(&self) -> u64 {
        self.scanned.load(Ordering::SeqCst)
    }
}
