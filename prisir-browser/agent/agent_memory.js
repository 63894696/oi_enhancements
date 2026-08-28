// Prisir 智能体 — 长期记忆提炼层(本体化红利 P0 第二支柱,2026-08-14)。
//
// 上游:prisir-native-dividend-automation-2026-08-14.md §4 P0(长期记忆);M2 profile 存储。
// 定位:长期记忆 ≠ 再存一份对话全文(M2 threads.json 已是存档)。本层是从对话/行为
//       **提炼出的偏好与上下文摘要**,供喂模型前「最小化注入」——越用越懂你,但不复述历史。
//
// 宿主无关:存储后端由外部注入(本体 = AgentStore profile 文件;测试 = 内存)。
// 红线(贯穿,代码内强制):
//   - 本地提炼、本地存储(profile),不上云(M2 红线延续)。
//   - 用户可见可删:记忆条目全部可枚举/可单删/可清空(数据主权,§6.5 原则2)。
//   - 最小化注入:喂模型前只取 top-K 相关条目,且不含原始对话全文(只存摘要)。
//   - 容量上限:LRU 淘汰,防无限膨胀(对齐 chatstore MAX 规则)。
//   - 敏感不落:提炼时过滤疑似凭证/key/口令模式(防御性,宁可漏记不滥记)。
'use strict';

const MAX_ITEMS = 100;      // 最多记忆条目(LRU 淘汰最旧/最久未用)
const MAX_TEXT_LEN = 200;   // 单条摘要最大字符(强制摘要,不存长文)
const TOP_K_DEFAULT = 5;    // 喂模型默认取前 5 条相关

// 记忆类别(提炼来源)。
const KINDS = ['preference', 'fact', 'context', 'workflow'];
//   preference 偏好(「用中文总结」「喜欢简洁」)
//   fact       事实(「我的项目叫 X」「常用模型 Y」)
//   context    上下文(「最近在调研 Z」「工作目录是 W」)
//   workflow   工作流(「每周一生成周报」)

// ── 敏感模式过滤(防御性:疑似凭证/口令不记忆) ──────────────────────────
const SENSITIVE = /(sk-[A-Za-z0-9]|api[_-]?key|password|passwd|口令|密码|密钥|secret|token|私钥|助记词|seed phrase)/i;
function isSensitive(text) {
  return SENSITIVE.test(String(text || ''));
}

// ── 记忆库(存储后端注入) ────────────────────────────────────────────────
// store: { read() => Promise<obj>, write(obj) => Promise<bool> }
//   obj 结构: { items: { id: {id,kind,text,createdAt,lastUsedAt,useCount} }, order: [id...] }
function makeMemory(store) {
  async function _read() {
    const s = (await store.read()) || {};
    if (!s.items) s.items = {};
    if (!Array.isArray(s.order)) s.order = Object.keys(s.items);
    return s;
  }
  async function _write(s) { return store.write(s); }

  function _uuid() {
    return 'm' + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36);
  }
  function _now() { return new Date().toISOString(); }

  // 新增/更新一条记忆。同 kind+同文本(规范化后)视为同一条 → 更新 lastUsed/useCount。
  async function remember(kind, text) {
    if (KINDS.indexOf(kind) < 0) return { ok: false, error: 'bad_kind' };
    text = String(text || '').trim();
    if (!text) return { ok: false, error: 'empty' };
    if (isSensitive(text)) return { ok: false, error: 'sensitive_blocked' };  // 红线:敏感不记
    if (text.length > MAX_TEXT_LEN) text = text.slice(0, MAX_TEXT_LEN) + '…';

    const s = await _read();
    const norm = kind + '|' + text.toLowerCase().replace(/\s+/g, ' ');
    // 去重:已有同 kind+同文本 → 更新。
    for (const id of s.order) {
      const it = s.items[id];
      if (it && (it.kind + '|' + it.text.toLowerCase().replace(/\s+/g, ' ')) === norm) {
        it.lastUsedAt = _now();
        it.useCount = (it.useCount || 0) + 1;
        _touchOrder(s, id);
        await _write(s);
        return { ok: true, id, merged: true };
      }
    }
    // 新增。
    const id = _uuid();
    s.items[id] = { id, kind, text, createdAt: _now(), lastUsedAt: _now(), useCount: 1 };
    s.order.unshift(id);
    // LRU 淘汰。
    while (s.order.length > MAX_ITEMS) {
      const drop = s.order.pop();
      delete s.items[drop];
    }
    await _write(s);
    return { ok: true, id, merged: false };
  }

  function _touchOrder(s, id) {
    const i = s.order.indexOf(id);
    if (i > 0) { s.order.splice(i, 1); s.order.unshift(id); }
  }

  // 列出全部(用户可见)。按 lastUsedAt 新→旧。
  async function list() {
    const s = await _read();
    return s.order.map((id) => s.items[id]).filter(Boolean)
      .sort((a, b) => (b.lastUsedAt || '').localeCompare(a.lastUsedAt || ''));
  }

  // 检索:喂模型前取 top-K 相关。简单相关 = 查询词命中(kind/文本)+ 近期 + 高频加权。
  async function recall(query, topK) {
    topK = topK || TOP_K_DEFAULT;
    const q = String(query || '').toLowerCase();
    const qWords = q.split(/\s+/).filter((w) => w.length >= 2);
    const all = await list();
    const scored = all.map((it) => {
      let score = (it.useCount || 1);  // 高频加权
      const hay = (it.kind + ' ' + it.text).toLowerCase();
      for (const w of qWords) if (hay.indexOf(w) >= 0) score += 10;  // 命中加权
      // 近期加权(7 天内用过 +5)。
      if (it.lastUsedAt && (Date.now() - new Date(it.lastUsedAt).getTime()) < 7 * 864e5) score += 5;
      return { it, score };
    });
    scored.sort((a, b) => b.score - a.score);
    // 只取有相关性(score>useCount 基线)或 topK 兜底,且不敏感(双保险)。
    return scored.slice(0, topK).map((x) => x.it).filter((it) => !isSensitive(it.text));
  }

  // 删除单条(用户可删)。
  async function forget(id) {
    const s = await _read();
    if (!s.items[id]) return { ok: false };
    delete s.items[id];
    s.order = s.order.filter((x) => x !== id);
    await _write(s);
    return { ok: true };
  }

  // 清空(用户一键清除所有记忆)。
  async function clear() {
    const s = await _read();
    const n = s.order.length;
    await _write({ items: {}, order: [] });
    return { ok: true, cleared: n };
  }

  // 喂模型的最小化注入文本(top-K 摘要,无全文)。
  async function contextSnippet(query, topK) {
    const items = await recall(query, topK);
    if (!items.length) return '';
    const lines = items.map((it) => '- [' + it.kind + '] ' + it.text);
    return '用户长期记忆(摘要):\n' + lines.join('\n');
  }

  return { remember, list, recall, forget, clear, contextSnippet, MAX_ITEMS, KINDS };
}

const MEM = { makeMemory, isSensitive, KINDS, MAX_ITEMS, MAX_TEXT_LEN, TOP_K_DEFAULT };
if (typeof module !== 'undefined' && module.exports) module.exports = MEM;
if (typeof self !== 'undefined') self.CT_AGENT_MEMORY = MEM;
