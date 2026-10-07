'use strict';

/**
 * calibre-metadata-indexer v0.1.0 — Calibre metadata.db 索引(只读)
 *
 * 数据来源:Calibre metadata.db SQLite 库。
 * 默认路径:~/Calibre Library/metadata.db(Windows:C:\Users\<user>\Calibre Library\metadata.db)
 * 可通过 PRISIR_CALIBRE_DB 环境变量覆盖。
 *
 * Calibre 用 WAL journal(metadata.db-wal + metadata.db-shm sidecar),readonly mode 直接
 * 读 SQLite 在 WAL 模式下仍可读,且我们绝不会写。
 *
 * Calibre schema 参考(calibre/library/sqlite.py + manual.calibre-ebook.com/develop/en/db_schema.html):
 *   books(id, title, sort, timestamp, pubdate, series_index, author_sort, isbn, path, ...)
 *   authors(id, name, sort, link)
 *   books_authors_link(book, author)  ← 多对多
 *   data_series(id, name, sort)
 *   books_series_link(book, series)
 *   data_tags(id, name)
 *   books_tags_link(book, tag)
 *
 * 命令(L0 风险,纯只读):
 *   calibre.health        {}                                    → { ok, db_path, alive, books_total, authors_total, last_error }
 *   calibre.books.list    { limit?, offset? }                    → { ok, alive, books: [{id, title, author, timestamp, pubdate, path, has_cover}], total, last_error }
 *   calibre.books.search  { query, limit? }                      → { ok, alive, hits: [{id, title, author, timestamp}], last_error }
 *   calibre.authors.list  { limit? }                            → { ok, alive, authors: [{id, name, book_count}], last_error }
 *   calibre.tags.list     { limit? }                            → { ok, alive, tags: [{id, name, book_count}], last_error }
 *
 * 实现:Node 内置 node:sqlite(无 npm dep)。
 *
 * 设计取舍(沿用 LX 5 步法 + SiYuan SQLite readonly):
 *   - SQLite readonly mode:file:${db_path}?mode=ro,SQLite 拒绝任何 INSERT/UPDATE/DELETE
 *   - LIKE 模糊搜索:title LIKE '%query%' OR author_sort LIKE '%query%'(Calibre 无 FTS5)
 *   - 不缓存搜索结果(每次 invoke 现取,Calibre 加书 / 改书下次 search 立即看到)
 *   - Phase A 范围:**严格只读**
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');

// ── db 路径解析 ────────────────────────────────────────────────
function calibreDbPath() {
  if (process.env.PRISIR_CALIBRE_DB) return process.env.PRISIR_CALIBRE_DB;
  // Windows + Linux + macOS 默认:~/Calibre Library/metadata.db
  const home = process.env.USERPROFILE || process.env.HOME || '';
  return path.join(home, 'Calibre Library', 'metadata.db');
}

// ── node:sqlite 加载 + db 连接(只读)──────────────────────────
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

let db = null;
let dbOpenError = '';
function openDb() {
  if (db !== null) return db;
  const s = tryLoadSqlite();
  if (!s) { dbOpenError = `node:sqlite not available: ${sqliteLoadError}`; db = false; return null; }
  const p = calibreDbPath();
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

// ── health ──────────────────────────────────────────────────────
function probeHealth() {
  const p = calibreDbPath();
  if (!fs.existsSync(p)) {
    return { ok: false, alive: false, db_path: p, last_error: 'db file not found' };
  }
  const d = openDb();
  if (!d) return { ok: false, alive: false, db_path: p, last_error: dbOpenError || sqliteLoadError };
  try {
    const books = d.prepare('SELECT COUNT(*) AS n FROM books').get();
    const authors = d.prepare('SELECT COUNT(*) AS n FROM authors').get();
    return {
      ok: true, alive: true, db_path: p,
      books_total: Number(books.n || 0),
      authors_total: Number(authors.n || 0),
      last_error: '',
    };
  } catch (e) {
    return { ok: false, alive: false, db_path: p, last_error: `query failed: ${e.message}` };
  }
}

// ── books.list ──────────────────────────────────────────────────
function listBooks(limit, offset) {
  const d = openDb();
  if (!d) return fail();
  const lim = Math.max(1, Math.min(Number(limit) || 20, 100));
  const off = Math.max(0, Number(offset) || 0);
  try {
    // JOIN authors(多对多 → 这里取 first author 简化)
    const rows = d.prepare(
      `SELECT b.id, b.title, b.timestamp, b.pubdate, b.path, b.has_cover,
              (SELECT GROUP_CONCAT(a.name, ', ') FROM books_authors_link bal
               JOIN authors a ON a.id = bal.author WHERE bal.book = b.id) AS authors
         FROM books b
         ORDER BY b.timestamp DESC
         LIMIT ? OFFSET ?`
    ).all(lim, off);
    return {
      ok: true, alive: true,
      books: rows.map((r) => ({
        id: Number(r.id),
        title: String(r.title || ''),
        author: String(r.authors || ''),
        timestamp: String(r.timestamp || ''),
        pubdate: String(r.pubdate || ''),
        path: String(r.path || ''),
        has_cover: Number(r.has_cover || 0) === 1,
      })),
      total: rows.length,
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `books.list failed: ${e.message}` });
  }
}

// ── books.search ────────────────────────────────────────────────
function searchBooks(query, limit) {
  const d = openDb();
  if (!d) return fail();
  const q = String(query || '').trim();
  if (!q) return { ok: true, alive: true, hits: [], last_error: 'empty query' };
  const lim = Math.max(1, Math.min(Number(limit) || 20, 100));
  try {
    // LIKE %query% on title OR author_sort(转义 % _ 防 SQL 注入)
    const escaped = q.replace(/[\\%_]/g, (c) => `\\${c}`);
    const rows = d.prepare(
      `SELECT b.id, b.title, b.author_sort,
              (SELECT GROUP_CONCAT(a.name, ', ') FROM books_authors_link bal
               JOIN authors a ON a.id = bal.author WHERE bal.book = b.id) AS authors,
              b.timestamp
         FROM books b
         WHERE b.title LIKE ? ESCAPE '\\' OR b.author_sort LIKE ? ESCAPE '\\'
         ORDER BY b.timestamp DESC
         LIMIT ?`
    ).all(`%${escaped}%`, `%${escaped}%`, lim);
    return {
      ok: true, alive: true,
      hits: rows.map((r) => ({
        id: Number(r.id),
        title: String(r.title || ''),
        author: String(r.authors || ''),
        author_sort: String(r.author_sort || ''),
        timestamp: String(r.timestamp || ''),
      })),
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `books.search failed: ${e.message}` });
  }
}

// ── authors.list ────────────────────────────────────────────────
function listAuthors(limit) {
  const d = openDb();
  if (!d) return fail();
  const lim = Math.max(1, Math.min(Number(limit) || 50, 200));
  try {
    const rows = d.prepare(
      `SELECT a.id, a.name, COUNT(bal.book) AS book_count
         FROM authors a
         LEFT JOIN books_authors_link bal ON bal.author = a.id
         GROUP BY a.id
         ORDER BY book_count DESC, a.name
         LIMIT ?`
    ).all(lim);
    return {
      ok: true, alive: true,
      authors: rows.map((r) => ({
        id: Number(r.id),
        name: String(r.name || ''),
        book_count: Number(r.book_count || 0),
      })),
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `authors.list failed: ${e.message}` });
  }
}

// ── tags.list ──────────────────────────────────────────────────
// Calibre schema: data_tags(id, name) + books_tags_link(book, tag)
function listTags(limit) {
  const d = openDb();
  if (!d) return fail();
  const lim = Math.max(1, Math.min(Number(limit) || 50, 200));
  try {
    // Calibre 实际可能没建 data_tags 表(老版本),先试探
    const probe = d.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='data_tags'").get();
    if (!probe) {
      return { ok: true, alive: true, tags: [], last_error: 'data_tags table not exist(older Calibre)' };
    }
    const rows = d.prepare(
      `SELECT t.id, t.name, COUNT(btl.book) AS book_count
         FROM data_tags t
         LEFT JOIN books_tags_link btl ON btl.tag = t.id
         GROUP BY t.id
         ORDER BY book_count DESC, t.name
         LIMIT ?`
    ).all(lim);
    return {
      ok: true, alive: true,
      tags: rows.map((r) => ({
        id: Number(r.id),
        name: String(r.name || ''),
        book_count: Number(r.book_count || 0),
      })),
      last_error: '',
    };
  } catch (e) {
    return fail({ last_error: `tags.list failed: ${e.message}` });
  }
}

// ── 注册 extension ───────────────────────────────────────────────
const ext = new PrisIrExt({
  id: 'calibre-metadata-indexer',
  name: 'Calibre metadata.db 索引(只读)',
  version: '0.1.0',
});

ext.registerCommand('calibre.health', async () => probeHealth());
ext.registerCommand('calibre.books.list', async (args) => listBooks(args.limit, args.offset));
ext.registerCommand('calibre.books.search', async (args) => searchBooks(args.query, args.limit));
ext.registerCommand('calibre.authors.list', async (args) => listAuthors(args.limit));
ext.registerCommand('calibre.tags.list', async (args) => listTags(args.limit));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ─────────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, listBooks, searchBooks, listAuthors, listTags,
    calibreDbPath, openDb,
  };
}