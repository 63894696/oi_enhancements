//! 词库访问(rusqlite 读 ciku.db)。逻辑对齐 Python database.py。
//! 表:phrase(jp,key,value,weight) pinyin(jp,key,value,weight) wubi86(key,value,weight)。
//! 注意:pinyin 表上游混入约 31 万词组,查单字必须 LENGTH(value)=1 过滤。

use rusqlite::{Connection, OpenFlags, Result as SqlResult};

/// 主库写互斥(2026-09-04 主库可写):防止用户连点「删/加/改」并发开多个写连接撞 SQLITE_BUSY。
/// 进程内单例;词库管理是低频操作,串行无性能问题。
pub(crate) static MAIN_WRITE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub struct CikuDb {
    conn: Connection,
    /// 学习记录独立连接(2026-09-03):写 user.db,不碰主库 ciku.db。
    /// 主库只读 → 文件大小/mtime 永不变 → 索引指纹稳定 → 切换即打不重建。
    /// None = user.db 打开失败(学习静默失效,不影响打字)。
    user_conn: Option<Connection>,
}

/// (value, weight)
pub type Weighted = (String, i64);
/// (key, value, weight)
pub type Row = (String, String, i64);
/// (value, seq) — 学习词:学成置顶固定权重 + 学习次序(位置锁定用)
pub type UserWord = (String, i64);

/// 学成置顶固定权重(2026-09-04):必须大于词库静态权重动态范围(实测 max≈26000),
/// 让用户第一次选某词就一步跳到该拼音下学成词第一梯队。之后 weight 不再变
/// (不再 +1 累积、不做时间衰减)→ 位置永久锁定,解决「偶尔用的词位置老变、
/// 无法固化调用」。多个学成词按 seq(学习先后)稳定排序,先学在前。
pub const LEARN_PIN_WEIGHT: i64 = 100_000;

fn next_prefix(prefix: &str) -> String {
    // shij -> shik,用于 key >= p AND key < next(p) 走索引的范围查询。
    if prefix.is_empty() {
        return prefix.to_string();
    }
    let mut chars: Vec<char> = prefix.chars().collect();
    let mut i = chars.len() as isize - 1;
    while i >= 0 && chars[i as usize] == 'z' {
        i -= 1;
    }
    if i < 0 {
        // 全是 z(或单字母 z):没有可 +1 的前缀字符。拼音 key 全是 ASCII 小写字母,
        // 'z' 之后用 '{'(0x7B,z 的下一个 ASCII)作开区间上界,覆盖所有 z* 音节
        // (z/za/zai/.../zuo/zh/zha/.../zhuo)。2026-09-02 修:旧返回 "za" 把范围缩成
        // [z,za),单字母 z 只剩 1 个候选(在),前缀高权桶失效。
        return "{".to_string();
    }
    chars[i as usize] = std::char::from_u32(chars[i as usize] as u32 + 1).unwrap_or(chars[i as usize]);
    chars[..=i as usize].iter().collect()
}

impl CikuDb {
    pub fn open(path: &str) -> SqlResult<Self> {
        // 主库只读打开(2026-09-03 修「学习写库→索引失效重建 69s」):
        // 之前 Connection::open 是读写模式,即使只 SELECT,SQLite 在某些情况下也会刷
        // 主文件 mtime;更严重的是 learn() 每次上屏 UPDATE/INSERT 直接改主库内容,
        // 大小+mtime 一变,load_or_build_index 的指纹(大小+mtime)就对不上 → 全量重建
        // 322MB 索引(69s)→ 切换输入法后要等几十秒才能打字。
        // 只读打开后主库物理上不可写,指纹永久稳定,索引建好一次永久秒开。
        let conn = Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        // 学习记录走独立 user.db(与主库同目录,`<name>.user.db`)。打开失败不致命——
        // 学习静默失效,打字/查询完全不受影响(满足「切换即打」优先于学习)。
        let user_conn = Self::open_user_db(path).ok();
        Ok(CikuDb { conn, user_conn })
    }

    /// 打开/创建学习库 user.db,并确保 user_word 表存在(含 seq 学习次序列)。
    fn open_user_db(main_path: &str) -> SqlResult<Connection> {
        let user_path = std::path::Path::new(main_path).with_extension("user.db");
        let uc = Connection::open(user_path)?;
        uc.execute_batch(
            "CREATE TABLE IF NOT EXISTS user_word(
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                seq INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(key, value)
            );",
        )?;
        // 对 2026-09-04 之前建的旧 user.db(无 seq 列)补列;已存在则忽略错误。
        let _ = uc.execute_batch("ALTER TABLE user_word ADD COLUMN seq INTEGER NOT NULL DEFAULT 0;");
        Ok(uc)
    }

    /// 下一个学习次序号(现有 max(seq)+1,从 1 起)。学成词按 seq 升序锁定位置。
    fn next_seq(uc: &Connection) -> i64 {
        uc.query_row("SELECT COALESCE(MAX(seq),0)+1 FROM user_word", [], |r| r.get(0))
            .unwrap_or(1)
    }

    /// 全部词组 (key,value,weight),供内存索引构建。
    pub fn all_phrase_rows(&self) -> SqlResult<Vec<Row>> {
        let mut st = self.conn.prepare("SELECT key, value, weight FROM phrase")?;
        let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 全部词组带简拼 (jp,key,value,weight),供混拼内存索引构建(2026-09-02)。
    pub fn all_phrase_rows_with_jp(&self) -> SqlResult<Vec<(String, String, String, i64)>> {
        let mut st = self.conn.prepare("SELECT jp, key, value, weight FROM phrase")?;
        let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 全部单字 (key,value,weight),LENGTH(value)=1。
    pub fn all_single_char_rows(&self) -> SqlResult<Vec<Row>> {
        let mut st = self
            .conn
            .prepare("SELECT key, value, weight FROM pinyin WHERE LENGTH(value)=1")?;
        let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 拼音单字(过滤多字词组)。
    pub fn query_pinyin(&self, key: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self.conn.prepare(
            "SELECT value, weight FROM pinyin WHERE key=?1 AND LENGTH(value)=1 ORDER BY weight DESC LIMIT ?2",
        )?;
        let rows = st.query_map(rusqlite::params![key, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 简拼单字(过滤多字词组)。
    pub fn query_pinyin_jp(&self, jp: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self.conn.prepare(
            "SELECT value, weight FROM pinyin WHERE jp=?1 AND LENGTH(value)=1 ORDER BY weight DESC LIMIT ?2",
        )?;
        let rows = st.query_map(rusqlite::params![jp, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 单字母前缀高权单字桶(2026-09-02):key 以该字母开头的所有音节里,按权重取高权单字。
    /// 对齐外挂内存路径的「前缀节点高权桶」语义(z→在/张/中/这/再…),取代只查 jp='z' 的窄覆盖。
    /// 范围比较 key>=p AND key<next(p) 走索引;过滤 LENGTH(value)=1 防词组混入。
    pub fn query_pinyin_prefix_top(&self, prefix: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self.conn.prepare(
            "SELECT value, weight FROM pinyin WHERE key >= ?1 AND key < ?2 AND LENGTH(value)=1 \
             ORDER BY weight DESC LIMIT ?3",
        )?;
        let rows = st.query_map(
            rusqlite::params![prefix, next_prefix(prefix), limit as i64],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 词组全拼 key 精确。
    pub fn query_phrase(&self, key: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self
            .conn
            .prepare("SELECT value, weight FROM phrase WHERE key=?1 ORDER BY weight DESC LIMIT ?2")?;
        let rows = st.query_map(rusqlite::params![key, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 纯简拼词组:jp 精确(sj→世界/时间)。
    pub fn query_phrase_by_jp(&self, jp: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self
            .conn
            .prepare("SELECT value, weight FROM phrase WHERE jp=?1 ORDER BY weight DESC LIMIT ?2")?;
        let rows = st.query_map(rusqlite::params![jp, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 前全拼+后首字母:key 前缀(shij→世界/实际)。范围比较走 idx_phrase_key。
    pub fn query_phrase_by_key_prefix(&self, prefix: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self.conn.prepare(
            "SELECT value, weight FROM phrase WHERE key >= ?1 AND key < ?2 ORDER BY weight DESC LIMIT ?3",
        )?;
        let rows = st.query_map(
            rusqlite::params![prefix, next_prefix(prefix), limit as i64],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 反向混拼:jp 首字母=first(范围),结果集内过滤 key 后缀 rest(sjie→世界)。
    pub fn query_phrase_reverse_mixed(&self, first: &str, rest: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self.conn.prepare(
            "SELECT value, key, weight FROM phrase WHERE jp >= ?1 AND jp < ?2 ORDER BY weight DESC LIMIT ?3",
        )?;
        let rows = st.query_map(
            rusqlite::params![first, next_prefix(first), (limit * 20) as i64],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?)),
        )?;
        let mut out: Vec<Weighted> = rows
            .filter_map(|x| x.ok())
            .filter(|(_, key, _)| key.ends_with(rest))
            .map(|(val, _, w)| (val, w))
            .collect();
        out.truncate(limit);
        Ok(out)
    }

    /// 五笔候选(预留)。
    pub fn query_wubi(&self, key: &str, limit: usize) -> SqlResult<Vec<Weighted>> {
        let mut st = self
            .conn
            .prepare("SELECT value, weight FROM wubi86 WHERE key=?1 ORDER BY weight DESC LIMIT ?2")?;
        let rows = st.query_map(rusqlite::params![key, limit as i64], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.filter_map(|x| x.ok()).collect())
    }

    /// 学习:写独立 user.db 的 user_word 表(2026-09-03 改,不再碰主库 phrase/pinyin)。
    /// 主库只读后,任何 UPDATE/INSERT 主库都会返错;学习记录统一进 user.db,
    /// 查询时由 engine 合并(学成词置顶)。user_conn 为 None(打开失败)时静默跳过。
    ///
    /// 2026-09-04 学成置顶+位置锁定:
    /// - 新词首次学:weight 固定 = LEARN_PIN_WEIGHT(不再从 1 起累积),seq 取学习次序。
    /// - 已学词再选:**只刷新 seq 不动 weight** — 权重不累积 → 学成词位置永久锁定,
    ///   解决「偶尔用的词位置老变、无法固化调用」(用户 2026-09-04 明确不要时间衰减)。
    /// `weight` 参数保留兼容旧调用(传 1),本函数内部忽略之,统一用 LEARN_PIN_WEIGHT。
    pub fn add_user_word(&self, pinyin: &str, word: &str, _weight: i64) -> SqlResult<()> {
        let Some(uc) = &self.user_conn else {
            return Ok(()); // user.db 不可用:学习失效但不影响打字
        };
        let seq = Self::next_seq(uc);
        uc.execute(
            "INSERT INTO user_word(key,value,weight,seq) VALUES(?1,?2,?3,?4)
             ON CONFLICT(key,value) DO UPDATE SET seq=excluded.seq",
            rusqlite::params![pinyin, word, LEARN_PIN_WEIGHT, seq],
        )?;
        Ok(())
    }

    /// 查学习词:某拼音下用户学过的所有词 → [(value, seq)](按 seq 升序,先学在前)。
    /// 供 engine 查询合并用(学成置顶+位置锁定)。无 user.db 时返空。
    /// 返回的 weight 由 engine 统一给 LEARN_PIN_WEIGHT,这里只带 seq 用于稳定排序。
    pub fn user_words_for(&self, pinyin: &str) -> Vec<UserWord> {
        let Some(uc) = &self.user_conn else {
            return Vec::new();
        };
        let mut st = match uc.prepare("SELECT value, seq FROM user_word WHERE key=?1 ORDER BY seq ASC") {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        let rows = match st.query_map(rusqlite::params![pinyin], |r| Ok((r.get(0)?, r.get(1)?))) {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        rows.filter_map(|x| x.ok()).collect()
    }

    /// 删除指定学习词(词库管理窗口用)。返受影响行数。
    pub fn remove_user_word(&self, pinyin: &str, word: &str) -> SqlResult<usize> {
        let Some(uc) = &self.user_conn else {
            return Ok(0);
        };
        uc.execute(
            "DELETE FROM user_word WHERE key=?1 AND value=?2",
            rusqlite::params![pinyin, word],
        )
    }

    /// 清空全部学习记录(词库管理窗口「清空学习」用)。
    pub fn clear_user_words(&self) -> SqlResult<()> {
        let Some(uc) = &self.user_conn else {
            return Ok(());
        };
        uc.execute("DELETE FROM user_word", [])?;
        Ok(())
    }

    /// 搜索主词库(只读,词库管理窗口「全部词库」查询用,2026-09-04)。
    /// term 全为字母 → 按拼音(phrase.key + pinyin.key)前缀匹配;含非字母 → 按词 value 子串匹配。
    /// 返回 [(key, value, weight, source)],source ∈ phrase/pinyin。主库只读,只能查不能改。
    pub fn search_main_dict(&self, term: &str, limit: usize) -> Vec<(String, String, i64, String)> {
        self.search_main_dict_page(term, limit, 0)
    }

    /// 分页搜索(对齐灵犀 dict_list 2026-09-04):
    ///   - keyword 非空:`WHERE key LIKE %k% OR value LIKE %k%`(拼音/文字一条语句同搜)
    ///   - keyword 空:`ORDER BY weight DESC` 全表 top(初始铺一批高频词)
    ///   - offset 跳过前 N 条(分页)。
    pub fn search_main_dict_page(&self, term: &str, limit: usize, offset: usize) -> Vec<(String, String, i64, String)> {
        let t = term.trim();
        let mut out: Vec<(String, String, i64, String)> = Vec::new();
        if t.is_empty() {
            if let Ok(mut st) = self.conn.prepare(
                "SELECT key, value, weight FROM phrase ORDER BY weight DESC LIMIT ?1 OFFSET ?2",
            ) {
                if let Ok(rows) = st.query_map(rusqlite::params![limit as i64, offset as i64], |r| {
                    Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
                }) {
                    for r in rows.flatten() {
                        out.push((r.0, r.1, r.2, "phrase".to_string()));
                    }
                }
            }
        } else {
            let like = format!("%{}%", t);
            if let Ok(mut st) = self.conn.prepare(
                "SELECT key, value, weight FROM phrase WHERE key LIKE ?1 OR value LIKE ?1 ORDER BY weight DESC LIMIT ?2 OFFSET ?3",
            ) {
                if let Ok(rows) = st.query_map(rusqlite::params![like, limit as i64, offset as i64], |r| {
                    Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?))
                }) {
                    for r in rows.flatten() {
                        out.push((r.0, r.1, r.2, "phrase".to_string()));
                    }
                }
            }
        }
        out.truncate(limit);
        out
    }

    /// 匹配总条数(对齐灵犀 dict_count,分页「共 N 条」用)。空 = 全表总数。
    pub fn search_main_dict_count(&self, term: &str) -> usize {
        let t = term.trim();
        let total: i64 = if t.is_empty() {
            self.conn.query_row("SELECT COUNT(*) FROM phrase", [], |r| r.get::<_, i64>(0)).unwrap_or(0)
        } else {
            let like = format!("%{}%", t);
            self.conn.query_row(
                "SELECT COUNT(*) FROM phrase WHERE key LIKE ?1 OR value LIKE ?1",
                rusqlite::params![like], |r| r.get::<_, i64>(0),
            ).unwrap_or(0)
        };
        total.max(0) as usize
    }

    /// 列出全部学习词(词库管理窗口列表用) → [(key, value, weight, seq)] 按 seq 升序。
    pub fn list_user_words(&self) -> Vec<(String, String, i64, i64)> {
        let Some(uc) = &self.user_conn else {
            return Vec::new();
        };
        let mut st = match uc.prepare("SELECT key, value, weight, seq FROM user_word ORDER BY seq ASC") {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        let rows = match st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))) {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        rows.filter_map(|x| x.ok()).collect()
    }

    // ============================================================
    // 主库可写(2026-09-04,恢复外挂式删/加/改权重,用户拍板)
    //
    // 语义复用 lingxi_ime/backend/database.py:
    //   dict_delete(pinyin,word)        → DELETE FROM phrase WHERE key AND value
    //   dict_upsert(pinyin,word,weight) → 存在改 weight+jp,不存在 INSERT(jp 由音节首字母算)
    //   dict_max_weight(pinyin)         → 同 key 最高词频(新词提频排第一)
    //
    // **架构(多进程安全)**:打字引擎保持只读(切换秒开),主库写**不走 self.conn**,
    // 每次写都独立开一个短时读写连接(开→写→立即 Drop 关),配 busy_timeout 等读锁,
    // 并由 MAIN_WRITE_LOCK 进程内互斥。这样多宿主进程(notepad/explorer)各自只读
    // 不受影响,只有词库管理这一个低频操作短时持写锁。
    //
    // **一致性**:运行时引擎纯 SQLite(mem 索引恒 None,见 engine.rs load_or_build_index),
    // 所以写后主库内容即刻对新查询生效;但同进程 self.conn 是只读旧连接,新建查询
    // 连接才读到最新。失效的 .idx/.midx 缓存索引由 invalidate_main_index_caches 清理。
    // ============================================================

    /// 主库路径(从只读连接的 db 文件名取,供短时写连接用)。
    fn main_db_path(&self) -> SqlResult<String> {
        self.conn
            .query_row("PRAGMA database_list", [], |r| r.get::<_, String>(2))
            .map_err(|e| e)
    }

    /// 开一个短时主库读写连接(busy_timeout 5s 等读锁释放)。调用方须立即用立即 Drop。
    fn open_main_writable(&self) -> SqlResult<Connection> {
        let path = self.main_db_path()?;
        let c = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        c.busy_timeout(std::time::Duration::from_millis(5000));
        Ok(c)
    }

    /// 同 key 下 phrase 最高词频(改权重/新词提频参考)。词库管理 UI 用。
    pub fn dict_max_weight(&self, pinyin: &str) -> i64 {
        self.conn
            .query_row(
                "SELECT MAX(weight) FROM phrase WHERE key=?1",
                rusqlite::params![pinyin],
                |r| r.get(0),
            )
            .ok()
            .flatten()
            .unwrap_or(0)
    }

    /// 删除主库词条(phrase)。返回删除行数。多进程安全(独立短时可写连接+互斥)。
    pub fn main_delete_word(&self, pinyin: &str, word: &str) -> Result<usize, String> {
        let _g = MAIN_WRITE_LOCK.lock().map_err(|e| e.to_string())?;
        let c = self.open_main_writable().map_err(|e| e.to_string())?;
        let n = c
            .execute(
                "DELETE FROM phrase WHERE key=?1 AND value=?2",
                rusqlite::params![pinyin, word],
            )
            .map_err(|e| e.to_string())?;
        drop(c); // 立即关写连接,释放写锁
        self.invalidate_main_index_caches();
        Ok(n)
    }

    /// 主库 upsert 词条(phrase):存在改 weight+jp,不存在插入。
    /// 外挂「改权重」= 对同 (key,value) upsert 新 weight(等效先删旧词再插新权重词)。
    /// 返回 true=成功。
    pub fn main_upsert_word(&self, pinyin: &str, word: &str, weight: i64) -> Result<bool, String> {
        let _g = MAIN_WRITE_LOCK.lock().map_err(|e| e.to_string())?;
        let c = self.open_main_writable().map_err(|e| e.to_string())?;
        let jp = Self::to_jianpin(pinyin);
        let exists: bool = c
            .query_row(
                "SELECT 1 FROM phrase WHERE key=?1 AND value=?2",
                rusqlite::params![pinyin, word],
                |_| Ok(()),
            )
            .is_ok();
        if exists {
            c.execute(
                "UPDATE phrase SET weight=?1, jp=?2 WHERE key=?3 AND value=?4",
                rusqlite::params![weight, jp, pinyin, word],
            )
            .map_err(|e| e.to_string())?;
        } else {
            c.execute(
                "INSERT INTO phrase (jp, key, value, weight) VALUES (?1,?2,?3,?4)",
                rusqlite::params![jp, pinyin, word, weight],
            )
            .map_err(|e| e.to_string())?;
        }
        drop(c);
        self.invalidate_main_index_caches();
        Ok(true)
    }

    /// 写主库后清缓存索引(.idx/.midx):指纹已变,留着只会让别的进程误判或重建。
    /// 运行时纯 SQLite,删掉它们无副作用(下次需要时按需重建,但当前链不用)。
    fn invalidate_main_index_caches(&self) {
        if let Ok(path) = self.main_db_path() {
            for ext in ["idx", "midx"] {
                let p = std::path::Path::new(&path).with_extension(ext);
                let _ = std::fs::remove_file(p);
            }
        }
    }

    /// 拼音转简拼(每音节首字母),对齐 database.py _to_jianpin。
    /// 用标准音节表切分后取各音节首字母;切不出则退化为整串首字母。
    pub fn to_jianpin(pinyin: &str) -> String {
        let sy = crate::syllable::Syllables::new();
        let segs = sy.segment(pinyin);
        if segs.is_empty() {
            return pinyin.chars().next().map(|c| c.to_string()).unwrap_or_default();
        }
        segs.iter().filter_map(|s| s.chars().next()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 建一个空的临时主库(只需存在,user.db 与之同目录派生),返回 (CikuDb, 临时目录)。
    /// 主库不必有 phrase/pinyin 表——学习路径只写 user.db,本测试不查主库。
    /// 目录名用全局原子计数器:同进程各测试独占目录(并发安全),不会互相清/串行残留。
    /// (曾用 temp_dir+pid:同进程多测试共享 → 并发互删 user.db → 间歇 3/4 假败,2026-09-04 修)
    fn temp_db() -> (CikuDb, std::path::PathBuf) {
        use std::sync::atomic::{AtomicU64, Ordering};
        static N: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "prisir_dbtest_{}_{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir); // 清掉同目录历史残留
        std::fs::create_dir_all(&dir).unwrap();
        let main = dir.join("ciku.db");
        // 空主库文件(占位,只读打开用)
        Connection::open(&main).unwrap().execute_batch("CREATE TABLE IF NOT EXISTS phrase(jp TEXT,key TEXT,value TEXT,weight INTEGER);").unwrap();
        let db = CikuDb::open(main.to_str().unwrap()).unwrap();
        (db, dir)
    }

    #[test]
    fn learn_pins_word_at_fixed_weight() {
        let (db, _d) = temp_db();
        db.add_user_word("nihao", "你好", 1).unwrap();
        let words = db.user_words_for("nihao");
        assert_eq!(words.len(), 1);
        assert_eq!(words[0].0, "你好");
        // 学成置顶:固定权重写进库,直接读校验
        let all = db.list_user_words();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].2, LEARN_PIN_WEIGHT, "学成词权重必须=LEARN_PIN_WEIGHT(置顶)");
        assert!(LEARN_PIN_WEIGHT > 26000, "LEARN_PIN_WEIGHT 必须大于词库静态 max");
    }

    #[test]
    fn relearn_does_not_accumulate_weight_position_locked() {
        let (db, _d) = temp_db();
        db.add_user_word("nihao", "你好", 1).unwrap();
        // 重复学习多次:weight 不累积(位置锁定)
        for _ in 0..5 {
            db.add_user_word("nihao", "你好", 1).unwrap();
        }
        let all = db.list_user_words();
        assert_eq!(all.len(), 1, "重复学习不应产生多行");
        assert_eq!(all[0].2, LEARN_PIN_WEIGHT, "重复学习 weight 不累积,保持置顶固定值");
    }

    #[test]
    fn multiple_learned_words_ordered_by_seq() {
        let (db, _d) = temp_db();
        db.add_user_word("ni", "你", 1).unwrap();
        db.add_user_word("ni", "泥", 1).unwrap();
        db.add_user_word("ni", "尼", 1).unwrap();
        let words = db.user_words_for("ni");
        let vals: Vec<&str> = words.iter().map(|(v, _)| v.as_str()).collect();
        assert_eq!(vals, vec!["你", "泥", "尼"], "学成词按学习先后(seq)稳定排序");
    }

    #[test]
    fn remove_and_clear_user_words() {
        let (db, _d) = temp_db();
        db.add_user_word("nihao", "你好", 1).unwrap();
        db.add_user_word("nihao", "泥嚎", 1).unwrap();
        assert_eq!(db.user_words_for("nihao").len(), 2);
        let n = db.remove_user_word("nihao", "泥嚎").unwrap();
        assert_eq!(n, 1);
        let rest = db.user_words_for("nihao");
        assert_eq!(rest.len(), 1);
        assert_eq!(rest[0].0, "你好");
        db.clear_user_words().unwrap();
        assert_eq!(db.user_words_for("nihao").len(), 0, "清空后无学成词");
    }
}
