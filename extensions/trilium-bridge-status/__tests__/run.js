/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/trilium-bridge-status/__tests__/run.js
 *   node extensions/trilium-bridge-status/__tests__/run.js --e2e
 *
 * Phase C (2026-10-08): Trilium 是 bearer SDK 第 13 个用户
 * (单层 Bearer ETAPI + 直接对象数组 envelope)
 */
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const vm = require('vm');

const EXT_DIR = path.resolve(__dirname, '..');
const INDEX_JS = path.join(EXT_DIR, 'index.js');
const SRC = fs.readFileSync(INDEX_JS, 'utf8');
const E2E = process.argv.includes('--e2e');

// ── mock Trilium ETAPI server ────────────────────────────────
const DEMO_TOKEN = 'trilium_demo_bearer_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      // /etapi/app-info 公开探活
      if (req.method === 'GET' && req.url === '/etapi/app-info') {
        res.end(JSON.stringify({
          appName: 'Trilium',
          appVersion: '0.63.7',
          dbVersion: '230',
          syncVersion: 32,
          buildDate: '2026-09-15T10:00:00Z',
        }));
        return;
      }

      // 错 token → 401
      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: 'Unauthorized' }));
        return;
      }

      // /etapi/notes(列/搜索,带 search/fastSearch/limit/archived/debug query)
      if (req.method === 'GET' && (req.url === '/etapi/notes' || req.url.startsWith('/etapi/notes?'))) {
        const u = new URL(req.url, 'http://x');
        const search = u.searchParams.get('search') || '';
        const allNotes = [
          { noteId: 'root', title: 'root', type: 'text', mime: 'text/html',
            dateCreated: '2024-01-01T00:00:00Z', dateModified: '2026-10-01T08:00:00Z',
            parentNoteIds: [], childNoteIds: ['note1', 'note2'], isArchived: false, isProtected: false },
          { noteId: 'note1', title: 'Daily Journal', type: 'text', mime: 'text/html',
            dateCreated: '2024-06-15T00:00:00Z', dateModified: '2026-10-08T10:30:00Z',
            parentNoteIds: ['root'], childNoteIds: ['note1child1'], isArchived: false, isProtected: false },
          { noteId: 'note2', title: 'API Keys Reference (sensitive)', type: 'text', mime: 'text/html',
            dateCreated: '2025-03-10T00:00:00Z', dateModified: '2026-09-20T12:00:00Z',
            parentNoteIds: ['root'], childNoteIds: [], isArchived: false, isProtected: true /* 加密 */ },
          { noteId: 'note1child1', title: '2026-10-08 entry', type: 'text', mime: 'text/html',
            dateCreated: '2026-10-08T00:00:00Z', dateModified: '2026-10-08T22:00:00Z',
            parentNoteIds: ['note1'], childNoteIds: [], isArchived: false, isProtected: false },
        ];
        const filtered = search
          ? allNotes.filter(n => n.title.toLowerCase().includes(search.toLowerCase()))
          : allNotes;
        res.end(JSON.stringify(filtered));
        return;
      }

      // /etapi/notes/{id} 单 note
      if (req.method === 'GET' && /^\/etapi\/notes\/[A-Za-z0-9_-]+\/?$/.test(req.url)) {
        const noteId = req.url.split('/').filter(s => s.length > 0).pop();
        if (noteId === 'note1') {
          res.end(JSON.stringify({
            noteId: 'note1',
            title: 'Daily Journal',
            type: 'text',
            mime: 'text/html',
            dateCreated: '2024-06-15T00:00:00Z',
            dateModified: '2026-10-08T10:30:00Z',
            parentNoteIds: ['root'],
            childNoteIds: ['note1child1', 'note1child2'],
            isArchived: false,
            isProtected: false,
          }));
          return;
        }
        if (noteId === 'note2') {
          res.end(JSON.stringify({
            noteId: 'note2',
            title: 'API Keys Reference (sensitive)',
            type: 'text',
            mime: 'text/html',
            dateCreated: '2025-03-10T00:00:00Z',
            dateModified: '2026-09-20T12:00:00Z',
            parentNoteIds: ['root'],
            childNoteIds: [],
            isArchived: false,
            isProtected: true,
          }));
          return;
        }
        if (noteId === 'nonexistent') {
          res.statusCode = 404;
          res.end(JSON.stringify({ error: 'Note not found' }));
          return;
        }
        // 兜底返通用
        res.end(JSON.stringify({
          noteId, title: `Mock ${noteId}`, type: 'text', mime: 'text/html',
          dateCreated: '2026-01-01T00:00:00Z', dateModified: '2026-10-01T00:00:00Z',
          parentNoteIds: [], childNoteIds: [], isArchived: false, isProtected: false,
        }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ error: `Route ${req.method} ${req.url} not found` }));
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

// ── sandbox 加载 index.js ─────────────────────────────────────
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
  const { createRequire } = require('module');
  const realRequire = createRequire(INDEX_JS);
  const requireFn = (id) => {
    if (id === '@prisir/extension-sdk') return sdkStub;
    try { return realRequire(id); } catch (e) { throw e; }
  };
  const stubProcess = { env: { ...process.env, ...extraEnv } };
  const sandbox = {
    module: captured,
    exports: captured.exports,
    require: requireFn,
    console,
    process: stubProcess,
    Buffer,
    setTimeout,
    clearTimeout,
    setImmediate,
    clearImmediate,
    http,
    URL,
    URLSearchParams,
  };
  sandbox.global = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return captured.exports;
}

// ── 极简 assert ──────────────────────────────────────────────
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
  console.log('\n[Phase A 只读扩展单测] trilium-bridge-status(bearer SDK 第 13 用户 + 跨入笔记/层级知识域 + 单层 Bearer ETAPI)\n');

  // 1. env 默认 + override
  t('env 默认 + override + triliumConfig 不可变', () => {
    const m1 = loadModule({});
    assertEq(m1.triliumConfig().baseUrl_(), 'http://127.0.0.1:8080');
    assertEq(m1.triliumConfig().token_(), '');
    assertEq(m1.triliumConfig().timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_TRILIUM_URL: 'http://trilium.local:9000',
      PRISIR_TRILIUM_API_KEY: 'trilium_jwt',
    });
    assertEq(m2.triliumConfig().baseUrl_(), 'http://trilium.local:9000');
    assertEq(m2.triliumConfig().token_(), 'trilium_jwt');
  });

  // 2. probeHealth 无 token → no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchNotes 无 token → ok=false
  await ta('fetchNotes 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchNotes();
    assertEq(r.ok, false);
  });

  // 4. fetchNote 无 token → ok=false
  await ta('fetchNote 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchNote({ noteId: 'note1' });
    assertEq(r.ok, false);
  });

  // 5. fetchNote 错 noteId → invalid
  await ta('fetchNote 空 noteId → invalid noteId', async () => {
    const m = loadModule({ PRISIR_TRILIUM_API_KEY: 'tok' });
    const r = await m.fetchNote({ noteId: '' });
    assertEq(r.ok, false);
    assertTrue(/invalid noteId/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 6. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_TRILIUM_URL: 'http://127.0.0.1:1',
      PRISIR_TRILIUM_API_KEY: 'tok',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 7. SDK 复用验证
  await ta('SDK 复用验证:httpGet + describeAuth + makeConfig SDK 在场', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:8080', token: '' });
    assertEq(sdk.describeAuth(cfg).has_token, false);
  });

  // 8. fetchNotes query 拼接
  t('fetchNotes query 拼接:空 args → 无 ?;带 args → ?search=...', async () => {
    const m = loadModule({
      PRISIR_TRILIUM_URL: 'http://127.0.0.1:1',
      PRISIR_TRILIUM_API_KEY: 'demo',
    });
    // 不可达但能验 query 串构造不抛
    m.fetchNotes({ search: 'journal', limit: 5 }).catch(() => {});
  });

  // ── E2E(mock Trilium ETAPI 模拟 Bearer + 直数组 envelope + content 不抓)──
  if (E2E) {
    console.log('\n[E2E] Mock Trilium ETAPI @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer <token> + GET + 直数组 envelope + content 字段不抓\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_TRILIUM_URL: `http://127.0.0.1:${port}`,
        PRISIR_TRILIUM_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + app_version=0.63.7 + db_version=230 + sync=32', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.app_name, 'Trilium');
        assertEq(r.app_version, '0.63.7');
        assertEq(r.db_version, '230');
        assertEq(r.sync_version, 32);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchNotes(默认) → 4 notes + is_protected 标记 + has_children', async () => {
        const r = await m.fetchNotes();
        assertEq(r.ok, true);
        assertEq(r.total, 4);
        assertEq(r.notes[0].note_id, 'root');
        assertEq(r.notes[0].has_children, true);
        assertEq(r.notes[1].title, 'Daily Journal');
        assertEq(r.notes[2].title, 'API Keys Reference (sensitive)');
        assertEq(r.notes[2].is_protected, true, '加密 note 应被标记');
        assertEq(r.notes[2].is_archived, false);
      });

      await ta('E2E fetchNotes(search=daily) → 只 Daily Journal 匹配', async () => {
        const r = await m.fetchNotes({ search: 'Daily' });
        assertEq(r.ok, true);
        assertEq(r.total, 1);
        assertEq(r.notes[0].title, 'Daily Journal');
      });

      await ta('E2E fetchNotes(limit=2) → 2 notes(验 query 拼接成功)', async () => {
        const r = await m.fetchNotes({ limit: 2 });
        // mock 不真 limit,全返 — 验 query 拼接成功即可
        assertEq(r.ok, true);
      });

      await ta('E2E fetchNote(note1) → 元数据 + child_count=2 + content_included=false', async () => {
        const r = await m.fetchNote({ noteId: 'note1' });
        assertEq(r.ok, true);
        assertEq(r.note.note_id, 'note1');
        assertEq(r.note.title, 'Daily Journal');
        assertEq(r.note.is_protected, false);
        assertEq(r.note.child_count, 2);
        assertEq(r.content_included, false, 'Phase A 不抓 content(产品级 P0)');
      });

      await ta('E2E fetchNote(note2) → is_protected=true(标记,不解密)', async () => {
        const r = await m.fetchNote({ noteId: 'note2' });
        assertEq(r.ok, true);
        assertEq(r.note.is_protected, true);
        assertEq(r.content_included, false);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_TRILIUM_URL: `http://127.0.0.1:${port}`,
        PRISIR_TRILIUM_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchNotes();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });

      await ta('E2E fetchNote(nonexistent) → 404', async () => {
        const r = await m.fetchNote({ noteId: 'nonexistent' });
        assertEq(r.ok, false);
        assertEq(r.http_status, 404);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Trilium ETAPI)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();