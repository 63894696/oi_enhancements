//! 候选词引擎(移植 engine.py):切分 → 单字+词组+混拼合并排序 → DP 整句智能首选。
//! 逻辑同源 Python,词库经 db.rs 读 ciku.db,前缀查询走 trie.rs 内存索引。

use crate::db::CikuDb;
use crate::syllable::Syllables;
use crate::trie::MemoryIndex;
use std::collections::HashMap;

/// (候选词, 权重)
pub type Candidate = (String, i64);

pub struct ImeEngine {
    pub db: CikuDb,
    pub syll: Syllables,
    pub mem: Option<MemoryIndex>,
    /// 模糊音规则集(对齐 ime_config.FUZZY_RULES)
    pub fuzzy_rules: Vec<&'static str>,
}

/// 模糊音映射:规则名 -> [(源,目标)](对齐 Python _FUZZY_MAP)
fn fuzzy_map(rule: &str) -> &'static [(&'static str, &'static str)] {
    match rule {
        "z_zh" => &[("zh", "z"), ("z", "zh")],
        "c_ch" => &[("ch", "c"), ("c", "ch")],
        "s_sh" => &[("sh", "s"), ("s", "sh")],
        "an_ang" => &[("an", "ang"), ("ang", "an")],
        "en_eng" => &[("en", "eng"), ("eng", "en")],
        "in_ing" => &[("in", "ing"), ("ing", "in")],
        "ian_iang" => &[("ian", "iang"), ("iang", "ian")],
        "uan_uang" => &[("uan", "uang"), ("uang", "uan")],
        "ai_an" => &[("ai", "an"), ("an", "ai")],
        "un_ong" => &[("un", "ong"), ("ong", "un")],
        _ => &[],
    }
}
/// 声母规则(起始替换);其余按结尾替换
fn fuzzy_is_initial(rule: &str) -> bool {
    matches!(rule, "z_zh" | "c_ch" | "s_sh")
}

/// 简拼 = 每个音节首字母拼接(供 db.add_user_word 用)
pub fn to_jianpin(pinyin: &str) -> String {
    // pinyin 形如 "nihao" / "zhongguo";按音节切分取首字母
    let syll = Syllables::new();
    let segs = syll.segment(pinyin);
    if segs.is_empty() {
        // 兜底:空格/逐字符
        return pinyin.chars().filter(|c| c.is_ascii_alphabetic()).take(1).collect();
    }
    segs.iter().filter_map(|s| s.chars().next()).collect()
}

impl ImeEngine {
    pub fn new(db_path: &str) -> Result<Self, String> {
        let db = CikuDb::open(db_path).map_err(|e| format!("open db: {e}"))?;
        Ok(ImeEngine {
            db,
            syll: Syllables::new(),
            mem: None,
            fuzzy_rules: Vec::new(),
        })
    }

    /// 构建内存索引(一次性灌 phrase+pinyin 全量)。失败保留 None 走 SQLite。
    pub fn build_memory_index(&mut self) -> Result<(), String> {
        let phrase = self.db.all_phrase_rows().map_err(|e| format!("phrase: {e}"))?;
        let pinyin = self.db.all_single_char_rows().map_err(|e| format!("pinyin: {e}"))?;
        let mut mem = MemoryIndex::new();
        for (key, value, weight) in &phrase {
            mem.insert(key, value, *weight);
        }
        for (key, value, weight) in &pinyin {
            mem.insert(key, value, *weight);
        }
        self.mem = Some(mem);
        Ok(())
    }

    pub fn set_fuzzy_rules(&mut self, rules: Vec<&'static str>) {
        self.fuzzy_rules = rules;
    }

    pub fn segment(&self, input: &str) -> Vec<String> {
        self.syll.segment(input)
    }

    fn is_full_pinyin(&self, s: &str) -> bool {
        self.syll.is_full_pinyin(s)
    }

    /// 拼音候选查询(对齐 Python _query_pinyin)
    pub fn query(&self, input: &str) -> Vec<Candidate> {
        let n_syll = self.segment(input).len().max(1);
        let mut seen: HashMap<String, i64> = HashMap::new();

        // 模糊音扩展(仅单音节单字查询)
        let fuzzy_inputs = if n_syll == 1 { self.fuzzy_expand(input) } else { vec![input.to_string()] };

        if let Some(mem) = &self.mem {
            for fp in &fuzzy_inputs {
                for (word, w) in mem.query_prefix(fp) {
                    // 词组错配过滤:多字词字数须等于音节数
                    let wlen = word.chars().count();
                    if wlen > 1 && wlen != n_syll {
                        continue;
                    }
                    upsert(&mut seen, word, w);
                }
            }
        } else {
            for fp in &fuzzy_inputs {
                if let Ok(rows) = self.db.query_pinyin(fp, 50) {
                    for (word, w) in rows {
                        upsert(&mut seen, word, w);
                    }
                }
            }
            if let Ok(rows) = self.db.query_phrase(input, 50) {
                for (word, w) in rows {
                    if word.chars().count() != n_syll {
                        continue;
                    }
                    upsert(&mut seen, word, w);
                }
            }
        }

        // 混拼词组(正反向)
        for (word, w) in self.query_phrase_mixed(input) {
            upsert(&mut seen, word, w);
        }

        if !seen.is_empty() {
            // 多字真整词优先于单字,同权重整词靠前
            let mut merged: Vec<Candidate> = seen.into_iter().collect();
            merged.sort_by(|a, b| {
                let a_multi = a.0.chars().count() > 1;
                let b_multi = b.0.chars().count() > 1;
                b_multi.cmp(&a_multi).then(b.1.cmp(&a.1))
            });
            merged.truncate(50);
            return merged;
        }

        // 单字母:首字母简拼带高频字
        if input.chars().count() == 1 {
            return self.db.query_pinyin_jp(input, 50).unwrap_or_default();
        }
        // 多字母无整匹配:取首音节单字
        let segs = self.segment(input);
        if !segs.is_empty() {
            return self.db.query_pinyin(&segs[0], 50).unwrap_or_default();
        }
        Vec::new()
    }

    /// 模糊音扩展(对齐 Python _fuzzy_expand),原音在前。
    fn fuzzy_expand(&self, inp: &str) -> Vec<String> {
        let valid = |cand: &str| {
            let segs = self.segment(cand);
            segs.len() == 1 && segs[0] == cand
        };
        let mut results: Vec<String> = vec![inp.to_string()];
        for rule in self.fuzzy_rules.clone() {
            for (src, dst) in fuzzy_map(rule) {
                if fuzzy_is_initial(rule) {
                    if let Some(rest) = inp.strip_prefix(*src) {
                        let cand = format!("{dst}{rest}");
                        if valid(&cand) && !results.contains(&cand) {
                            results.push(cand);
                        }
                    }
                } else if let Some(head) = inp.strip_suffix(*src) {
                    let cand = format!("{head}{dst}");
                    if valid(&cand) && !results.contains(&cand) {
                        results.push(cand);
                    }
                }
            }
        }
        results
    }

    /// 混拼词组查询(正反向),对齐 Python _query_phrase_mixed。
    fn query_phrase_mixed(&self, inp: &str) -> Vec<Candidate> {
        if inp.len() < 2 {
            return Vec::new();
        }
        let segs = self.segment(inp);
        if self.is_full_pinyin(inp) && segs.len() == 1 {
            return Vec::new();
        }
        let mut seen: HashMap<String, i64> = HashMap::new();
        let mut add = |rows: Vec<Candidate>| {
            for (val, w) in rows {
                if val.chars().count() < 2 {
                    continue;
                }
                upsert(&mut seen, val, w);
            }
        };
        if !self.is_full_pinyin(inp) {
            add(self.db.query_phrase_by_jp(inp, 20).unwrap_or_default());
            let rest = &inp[1..];
            if self.is_full_pinyin(rest) {
                let first = inp.chars().next().unwrap().to_string();
                add(self.db.query_phrase_reverse_mixed(&first, rest, 15).unwrap_or_default());
            }
        }
        add(self.db.query_phrase_by_key_prefix(inp, 15).unwrap_or_default());
        let mut out: Vec<Candidate> = seen.into_iter().collect();
        out.sort_by(|a, b| b.1.cmp(&a.1));
        out.truncate(15);
        out
    }

    /// 整句智能首选(DP/Viterbi),对齐 Python smart_sentence。
    pub fn smart_sentence(&self, input: &str) -> Option<String> {
        let segs = self.segment(input);
        let n = segs.len();
        if n < 2 {
            return None;
        }

        let best_word = |pseq: &[String]| -> Option<Candidate> {
            let key = pseq.concat();
            if let Ok(rows) = self.db.query_phrase(&key, 1) {
                if let Some(r) = rows.into_iter().next() {
                    return Some(r);
                }
            }
            if pseq.len() == 1 {
                if let Ok(rows) = self.db.query_pinyin(&key, 5) {
                    let singles: Vec<Candidate> =
                        rows.into_iter().filter(|(w, _)| w.chars().count() == 1).collect();
                    if let Some(r) = singles.into_iter().next() {
                        return Some(r);
                    }
                }
            }
            None
        };

        // 整句命中完整整词优先(如 shurufa->输入法)
        if let Some(full) = best_word(&segs) {
            if full.0.chars().count() == n {
                return Some(full.0);
            }
        }

        let neg = f64::NEG_INFINITY;
        let mut dp: Vec<(f64, Vec<String>)> = vec![(neg, Vec::new()); n + 1];
        dp[0] = (0.0, Vec::new());
        for i in 1..=n {
            let start = if i >= 4 { i - 4 } else { 0 };
            for j in start..i {
                let pseq = &segs[j..i];
                let bw = best_word(pseq);
                let bw = match bw {
                    Some(x) if dp[j].0 != neg => x,
                    _ => continue,
                };
                let (word, weight) = bw;
                let bonus = if pseq.len() >= 2 { 20.0 } else { 0.0 };
                let score = dp[j].0 + (weight.max(1) as f64).ln() + bonus;
                if score > dp[i].0 {
                    let mut path = dp[j].1.clone();
                    path.push(word);
                    dp[i] = (score, path);
                }
            }
        }
        if dp[n].0 == neg || dp[n].1.is_empty() {
            return None;
        }
        Some(dp[n].1.concat())
    }

    /// 学习用户选择(更新词频)
    pub fn learn(&self, input: &str, selected: &str) {
        if input.is_empty() || selected.is_empty() {
            return;
        }
        let _ = self.db.add_user_word(input, selected, 1);
    }
}

fn upsert(map: &mut HashMap<String, i64>, word: String, w: i64) {
    match map.get(&word) {
        Some(&existing) if existing >= w => {}
        _ => {
            map.insert(word, w);
        }
    }
}
