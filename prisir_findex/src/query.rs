//! 检索:文件名/路径子串匹配,按 mtime 倒序。只读。

use crate::index::FindexEngine;
use rusqlite::params;
use serde::Serialize;

#[derive(Serialize)]
pub struct FileHit {
    pub path: String,
    pub name: String,
    pub dir: String,
    pub ext: String,
    pub size: i64,
    pub mtime: i64,
}

impl FindexEngine {
    /// 子串搜索:命中 name 或 path,按 mtime 倒序,limit 截断。
    /// q 为空串时回最近修改的 limit 条。返回 JSON 数组。
    pub fn query(&self, q: &str, limit: u32) -> Result<Vec<FileHit>, String> {
        let conn = self.conn.lock().unwrap();
        let lim = if limit == 0 { 50 } else { limit.min(500) } as i64;
        let mut out = Vec::new();
        if q.trim().is_empty() {
            let mut stmt = conn.prepare(
                "SELECT path,name,dir,ext,size,mtime FROM files ORDER BY mtime DESC LIMIT ?1",
            ).map_err(|e| format!("prepare: {e}"))?;
            let rows = stmt.query_map(params![lim], Self::row_to_hit).map_err(|e| format!("query: {e}"))?;
            for r in rows.flatten() {
                out.push(r);
            }
            return Ok(out);
        }
        // LIKE 子串,转义 % 与 _ 防注入通配。
        let esc = q.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_");
        let like = format!("%{esc}%");
        let mut stmt = conn.prepare(
            "SELECT path,name,dir,ext,size,mtime FROM files
             WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\'
             ORDER BY mtime DESC LIMIT ?2",
        ).map_err(|e| format!("prepare: {e}"))?;
        let rows = stmt.query_map(params![like, lim], Self::row_to_hit).map_err(|e| format!("query: {e}"))?;
        for r in rows.flatten() {
            out.push(r);
        }
        Ok(out)
    }

    fn row_to_hit(r: &rusqlite::Row) -> rusqlite::Result<FileHit> {
        Ok(FileHit {
            path: r.get(0)?,
            name: r.get(1)?,
            dir: r.get(2)?,
            ext: r.get(3)?,
            size: r.get(4)?,
            mtime: r.get(5)?,
        })
    }
}
