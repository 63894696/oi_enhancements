//! 索引引擎:walkdir 遍历 + 排除规则 + 批量写 SQLite(WAL)。
//! 默认排除系统/缓存目录;只存元数据,不读文件内容。

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

pub struct FindexEngine {
    pub conn: Mutex<Connection>,
    /// 首扫进度:已扫描文件数(供预计时长)。
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
    /// 开/建 SQLite 库(不扫盘)。WAL + 建表 + name 列索引。
    pub fn new(db_path: &str) -> Result<Self, String> {
        if let Some(parent) = Path::new(db_path).parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
        }
        let conn = Connection::open(db_path).map_err(|e| format!("open db: {e}"))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS files(
               id INTEGER PRIMARY KEY,
               path TEXT NOT NULL UNIQUE,
               name TEXT NOT NULL,
               dir  TEXT NOT NULL,
               ext  TEXT NOT NULL,
               size INTEGER NOT NULL,
               mtime INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
             CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
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
    /// 同步执行(调用方/壳负责放线程)。返回扫描文件数。
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
            conn.execute_batch("DELETE FROM files;").map_err(|e| format!("clear: {e}"))?;
        }

        let mut total: u64 = 0;
        for root in roots {
            let root_path = Path::new(root);
            if !root_path.exists() {
                continue;
            }
            let walker = WalkDir::new(root).follow_links(false).into_iter();
            let mut batch: Vec<(String, String, String, String, i64, i64)> = Vec::with_capacity(1024);
            for entry in walker.filter_entry(|e| !Self::is_excluded(e, &exclude)) {
                let entry = match entry {
                    Ok(e) => e,
                    Err(_) => continue, // 权限/损坏条目跳过,不中断
                };
                if !entry.file_type().is_file() {
                    continue;
                }
                let path = entry.path();
                let full = path.to_string_lossy().to_string();
                let name = path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                let dir = path.parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
                let ext = path.extension().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default();
                let (size, mtime) = entry.metadata().map(|md| (md.len() as i64, sys_mtime(&md) as i64)).unwrap_or((0, 0));
                batch.push((full, name, dir, ext, size, mtime));
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

    fn flush_batch(&self, batch: &mut Vec<(String, String, String, String, i64, i64)>) -> Result<(), String> {
        let mut conn = self.conn.lock().unwrap();
        let tx = conn.transaction().map_err(|e| format!("tx: {e}"))?;
        {
            let mut stmt = tx.prepare_cached(
                "INSERT OR REPLACE INTO files(path,name,dir,ext,size,mtime) VALUES(?1,?2,?3,?4,?5,?6)",
            ).map_err(|e| format!("prepare: {e}"))?;
            for (path, name, dir, ext, size, mtime) in batch.iter() {
                stmt.execute(params![path, name, dir, ext, size, mtime])
                    .map_err(|e| format!("insert: {e}"))?;
            }
        }
        tx.commit().map_err(|e| format!("commit: {e}"))?;
        batch.clear();
        Ok(())
    }

    /// 清空索引(关闭功能)。删 files + meta.built。
    pub fn clear(&self) -> Result<(), String> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch("DELETE FROM files; DELETE FROM meta WHERE k='built';")
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
