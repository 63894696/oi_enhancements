/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测
 *
 * 跑法:
 *   node extensions/siyuan-vault-indexer/__tests__/run.js
 *   node extensions/siyuan-vault-indexer/__tests__/run.js --e2e   # 加 fixture db 端到端
 *
 * 测试策略:
 *   - 用 node:sqlite 在 :memory: 建 fixture SiYuan schema(blocks + blocks_fts5)
 *   - fixture db 写到临时文件供 ext fixture E2E 用(PRISIR_SIYUAN_DB 指向它)
 *   - 默认 8 单测,加 --e2e 多 3 个真实 fixture db 端到端
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

// ── fixture SiYuan db 构造器 ──────────────────────────────────────
// 按 kernel/sql/database.go master 复刻 SiYuan schema(tokenize 用 unicode61 近似)
function buildFixtureDb(dbPath) {
  if (fs.existsSync(dbPath)) fs.unlinkSync(dbPath);
  const d = new sqlite.DatabaseSync(dbPath);
  d.exec(`
    CREATE TABLE blocks (
      id TEXT PRIMARY KEY,
      parent_id TEXT,
      root_id TEXT,
      hash TEXT,
      box TEXT,
      path TEXT,
      hpath TEXT,
      name TEXT,
      alias TEXT,
      memo TEXT,
      tag TEXT,
      content TEXT,
      fcontent TEXT,
      markdown TEXT,
      length INTEGER,
      type TEXT,
      subtype TEXT,
      ial TEXT,
      sort INTEGER,
      created TEXT,
      updated TEXT
    );
    CREATE VIRTUAL TABLE blocks_fts5 USING fts5(
      id UNINDEXED, parent_id UNINDEXED, root_id UNINDEXED,
      hash UNINDEXED, box UNINDEXED, path UNINDEXED, hpath,
      name, alias, memo, tag, content, fcontent, markdown UNINDEXED,
      length UNINDEXED, type UNINDEXED, subtype UNINDEXED, ial,
      sort UNINDEXED, created UNINDEXED, updated UNINDEXED,
      tokenize="unicode61"
    );
  `);
  // 注入 3 个 notebook + 8 个 block
  const ins = d.prepare(`INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const insFts = d.prepare(`INSERT INTO blocks_fts5 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const rows = [
    ['20260101000001-aaaaaa', '',         '20260101000001-aaaaaa', '', '2026010100000000-7q', '/d/20260101000001-aaaaaa.sy', '/NotebookA/Doc1', 'Doc1',  '', '', '#思源,#日记',     '这是我的第一篇日记内容', '',     '# 思源\n第一篇日记内容', 10, 'd', '', '', 0, '20260101000000', '20260101000000'],
    ['20260101000002-bbbbbb', '20260101000001-aaaaaa', '20260101000001-aaaaaa', '', '2026010100000000-7q', '/d/20260101000001-aaaaaa.sy', '/NotebookA/Doc1/Para1', '',  '', '', '',              '今天天气真好适合写代码', '今天天气真好适合写代码', '今天天气真好适合写代码', 12, 'p', '', '', 1, '20260101000001', '20260101000001'],
    ['20260101000003-cccccc', '',         '20260101000003-cccccc', '', '2026010100000000-7q', '/d/20260101000003-cccccc.sy', '/NotebookB/Doc2', 'Doc2',  '', '', '',              'RUST 学习笔记',     '',     '# RUST\n学习笔记',       8,  'd', '', '', 0, '20260101000002', '20260101000002'],
    ['20260101000004-dddddd', '20260101000003-cccccc', '20260101000003-cccccc', '', '2026010100000000-7q', '/d/20260101000003-cccccc.sy', '/NotebookB/Doc2/Head', '',  '', '', '',              '所有权系统',         '所有权系统',         '## 所有权系统',         5,  'h', 'h2',  '', 1, '20260101000003', '20260101000003'],
    ['20260101000005-eeeeee', '20260101000003-cccccc', '20260101000003-cccccc', '', '2026010100000000-7q', '/d/20260101000003-cccccc.sy', '/NotebookB/Doc2/Para', '',  '', '', '',              'Rust 变量默认不可变', 'Rust 变量默认不可变', 'Rust 变量默认不可变',  11, 'p', '',    '', 2, '20260101000004', '20260101000004'],
    ['20260101000006-ffffff', '',         '20260101000006-ffffff', '', '2026010200000000-8r', '/d/20260101000006-ffffff.sy', '/NotebookC/Doc3', 'Doc3',  '', '', '#技术,#Python',  'Python 多线程笔记',     '',     '# Python\n多线程笔记',   10, 'd', '', '', 0, '20260101000005', '20260101000005'],
    ['20260101000007-gggggg', '20260101000006-ffffff', '20260101000006-ffffff', '', '2026010200000000-8r', '/d/20260101000006-ffffff.sy', '/NotebookC/Doc3/Para', '',  '', '', '',              'GIL 全局解释器锁',   'GIL 全局解释器锁',   'GIL 全局解释器锁',    10, 'p', '',    '', 1, '20260101000006', '20260101000006'],
    ['20260101000008-hhhhhh', '20260101000006-ffffff', '20260101000006-ffffff', '', '2026010200000000-8r', '/d/20260101000006-ffffff.sy', '/NotebookC/Doc3/Head', '',  '', '', '',              '协程与线程',         '协程与线程',         '## 协程与线程',         5,  'h', 'h2',  '', 2, '20260101000007', '20260101000007'],
  ];
  for (const r of rows) {
    ins.run(...r);
    insFts.run(...r);
  }
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
  console.log('\n[Phase A 只读扩展单测] siyuan-vault-indexer\n');

  // 1. escapeFts 双引号转义
  t('escapeFts 双引号转义', () => {
    const m = loadModule({});
    assertEq(m.escapeFts('hello'), 'hello');
    assertEq(m.escapeFts('he said "hi"'), 'he said ""hi""');
    assertEq(m.escapeFts(''), '');
    assertEq(m.escapeFts(null), '');
    assertEq(m.escapeFts(undefined), '');
  });

  // 2. siyuanDbPath env 覆盖
  t('siyuanDbPath env 覆盖默认', () => {
    const m1 = loadModule({});
    const defaultPath = m1.siyuanDbPath();
    assertTrue(defaultPath.endsWith('siyuan.db') || defaultPath.includes('siyuan'), `default ${defaultPath}`);
    const m2 = loadModule({ PRISIR_SIYUAN_DB: '/tmp/custom.db' });
    assertEq(m2.siyuanDbPath(), '/tmp/custom.db');
  });

  // 3. probeHealth db 不存在 → ok=false + alive=false
  t('probeHealth db 不存在 → ok=false', () => {
    const m = loadModule({ PRISIR_SIYUAN_DB: '/tmp/does-not-exist-' + Date.now() + '.db' });
    const r = m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 4. getBlock id 空 → ok=false
  t('getBlock 空 id → last_error=empty id', () => {
    const dbPath = path.join(os.tmpdir(), `siyuan-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_SIYUAN_DB: dbPath });
    const r = m.getBlock('');
    assertEq(r.ok, false);
    assertEq(r.alive, true);
    assertEq(r.last_error, 'empty id');
  });

  // 5. getBlock 不存在 id → block=null
  t('getBlock 不存在 id → block=null + ok=true', () => {
    const dbPath = path.join(os.tmpdir(), `siyuan-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_SIYUAN_DB: dbPath });
    const r = m.getBlock('99999999999999-xxxxxx');
    assertEq(r.ok, true);
    assertEq(r.alive, true);
    assertEq(r.block, null);
    assertEq(r.last_error, '');
  });

  // 6. listNotebooks 无 db → ok=false
  t('listNotebooks 无 db → ok=false + alive=false', () => {
    const m = loadModule({ PRISIR_SIYUAN_DB: '/tmp/none-' + Date.now() + '.db' });
    const r = m.listNotebooks();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 7. searchBlocks 空 query → ok + hits=[]
  t('searchBlocks 空 query → ok + hits=[]', () => {
    const dbPath = path.join(os.tmpdir(), `siyuan-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_SIYUAN_DB: dbPath });
    const r = m.searchBlocks('', 10);
    assertEq(r.ok, true);
    assertEq(r.hits, []);
  });

  // 8. searchBlocks limit clamp [1, 100]
  t('searchBlocks limit clamp [1, 100]', () => {
    const dbPath = path.join(os.tmpdir(), `siyuan-fixture-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_SIYUAN_DB: dbPath });
    // limit 99999 应被截到 100;limit 0 应被抬到 1
    const r1 = m.searchBlocks('Rust', 99999);
    assertEq(r1.ok, true);
    const r2 = m.searchBlocks('Rust', 0);
    assertEq(r2.ok, true);
  });

  // ── E2E ─────────────────────────────────────────────────────────
  if (E2E) {
    console.log('\n[E2E] 真实 fixture db(8 blocks / 3 notebooks)\n');
    const dbPath = path.join(os.tmpdir(), `siyuan-fixture-e2e-${process.pid}-${Date.now()}.db`);
    buildFixtureDb(dbPath);
    const m = loadModule({ PRISIR_SIYUAN_DB: dbPath });

    await ta('E2E probeHealth → alive + blocks_total=8', () => {
      const r = m.probeHealth();
      assertEq(r.ok, true);
      assertEq(r.alive, true);
      assertEq(r.blocks_total, 8);
    });

    await ta('E2E listNotebooks → 2 个 notebook', () => {
      const r = m.listNotebooks();
      assertEq(r.ok, true);
      // fixture 设计:8 blocks 分 2 个 box(7q 含 5 doc,8r 含 3 doc)
      assertEq(r.notebooks.length, 2);
      const boxes = new Set(r.notebooks.map((n) => n.box));
      assertEq(boxes.size, 2);
    });

    await ta('E2E searchBlocks "Rust" → ≥2 hits', () => {
      const r = m.searchBlocks('Rust', 10);
      assertEq(r.ok, true);
      assertTrue(r.hits.length >= 2, `expected ≥2 hits, got ${r.hits.length}`);
      // 第一个 hit 应包含 <<Rust>> marker(snippet 高亮)
      assertTrue(r.hits[0].snippet.includes('<<') || r.hits[0].snippet.includes('Rust'),
        `hit 0 snippet missing Rust: ${r.hits[0].snippet}`);
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
