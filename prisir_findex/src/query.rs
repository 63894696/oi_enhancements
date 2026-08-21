//! 检索:文件名/路径子串匹配,匹配度优先排序,分页(LIMIT/OFFSET)。只读。
//! v3:子串走 FTS5「应用层分词 + unicode61」索引(中文 unigram/bigram + ASCII 词),
//!    中英统一毫秒级;分词与建库同一套(crate::index::tokenize)。LIKE 仅作 FTS 失败兜底。

use crate::index::{tokenize, FindexEngine};
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
    pub is_dir: bool,
}

#[derive(Serialize)]
pub struct SearchResult {
    pub hits: Vec<FileHit>,
    pub total: i64,
}

/// 匹配度排序的 CASE 表达式(数值越小越靠前):
///   name 精确=0 < name 前缀=1 < name 子串=2 < path 子串=3;再按 is_dir 后移、mtime 倒序。
/// 注意:name 必须带表前缀 f.(files 与 files_fts 都有 name/相关列时裸名会歧义报错)。
const RANK_CASE: &str =
    "CASE
       WHEN lower(f.name) = lower(?Q) THEN 0
       WHEN lower(f.name) LIKE lower(?Q) || '%' THEN 1
       WHEN lower(f.name) LIKE '%' || lower(?Q) || '%' THEN 2
       ELSE 3
     END";

fn escape_like(s: &str) -> String {
    s.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

/// 是否含 glob 通配符(`*` 任意串 / `?` 单字符)。
fn has_wildcard(q: &str) -> bool {
    q.contains('*') || q.contains('?')
}

/// 把含通配符的查询转成 LIKE 模式:
///   先按字面转义 \,%,_;再把 glob 的 * → %(任意串)、? → _(单字符)。
/// 其余字符一律按字面(包括中文/空格/点),语义与 Everything 的通配一致。
/// 例: "*.docx"→"%.docx"  "报告*"→"报告%"  "2026*报告"→"2026%报告"  "me?ting"→"me_ting"
fn wildcard_to_like(q: &str) -> String {
    // 用占位符保护 glob 通配,先转义 LIKE 元字符,再换回 glob 语义。
    const STAR: char = '\u{1}';
    const QUEST: char = '\u{2}';
    let protected: String = q.chars().map(|c| match c {
        '*' => STAR,
        '?' => QUEST,
        _ => c,
    }).collect();
    let escaped = escape_like(&protected);
    escaped.replace(STAR, "%").replace(QUEST, "_")
}

/// 是否纯 CJK 的 2 字 bigram token。
fn is_bigram(t: &str) -> bool {
    let chars: Vec<char> = t.chars().collect();
    chars.len() == 2 && chars.iter().all(|&c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}

/// 是否含 CJK(决定是否能有 bigram)。
fn has_cjk(t: &str) -> bool {
    t.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}

/// 把用户查询串转成 FTS5 MATCH 表达式:
///   分词后,优先取 bigram(2 字纯中文)+ ASCII 词,AND 连接(顺序无关、全需命中);
///   若无 bigram(单字/纯英文),用全部 token AND。
/// 返回 None 表示无可用 token(退 LIKE)。
fn to_match(q: &str) -> Option<String> {
    let toks: Vec<String> = tokenize(q).split_whitespace().map(|s| s.to_string()).collect();
    if toks.is_empty() {
        return None;
    }
    let bigrams: Vec<&String> = toks.iter().filter(|t| is_bigram(t)).collect();
    let ascii: Vec<&String> = toks.iter().filter(|t| !has_cjk(t)).collect();
    let chosen: Vec<&String> = if !bigrams.is_empty() {
        bigrams.into_iter().chain(ascii).collect()
    } else {
        toks.iter().collect()
    };
    if chosen.is_empty() {
        return None;
    }
    // 每个 token 加引号,避免特殊字符被当 MATCH 语法;AND 连接。
    let expr = chosen
        .iter()
        .map(|t| format!("\"{}\"", t.replace('"', "")))
        .collect::<Vec<_>>()
        .join(" AND ");
    Some(expr)
}

/// 提取通配模式的最长字面**前缀**(第一个 * 或 ? 之前的部分),用于 NOCASE 索引区间下推。
/// 如 "报告*"→"报告"、"read*"→"read"、"*.docx"→""(无前缀)、"2026*报告"→"2026"。
/// 返回原始字面(未转 LIKE),供 name >= prefix AND name < prefix+1 区间。
fn literal_prefix(q: &str) -> String {
    q.chars().take_while(|&c| c != '*' && c != '?').collect()
}

impl FindexEngine {
    /// 子串搜索:name/path 命中,匹配度排序 + mtime 倒序,limit/offset 分页。
    /// q 空串时回最近修改的 limit 条。返回 hits + total。
    /// total 惰性:只统计到 limit+offset+1 即停(首页避免全表 COUNT 开销),
    /// 达到上限回 -1 表示「至少 offset+实返数,可能更多」,否则回精确值。前端按此翻页。
    pub fn query(&self, q: &str, limit: u32, offset: u32) -> Result<SearchResult, String> {
        let conn = self.conn.lock().unwrap();
        let lim = if limit == 0 { 50 } else { limit.min(500) } as i64;
        let off = offset as i64;
        let q = q.trim();
        let cap = lim + off + 1; // total 统计上限:够判断「是否还有下一页」即可

        if q.is_empty() {
            let total: i64 = conn.query_row("SELECT COUNT(*) FROM files", [], |r| r.get(0)).unwrap_or(0);
            let mut stmt = conn.prepare(
                "SELECT path,name,dir,ext,size,mtime,is_dir FROM files ORDER BY mtime DESC LIMIT ?1 OFFSET ?2",
            ).map_err(|e| format!("prepare: {e}"))?;
            let rows = stmt.query_map(params![lim, off], Self::row_to_hit).map_err(|e| format!("query: {e}"))?;
            return Ok(SearchResult { hits: rows.flatten().collect(), total });
        }

        // 通配符查询(* / ?)走 glob 路径:FTS 是整词匹配处理不了部分通配,
        // 转成 LIKE 模式(* → %,? → _),按 mtime 索引扫 + LIMIT 提前停。
        if has_wildcard(q) {
            return self.query_glob(&conn, q, lim, off, cap);
        }

        // 任何非空查询优先走 FTS 分词索引(中英统一);失败/无 token 才退 LIKE。
        // LIKE 退路的 RANK 用裸列名(无 JOIN 不歧义),FTS 路用 f.name(JOIN 必须带前缀)。
        if let Some(match_expr) = to_match(q) {
            if let Ok(res) = self.query_fts(&conn, q, &match_expr, lim, off, cap) {
                return Ok(res);
            }
        }
        self.query_like(&conn, q, lim, off, cap)
    }

    /// 安全体检:最近 mtime_unix 秒内**改动过的可执行/脚本文件**(按 mtime 倒序)。
    /// 用于一键「最近新增可执行文件」体检:刚下载/刚落地的程序 mtime 即落地时间,
    /// 纯元数据判断,不读内容,符合索引红线。exts 为小写扩展名(不带点)。
    pub fn query_recent_exec(&self, since_unix: i64, exts: &[String], limit: u32) -> Result<SearchResult, String> {
        if exts.is_empty() {
            return Ok(SearchResult { hits: vec![], total: 0 });
        }
        let conn = self.conn.lock().unwrap();
        let lim = if limit == 0 { 500 } else { limit.min(2000) } as i64;
        let marks = exts.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        // 全部用匿名 ? 占位,参数按出现顺序绑定:since, exts..., (count 无 limit / select 有 limit)。
        let sql = format!(
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE is_dir=0 AND mtime >= ? AND ext IN ({marks})
             ORDER BY mtime DESC LIMIT ?"
        );
        let cnt_sql = format!(
            "SELECT COUNT(*) FROM files WHERE is_dir=0 AND mtime >= ? AND ext IN ({marks})"
        );
        let mk = |with_limit: bool| -> Vec<Box<dyn rusqlite::ToSql>> {
            let mut v: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(since_unix)];
            for e in exts {
                v.push(Box::new(e.clone()));
            }
            if with_limit {
                v.push(Box::new(lim));
            }
            v
        };
        let cnt_prm = mk(false);
        let total: i64 = conn
            .query_row(&cnt_sql, cnt_prm.iter().map(|b| b.as_ref()).collect::<Vec<_>>().as_slice(), |r| r.get(0))
            .unwrap_or(-1);
        let mut stmt = conn.prepare_cached(&sql).map_err(|e| format!("recent_exec prepare: {e}"))?;
        let q_prm = mk(true);
        let rows = stmt
            .query_map(q_prm.iter().map(|b| b.as_ref()).collect::<Vec<_>>().as_slice(), Self::row_to_hit)
            .map_err(|e| format!("recent_exec query: {e}"))?;
        Ok(SearchResult { hits: rows.flatten().collect(), total })
    }

    /// 通配符搜索:`*` 任意串、`?` 单字符,语义同 Everything。
    /// 转 LIKE 模式后按 mtime 索引降序扫 + 提前停。
    /// 性能关键:**不单独 count**(稀疏命中时 count 要扫全表确认总数,再查一遍=双倍成本)。
    /// 改为直接取 limit+1 条:返回数 > limit 即知「还有更多」(total=-1),否则 total=实返数。
    /// 排序退化为纯 mtime(通配本就是批量捞一类文件,mtime 是用户最想要的序)。
    fn query_glob(&self, conn: &rusqlite::Connection, q: &str, lim: i64, off: i64, _cap: i64) -> Result<SearchResult, String> {
        let like = wildcard_to_like(q);
        // 全通配(如 "*" / "*.*" )等价于「全部」,直接按 mtime 回。
        let all_literal = like.chars().all(|c| c == '%' || c == '_');

        // 取 cap=limit+offset+1 条;若实返==cap 说明到上限可能还有(total=-1)。
        // 优化:有字面前缀(报告*/read*/2026*报告)时,先用 NOCASE 索引区间缩候选,
        // 再用完整 LIKE 模式过滤 —— 中文前缀从扫整个索引 348ms → 区间下推亚毫秒。
        let prefix = literal_prefix(q);
        let use_prefix = !all_literal && !prefix.is_empty();
        // 前缀上界:末字符 +1(字典序下一个串),界定 name >= prefix AND name < upper。
        let upper: String = {
            let mut cs: Vec<char> = prefix.chars().collect();
            match cs.pop() {
                Some(last) => {
                    let mut u: String = cs.into_iter().collect();
                    u.push(char::from_u32(last as u32 + 1).unwrap_or(last));
                    u
                }
                None => prefix.clone(),
            }
        };

        // 纯后缀通配 "*.ext"(只一个 * 开头 + . + 纯字母数字后缀):直通 ext 列等值查,
        // 走 idx_files_ext(全表扫 1551ms → 0.2ms)。按类型捞文件的高频操作。
        let pure_ext: Option<String> = {
            let b = q.as_bytes();
            if b.len() >= 3 && b[0] == b'*' && b[1] == b'.' {
                let ext = &q[2..];
                if !ext.is_empty()
                    && ext.chars().all(|c| c.is_ascii_alphanumeric())
                {
                    Some(ext.to_ascii_lowercase())
                } else {
                    None
                }
            } else {
                None
            }
        };

        let sql = if all_literal {
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files ORDER BY mtime DESC LIMIT ?1 OFFSET ?2".to_string()
        } else if pure_ext.is_some() {
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE ext = ?1 ORDER BY mtime DESC LIMIT ?2 OFFSET ?3".to_string()
        } else if use_prefix {
            // name 在 [prefix, upper) 区间内(NOCASE 区间下推)且整体匹配 LIKE 模式
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE name >= ?1 COLLATE NOCASE AND name < ?2 COLLATE NOCASE
               AND (name LIKE ?3 ESCAPE '\\' OR path LIKE ?3 ESCAPE '\\')
             ORDER BY mtime DESC LIMIT ?4 OFFSET ?5".to_string()
        } else {
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\'
             ORDER BY mtime DESC LIMIT ?2 OFFSET ?3".to_string()
        };
        // total 稳定语义(翻页不漂移):能走索引的两条快路径(ext 等值 / 前缀区间)count 极快,
        // 给精确值;mtime 全表扫的路径(纯后缀/中间无锚点)count 太贵,给 -1 让前端滚动探。
        let total: i64 = if all_literal {
            conn.query_row("SELECT COUNT(*) FROM files", [], |r| r.get(0)).unwrap_or(-1)
        } else if let Some(ext) = &pure_ext {
            conn.query_row("SELECT COUNT(*) FROM files WHERE ext=?1", params![ext], |r| r.get(0)).unwrap_or(-1)
        } else if use_prefix {
            conn.query_row(
                "SELECT COUNT(*) FROM files WHERE name >= ?1 COLLATE NOCASE AND name < ?2 COLLATE NOCASE
                 AND (name LIKE ?3 ESCAPE '\\' OR path LIKE ?3 ESCAPE '\\')",
                params![prefix, upper, like], |r| r.get(0),
            ).unwrap_or(-1)
        } else {
            -1
        };

        // 真 LIMIT/OFFSET 翻页:不 cap 多取、不手 drain —— 手凑 total 的 cap 方案在翻页中途
        // 会让 total 从 -1 跳成 off+len 的伪精确值(offset=100 时甚至越界空页),前端判断崩。
        let mut stmt = conn.prepare_cached(&sql).map_err(|e| format!("glob prepare: {e}"))?;
        let rows: Vec<FileHit> = if all_literal {
            stmt.query_map(params![lim, off], Self::row_to_hit).map_err(|e| format!("glob query: {e}"))?.flatten().collect()
        } else if let Some(ext) = pure_ext {
            stmt.query_map(params![ext, lim, off], Self::row_to_hit).map_err(|e| format!("glob query: {e}"))?.flatten().collect()
        } else if use_prefix {
            stmt.query_map(params![prefix, upper, like, lim, off], Self::row_to_hit).map_err(|e| format!("glob query: {e}"))?.flatten().collect()
        } else {
            stmt.query_map(params![like, lim, off], Self::row_to_hit).map_err(|e| format!("glob query: {e}"))?.flatten().collect()
        };
        Ok(SearchResult { hits: rows, total })
    }

    /// FTS 命中数阈值:超过它,为自定义 rank 取全量 rowid 排序就不划算(如 'py' 命中 25 万),
    /// 改走 mtime 索引扫 + 子串过滤 + LIMIT 提前停(热词按 mtime 扫前几条即凑够,亚毫秒)。
    const FTS_RANK_MAX: i64 = 5000;

    /// FTS5 分词索引检索 + 匹配度排序 + 惰性总数。
    /// match_expr 已内联为 SQL 字面量(单引号转义),token 已加引号,安全。
    /// files_fts.name_tok 存的是分词串,rowid 与 files.id 对齐。
    /// 命中数 > FTS_RANK_MAX 时自适应切到 mtime-scan 快路径。
    fn query_fts(&self, conn: &rusqlite::Connection, q: &str, match_expr: &str, lim: i64, off: i64, cap: i64) -> Result<SearchResult, String> {
        let m = match_expr.replace('\'', "''"); // SQL 字面量内联
        // 先数命中(上限探到 FTS_RANK_MAX+1 即可判断是否超阈值,再决定是否值得排序)
        let probe = Self::FTS_RANK_MAX + 1;
        let cnt: i64 = conn.query_row(
            &format!("SELECT COUNT(*) FROM (SELECT 1 FROM files_fts WHERE files_fts MATCH '{m}' LIMIT ?1)"),
            params![probe], |r| r.get(0),
        ).map_err(|e| format!("fts count: {e}"))?;

        if cnt > Self::FTS_RANK_MAX {
            // 命中爆炸:自定义 rank 排序要取全量,不值。按 mtime 扫 + 子串过滤,提前停。
            return self.query_by_mtime(conn, q, lim, off, cap);
        }

        // 命中可控:FTS + 匹配度排序(精确词这条路毫秒级)。
        let total = if cnt >= cap { -1 } else { cnt };
        let sql = format!(
            "SELECT f.path,f.name,f.dir,f.ext,f.size,f.mtime,f.is_dir FROM files f
             JOIN files_fts t ON f.id=t.rowid
             WHERE files_fts MATCH '{m}'
             ORDER BY {rank}, f.is_dir ASC, f.mtime DESC
             LIMIT ?1 OFFSET ?2",
            rank = RANK_CASE.replace("?Q", "?3")
        );
        let mut stmt = conn.prepare_cached(&sql).map_err(|e| format!("fts prepare: {e}"))?;
        let rows = stmt.query_map(params![lim, off, q], Self::row_to_hit)
            .map_err(|e| format!("fts query: {e}"))?;
        let hits: Vec<FileHit> = rows.flatten().collect();
        Ok(SearchResult { hits, total })
    }

    /// 热词快路径:按 mtime 索引降序扫,name/path 子串过滤,LIMIT 提前停。
    /// 不取全量、不排序全量——命中越多越快(前几条新文件即凑够 limit)。
    /// 排序退化为纯 mtime(命中爆炸时 rank 区分度本就近零,mtime 才是用户要的)。
    fn query_by_mtime(&self, conn: &rusqlite::Connection, q: &str, lim: i64, off: i64, cap: i64) -> Result<SearchResult, String> {
        let like = format!("%{}%", escape_like(q));
        let cnt: i64 = conn.query_row(
            "SELECT COUNT(*) FROM (SELECT 1 FROM files WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\' LIMIT ?2)",
            params![like, cap], |r| r.get(0),
        ).map_err(|e| format!("mtime count: {e}"))?;
        let total = if cnt >= cap { -1 } else { cnt };

        let mut stmt = conn.prepare_cached(
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\'
             ORDER BY mtime DESC
             LIMIT ?2 OFFSET ?3",
        ).map_err(|e| format!("mtime prepare: {e}"))?;
        let rows = stmt.query_map(params![like, lim, off], Self::row_to_hit)
            .map_err(|e| format!("mtime query: {e}"))?;
        let hits: Vec<FileHit> = rows.flatten().collect();
        Ok(SearchResult { hits, total })
    }

    /// LIKE 兜底(FTS 不可用/无 token 时)。惰性 total 同上。中文中缀扫百万行为亚秒级但不达毫秒,
    /// 仅作 FTS 失败时的退路,正常路径不进这里。RANK 用裸列名(单表无 JOIN 不歧义)。
    fn query_like(&self, conn: &rusqlite::Connection, q: &str, lim: i64, off: i64, cap: i64) -> Result<SearchResult, String> {
        let rank = RANK_CASE.replace("f.name", "name");
        let like = format!("%{}%", escape_like(q));
        let cnt: i64 = conn.query_row(
            "SELECT COUNT(*) FROM (SELECT 1 FROM files WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\' LIMIT ?2)",
            params![like, cap], |r| r.get(0),
        ).map_err(|e| format!("like count: {e}"))?;
        let total = if cnt >= cap { -1 } else { cnt };

        let sql = format!(
            "SELECT path,name,dir,ext,size,mtime,is_dir FROM files
             WHERE name LIKE ?1 ESCAPE '\\' OR path LIKE ?1 ESCAPE '\\'
             ORDER BY {rank}, is_dir ASC, mtime DESC
             LIMIT ?2 OFFSET ?3",
            rank = rank.replace("?Q", "?4")
        );
        let mut stmt = conn.prepare(&sql).map_err(|e| format!("like prepare: {e}"))?;
        let rows = stmt.query_map(params![like, lim, off, q], Self::row_to_hit)
            .map_err(|e| format!("like query: {e}"))?;
        Ok(SearchResult { hits: rows.flatten().collect(), total })
    }

    fn row_to_hit(r: &rusqlite::Row) -> rusqlite::Result<FileHit> {
        Ok(FileHit {
            path: r.get(0)?,
            name: r.get(1)?,
            dir: r.get(2)?,
            ext: r.get(3)?,
            size: r.get(4)?,
            mtime: r.get(5)?,
            is_dir: r.get::<_, i64>(6)? != 0,
        })
    }
}
