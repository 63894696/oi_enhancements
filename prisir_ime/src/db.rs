//! 词库访问(rusqlite 读 ciku.db)。逻辑对齐 Python database.py。
//! 表:phrase(jp,key,value,weight) pinyin(jp,key,value,weight) wubi86(key,value,weight)。
//! 注意:pinyin 表上游混入约 31 万词组,查单字必须 LENGTH(value)=1 过滤。

use rusqlite::{Connection, OpenFlags, Result as SqlResult};

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

    /// 打开/创建学习库 user.db,并确保 user_word 表存在。
    fn open_user_db(main_path: &str) -> SqlResult<Connection> {
        let user_path = std::path::Path::new(main_path).with_extension("user.db");
        let uc = Connection::open(user_path)?;
        uc.execute_batch(
            "CREATE TABLE IF NOT EXISTS user_word(
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                weight INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(key, value)
            );",
        )?;
        Ok(uc)
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
    /// 查询时由 engine 合并(常用字靠前)。user_conn 为 None(打开失败)时静默跳过。
    pub fn add_user_word(&self, pinyin: &str, word: &str, weight: i64) -> SqlResult<()> {
        let Some(uc) = &self.user_conn else {
            return Ok(()); // user.db 不可用:学习失效但不影响打字
        };
        uc.execute(
            "INSERT INTO user_word(key,value,weight) VALUES(?1,?2,?3)
             ON CONFLICT(key,value) DO UPDATE SET weight=weight+excluded.weight",
            rusqlite::params![pinyin, word, weight],
        )?;
        Ok(())
    }

    /// 查学习权重:某拼音下用户学过的所有词(key → [(value, weight)])。
    /// 供 engine 查询合并用(常用字靠前)。无 user.db 时返空。
    pub fn user_words_for(&self, pinyin: &str) -> Vec<Weighted> {
        let Some(uc) = &self.user_conn else {
            return Vec::new();
        };
        let mut st = match uc.prepare("SELECT value, weight FROM user_word WHERE key=?1") {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        let rows = match st.query_map(rusqlite::params![pinyin], |r| Ok((r.get(0)?, r.get(1)?))) {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        rows.filter_map(|x| x.ok()).collect()
    }
}
