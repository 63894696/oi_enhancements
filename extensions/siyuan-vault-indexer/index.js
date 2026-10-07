'use strict';

/**
 * siyuan-vault-indexer v0.1.0 — SiYuan vault 索引(只读)
 *
 * 数据来源:SiYuan 的 SQLite 索引库(siyuan.db)。
 * 默认路径:~/Documents/SiYuan/<workspace>/temp/siyuan.db(Windows:%USERPROFILE%\Documents\SiYuan)
 * 可通过 PRISIR_SIYUAN_DB 环境变量覆盖。
 *
 * SiYuan 启动时 temp/siyuan.db 从 .sy JSON 重建,所以读 db 不需要担心 sync 锁冲突。
 * 我们只做 SELECT,SQLite 用 readonly mode 打开(`file:...?mode=ro`),100% 本地 + 0 上传。
 *
 * SiYuan blocks 表 schema(参考 kernel/sql/database.go master 分支):
 *   id, parent_id, root_id, hash, box, path, hpath, name, alias, memo,
 *   tag, content, fcontent, markdown, length, type, subtype, ial,
 *   sort, created, updated
 * blocks_fts5 是 FTS5 virtual table,tokenize="siyuan",UNINDEXED id/parent_id/root_id/hash
 *
 * 命令(L0 风险,纯只读):
 *   siyuan.health      {}                              → { ok, db_path, alive, blocks_total, last_error }
 *   siyuan.notebooks   {}                              → { ok, alive, notebooks: [{box, doc_count, hpath}], last_error }
 *   siyuan.search      { query, limit? }               → { ok, alive, hits: [{id, root_id, hpath, type, content, snippet}], last_error }
 *   siyuan.block.get   { id }                          → { ok, alive, block: {id, type, subtype, markdown, content, tag, hpath, created, updated}, last_error }
 *
 * 实现:Node 内置 better-sqlite3? NO — 不加依赖。用 sql.js / sqlite3 / 自写?
 * 这里用 Node 22+ 内置 `node:sqlite` (node:sqlite) — Node 22.5+ 已 stable,可直接用。
 * 兼容性:要求 Node ≥ 22.5。如果用户 Node 太老,fallback 抛 { ok: false, last_error: 'node:sqlite requires Node 22.5+' }。
 *
 * 设计取舍(沿用 LX 5 步法):
 *   - 失败语义优先:不抛异常,返 { ok: false, alive: false, last_error: ... }
 *   - 只读模式打开 db:`file:${db_path}?mode=ro`,SQLite 拒绝任何 INSERT/UPDATE/DELETE
 *   - 不缓存搜索结果(每次 invoke 现取,SiYuan 实时更新 block 时下一次 search 立即看到)
 *   - FTS5 query 转义:用户输入的 query 里有 " ' 等特殊字符时,转义防 SQL 注入
 *   - Phase A 范围:**严格只读**,Phase B/C 待用户拍板(写 block 才考虑)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');

// ── db 路径解析 ──────────────────────────────────────────────────
function siyuanDbPath() {
  // 优先级:env PRISIR_SIYUAN_DB > 平台默认
  if (process.env.PRISIR_SIYUAN_DB) return process.env.PRISIR_SIYUAN_DB;
  // Windows: %USERPROFILE%\Documents\SiYuan\<workspace>\temp\siyuan.db
  // Linux/macOS: ~/Documents/SiYuan/<workspace>/temp/siyuan.db
  const docs = process.env.USERPROFILE
    ? path.join(process.env.USERPROFILE, 'Documents')
    : path.join(process.env.HOME || '', 'Documents');
  // 找第一个含 temp/siyuan.db 的 workspace(SiYuan 支持多 workspace,先扫一遍)
  const root = path.join(docs, 'SiYuan');
  if (!fs.existsSync(root)) return path.join(root, 'default', 'temp', 'siyuan.db');
  let found = null;
  try {
    for (const ws of fs.readdirSync(root)) {
      const cand = path.join(root, ws, 'temp', 'siyuan.db');
      if (fs.existsSync(cand)) { found = cand; break; }
    }
  } catch {}
  return found || path.join(root, 'default', 'temp', 'siyuan.db');
}

// ── node:sqlite 加载 ────────────────────────────────────────────
let sqlite = null;
let sqliteLoadError = '';
function tryLoadSqlite() {
  if (sqlite !== null) return sqlite;
  try {
    sqlite = require('node:sqlite');
    return sqlite;
  } catch (e) {
    sqliteLoadError = e.message;
    return null;
  }
}

// ── db 连接(只读)───────────────────────────────────────────────
let db = null;
let dbOpenError = '';
function openDb() {
  if (db !== null) return db;
  const s = tryLoadSqlite();
  if (!s) { dbOpenError = `node:sqlite not available: ${sqliteLoadError}`; db = false; return null; }
  const p = siyuanDbPath();
  if (!fs.existsSync(p)) { dbOpenError = `db not found: ${p}`; db = false; return null; }
  try {
    db = new s.DatabaseSync(`file:${p}?mode=ro`, { readOnly: true });
    return db;
  } catch (e) {
    dbOpenError = `open failed: ${e.message}`;
    db = false;
    return null;
  }
}

// ── 通用失败返回 ────────────────────────────────────────────────
function fail(extra = {}) {
  return { ok: false, alive: false, last_error: dbOpenError || sqliteLoadError || 'unknown', ...extra };
}

// ── health:db 是否可读 + blocks 总数 ────────────────────────────
function probeHealth() {
  const p = siyuanDbPath();
  if (!fs.existsSync(p)) {
    return { ok: false, alive: false, db_path: p, last_error: 'db file not found' };
  }
  const d = openDb();
  if (!d) return { ok: false, alive: false, db_path: p, last_error: dbOpenError || sqliteLoadError };
  try {
    const row = d.prepare('SELECT COUNT(*) AS n FROM blocks').get();
    return {
      ok: true, alive: true, db_path: p,
      blocks_total: Number(row.n || 0),
      node_sqlite_available: true,
      last_error: '',
    };
  } catch (e) {
    return { ok: false, alive: false, db_path: p, last_error: `query failed: ${e.message}` };
  }
}

// ── notebooks:DISTINCT box 列所有 notebook,带 doc 数 ────────────
function listNotebooks() {
  const d = openDb();
  if (!d) return fail();
  try {
    // SiYuan blocks 表里 type='d' 是 document(顶级 doc block),不同 box 是不同 notebook
    const rows = d.prepare(
      "SELECT box, COUNT(*) AS doc_count, MIN(hpath) AS sample_hpath FROM blocks WHERE type='d' GROUP BY box"
    ).all();
    return {
      ok: true, alive: true,
      notebooks: rows.map((r) => ({
        box: String(r.box || ''),
        doc_count: Number(r.doc_count || 0),
        sample_hpath: String(r.sample_hpath || ''),
      })),
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `notebooks query failed: ${e.message}` });
  }
}

// ── search:FTS5 全文搜 ─────────────────────────────────────────
// 转义 query:把 " 和 ' 替换成双字符,避免 FTS5 解析错误
function escapeFts(q) {
  return String(q || '').replace(/"/g, '""');
}

function searchBlocks(query, limit) {
  const d = openDb();
  if (!d) return fail();
  const q = escapeFts(query);
  if (!q.trim()) {
    return { ok: true, alive: true, hits: [], last_error: 'empty query' };
  }
  const lim = Math.max(1, Math.min(Number(limit) || 20, 100));
  try {
    // FTS5 MATCH 用双引号包裹 phrase;snippet() 返回命中上下文
    const rows = d.prepare(
      `SELECT b.id, b.root_id, b.hpath, b.type, b.subtype, b.tag,
              snippet(blocks_fts5, 11, '<<', '>>', '...', 16) AS snippet
       FROM blocks_fts5
       JOIN blocks b ON b.id = blocks_fts5.id
       WHERE blocks_fts5 MATCH ?
       ORDER BY rank
       LIMIT ?`
    ).all(`"${q}"`, lim);
    return {
      ok: true, alive: true,
      hits: rows.map((r) => ({
        id: String(r.id || ''),
        root_id: String(r.root_id || ''),
        hpath: String(r.hpath || ''),
        type: String(r.type || ''),
        subtype: String(r.subtype || ''),
        tag: String(r.tag || ''),
        snippet: String(r.snippet || ''),
      })),
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `search failed: ${e.message}` });
  }
}

// ── block.get:按 id 拿 block ──────────────────────────────────
function getBlock(id) {
  const d = openDb();
  if (!d) return fail();
  const bid = String(id || '').trim();
  if (!bid) return { ok: false, alive: true, last_error: 'empty id' };
  try {
    const row = d.prepare(
      'SELECT id, parent_id, root_id, box, path, hpath, name, alias, memo, tag, content, fcontent, markdown, type, subtype, ial, created, updated FROM blocks WHERE id = ?'
    ).get(bid);
    if (!row) return { ok: true, alive: true, block: null, last_error: '' };
    return {
      ok: true, alive: true,
      block: {
        id: String(row.id),
        root_id: String(row.root_id || ''),
        box: String(row.box || ''),
        hpath: String(row.hpath || ''),
        type: String(row.type || ''),
        subtype: String(row.subtype || ''),
        name: String(row.name || ''),
        tag: String(row.tag || ''),
        markdown: String(row.markdown || ''),
        content: String(row.content || ''),
        fcontent: String(row.fcontent || ''),
        created: String(row.created || ''),
        updated: String(row.updated || ''),
      },
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `block.get failed: ${e.message}` });
  }
}

// ── 注册 extension ───────────────────────────────────────────────
const ext = new PrisIrExt({
  id: 'siyuan-vault-indexer',
  name: '思源笔记 vault 索引(只读)',
  version: '0.1.0',
});

ext.registerCommand('siyuan.health', async () => probeHealth());
ext.registerCommand('siyuan.notebooks', async () => listNotebooks());
ext.registerCommand('siyuan.search', async (args) => searchBlocks(args.query, args.limit));
ext.registerCommand('siyuan.block.get', async (args) => getBlock(args.id));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ─────────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, listNotebooks, searchBlocks, getBlock,
    siyuanDbPath, escapeFts, openDb,
  };
}
