/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测
 *
 * 跑法:
 *   node extensions/calibre-metadata-indexer/__tests__/run.js
 *   node extensions/calibre-metadata-indexer/__tests__/run.js --e2e   # 加 fixture db 端到端
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const vm = require('vm');
const sqlite = require('node:sqlite');

const EXT_DIR = path.resolve(__dirname, '..');
const INDEX_JS = path.join(EXT_DIR, 'index.js');
const SRC = fs.readFileSync(INDEX_JS, 'utf8');
const E2E = process.argv.includes('--e2e');

// ── fixture Calibre metadata.db 构造器 ────────────────────────────
// 按 calibre/library/sqlite.py + manual.calibre-ebook.com/develop/en/db_schema.html 复刻
function buildFixtureDb(dbPath) {
  if (fs.existsSync(dbPath)) fs.unlinkSync(dbPath);
  const d = new sqlite.DatabaseSync(dbPath);
  d.exec(`
    CREATE TABLE books (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL DEFAULT 'Unknown',
      sort TEXT COLLATE NOCASE,
      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      pubdate TIMESTAMP DEFAULT '0101-01-01 00:00:00+00:00',
      series_index REAL NOT NULL DEFAULT 1.0,
      author_sort TEXT COLLATE NOCASE,
      isbn TEXT DEFAULT '',
      path TEXT NOT NULL DEFAULT '',
      has_cover BOOL DEFAULT 0
    );
    CREATE TABLE authors (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL COLLATE NOCASE,
      sort TEXT NOT NULL COLLATE NOCASE DEFAULT '',
      link TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE books_authors_link (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book INTEGER NOT NULL,
      author INTEGER NOT NULL,
      UNIQUE(book, author)
    );
    CREATE TABLE data_tags (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL COLLATE NOCASE,
      sort TEXT COLLATE NOCASE
    );
    CREATE TABLE books_tags_link (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book INTEGER NOT NULL,
      tag INTEGER NOT NULL,
      UNIQUE(book, tag)
    );
  `);
  const insB = d.prepare(`INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  const insA = d.prepare(`INSERT INTO authors VALUES (NULL, ?, ?, '')`);
  const insBA = d.prepare(`INSERT INTO books_authors_link (book, author) VALUES (?, ?)`);
  const insT = d.prepare(`INSERT INTO data_tags VALUES (?, ?, ?)`);
  const insBT = d.prepare(`INSERT INTO books_tags_link (book, tag) VALUES (?, ?)`);

  // 4 author
  const authors = [
    ['刘慈欣', 'Liu Cixin'],
    ['三体', 'San Ti'],
    ['余华', 'Yu Hua'],
    ['George Orwell', 'George Orwell'],
  ];
  const authorIds = {};
  for (const [name, sort] of authors) {
    const r = insA.run(name, sort);
    authorIds[name] = Number(r.lastInsertRowid);
  }

  // 6 books
  const books = [
    [1, '三体',          '2020-01-15 10:00:00+00:00', '2008-01-01',    1.0, 'Liu Cixin, San Ti',     '刘慈欣/三体 (1)/三体 - 三体.epub', 1],
    [2, '三体 2 : 黑暗森林', '2020-02-20 12:00:00+00:00', '2008-05-01',    2.0, 'Liu Cixin',            '刘慈欣/三体 2 (2)/三体 2 - 黑暗森林.epub', 1],
    [3, '三体 3 : 死神永生', '2020-03-25 14:00:00+00:00', '2010-12-01',    3.0, 'Liu Cixin',            '刘慈欣/三体 3 (3)/三体 3 - 死神永生.epub', 1],
    [4, '活着',           '2021-04-15 09:00:00+00:00', '1993-01-01',    1.0, 'Yu Hua',               '余华/活着 (4)/活着 - 余华.epub', 1],
    [5, '许三观卖血记',     '2021-06-20 11:00:00+00:00', '1995-01-01',    1.0, 'Yu Hua',               '余华/许三观卖血记 (5)/许三观卖血记 - 余华.epub', 0],
    [6, '1984',          '2022-01-10 08:00:00+00:00', '1949-06-08',    1.0, 'George Orwell',        'George Orwell/1984 (6)/1984 - George Orwell.epub', 1],
  ];
  const bookIds = [];
  for (const [id, title, ts, pd, si, authorSort, pathStr, hasCover] of books) {
    insB.run(id, title, title, ts, pd, si, authorSort, '', pathStr, hasCover);
    bookIds.push(id);
  }

  // books_authors_link
  insBA.run(1, authorIds['刘慈欣']);
  insBA.run(1, authorIds['三体']);
  insBA.run(2, authorIds['刘慈欣']);
  insBA.run(3, authorIds['刘慈欣']);
  insBA.run(4, authorIds['余华']);
  insBA.run(5, authorIds['余华']);
  insBA.run(6, authorIds['George Orwell']);

  // 5 tags
  const tags = [['科幻', 1], ['硬科幻', 2], ['中国文学', 3], ['反乌托邦', 4], ['长篇', 6]];
  const tagIds = {};
  for (const [name, sort] of tags) {
    const r = insT.run(null, name, name);
    tagIds[name] = Number(r.lastInsertRowid);
  }
  insBT.run(1, tagIds['科幻']);
  insBT.run(1, tagIds['硬科幻']);
  insBT.run(2, tagIds['科幻']);
  insBT.run(3, tagIds['科幻']);
  insBT.run(4, tagIds['中国文学']);
  insBT.run(6, tagIds['反乌托邦']);
  insBT.run(6, tagIds['长篇']);

  d.close();
  return dbPath;
}

// ── sandbox 加载 index.js ─────────────────────────────────────────
function loadModule(extraEnv = {}) {
  const captured = { exports: {} };
  const sdkStub = {
    PrisIrExt: class {
      constructor() {}
      registerCommand() {}
      registerPanel() {}
      async start() {}
    },
  };
  const requireFn = (id) => {
    if (id === '@prisir/extension-sdk') return sdkStub;
    try { return require(id); } catch (e) { throw e; }
  };
  const sandbox = {
    module: captured,
    exports: captured.exports,
    require: requireFn,
    console,
    process: { env: { ...process.env, ...extraEnv } },
    Buffer,
    setTimeout,
    clearTimeout,
    setImmediate,
    clearImmediate,
    fs,
    path,
  };
  sandbox.global = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return captured.exports;
}

// ── 极简 assert ──────────────────────────────────────────────────
let pass = 0, fail = 0;
const failures = [];
function t(name, fn) {
  try { fn(); pass++; console.log(`  ✓ ${name}`); }
  catch (e) { fail++; failures.push({ name, err: e }); console.log(`  ✗ ${name}: ${e.message}`); }
}
async function ta(name, fn) {
  try { await fn(); pass++; console.log(`  ✓ ${name}`); }
  catch (e) { fail++; failures.push({ name, err: e }); console.log(`  ✗ ${name}: ${e.message}`); }
}
function assertEq(a, b, msg = '') {
  const A = JSON.stringify(a), B = JSON.stringify(b);
  if (A !== B) throw new Error(`${msg}\n  expected: ${B}\n  actual:   ${A}`);
}
function assertTrue(v, msg = '') { if (!v) throw new Error(msg || 'expected truthy'); }

(async () => {
  console.log('\n[Phase A 只读扩展单测] calibre-metadata-indexer\n');

  // 1. calibreDbPath env 覆盖
  t('calibreDbPath env 覆盖默认', () => {
    const m1 = loadModule({});
    const def = m1.calibreDbPath();
    assertTrue(def.endsWith('metadata.db'), `default should end with metadata.db, got ${def}`);
    const m2 = loadModule({ PRISIR_CALIBRE_DB: '/tmp/custom.db' });
    assertEq(m2.calibreDbPath(), '/tmp/custom.db');
  });

  // 2. probeHealth db 不存在 → ok=false
  t('probeHealth db 不存在 → ok=false', () => {
    const m = loadModule({ PRISIR_CALIBRE_DB: '/tmp/does-not-exist-' + Date.now() + '.db' });
    const r = m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 3. listBooks 无 db → ok=false
  t('listBooks 无 db → ok=false + alive=false', () => {
    const m = loadModule({ PRISIR_CALIBRE_DB: '/tmp/none-' + Date.now() + '.db' });
    const r = m.listBooks(20, 0);
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 4. searchBooks 空 query → hits=[]
  t('searchBooks 空 query → ok + hits=[]', () => {
    const dbPath = path.join(os.tmpdir(), `calibre-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_CALIBRE_DB: dbPath });
    const r = m.searchBooks('', 10);
    assertEq(r.ok, true);
    assertEq(r.hits, []);
    try { fs.unlinkSync(dbPath); } catch {}
  });

  // 5. searchBooks LIKE 转义 (% _ 防注入)
  t('searchBooks LIKE 转义 % _ 不破 SQL', () => {
    const dbPath = path.join(os.tmpdir(), `calibre-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_CALIBRE_DB: dbPath });
    // 注入:50% off 这种查询不应爆 SQL
    const r = m.searchBooks('50%', 10);
    assertEq(r.ok, true);
    const r2 = m.searchBooks('a_b', 10);
    assertEq(r2.ok, true);
    try { fs.unlinkSync(dbPath); } catch {}
  });

  // 6. listBooks limit clamp [1, 100]
  t('listBooks limit clamp [1, 100]', () => {
    const dbPath = path.join(os.tmpdir(), `calibre-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_CALIBRE_DB: dbPath });
    const r = m.listBooks(99999, 0);
    assertEq(r.ok, true);
    assertTrue(r.books.length <= 100, `books.length > 100: ${r.books.length}`);
    try { fs.unlinkSync(dbPath); } catch {}
  });

  // 7. listAuthors 无 db → ok=false
  t('listAuthors 无 db → ok=false', () => {
    const m = loadModule({ PRISIR_CALIBRE_DB: '/tmp/none-' + Date.now() + '.db' });
    const r = m.listAuthors(50);
    assertEq(r.ok, false);
    assertEq(r.alive, false);
  });

  // 8. listTags 无 db → ok=false
  t('listTags 无 db → ok=false', () => {
    const m = loadModule({ PRISIR_CALIBRE_DB: '/tmp/none-' + Date.now() + '.db' });
    const r = m.listTags(50);
    assertEq(r.ok, false);
    assertEq(r.alive, false);
  });

  // ── E2E ─────────────────────────────────────────────────────────
  if (E2E) {
    console.log('\n[E2E] 真实 fixture metadata.db(6 books / 4 authors / 5 tags)\n');
    const dbPath = path.join(os.tmpdir(), `calibre-fixture-e2e-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_CALIBRE_DB: dbPath });

    await ta('E2E probeHealth → alive + books=6 + authors=4', () => {
      const r = m.probeHealth();
      assertEq(r.ok, true);
      assertEq(r.alive, true);
      assertEq(r.books_total, 6);
      assertEq(r.authors_total, 4);
    });

    await ta('E2E listBooks 限 3 → 3 本 + 按 timestamp 倒序', () => {
      const r = m.listBooks(3, 0);
      assertEq(r.ok, true);
      assertEq(r.books.length, 3);
      // 1984 (2022-01) → 许三观 (2021-06) → 活着 (2021-04)
      assertEq(r.books[0].title, '1984');
      assertEq(r.books[1].title, '许三观卖血记');
      assertEq(r.books[2].title, '活着');
    });

    await ta('E2E searchBooks "三体" → ≥3 hits', () => {
      const r = m.searchBooks('三体', 10);
      assertEq(r.ok, true);
      assertTrue(r.hits.length >= 3, `expected ≥3, got ${r.hits.length}`);
      assertTrue(r.hits.some((h) => h.title.includes('三体')), 'no hit with 三体 in title');
    });

    // 清理
    try { fs.unlinkSync(dbPath); } catch {}
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 fixture db)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();