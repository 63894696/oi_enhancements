//! 全内存 Trie 前缀索引(移植 trie_index.py MemoryIndex)。
//! 启动时把 phrase/pinyin 全量灌进来,每个节点保留权重 top-N 结果,
//! 前缀查询 = 走到节点直接拿缓存,O(前缀长),避免每次 SQL。

use std::collections::HashMap;

const NODE_TOP: usize = 12; // 每节点缓存的 top 结果数(对齐 Python)

#[derive(Default)]
struct Node {
    children: HashMap<u8, Node>,
    // (value, weight),按 weight 降序保留 top NODE_TOP
    top: Vec<(String, i64)>,
}

pub struct MemoryIndex {
    root: Node,
}

impl MemoryIndex {
    pub fn new() -> Self {
        MemoryIndex { root: Node::default() }
    }

    /// 插入一条 (key,value,weight);沿路径每个节点更新 top 缓存。
    pub fn insert(&mut self, key: &str, value: &str, weight: i64) {
        let mut node = &mut self.root;
        for &b in key.as_bytes() {
            node = node.children.entry(b).or_default();
            insert_top(&mut node.top, value, weight);
        }
    }

    /// 前缀查询:返回该前缀下 top 结果(权重降序)。
    pub fn query_prefix(&self, prefix: &str) -> Vec<(String, i64)> {
        let mut node = &self.root;
        for &b in prefix.as_bytes() {
            match node.children.get(&b) {
                Some(n) => node = n,
                None => return Vec::new(),
            }
        }
        node.top.clone()
    }
}

/// 往 top 列表插一条并保持按 weight 降序、去重 value、截断到 NODE_TOP。
fn insert_top(top: &mut Vec<(String, i64)>, value: &str, weight: i64) {
    // 去重:同 value 保留更高权重
    if let Some(existing) = top.iter_mut().find(|(v, _)| v == value) {
        if weight > existing.1 {
            existing.1 = weight;
        }
    } else {
        top.push((value.to_string(), weight));
    }
    top.sort_by(|a, b| b.1.cmp(&a.1));
    top.truncate(NODE_TOP);
}
