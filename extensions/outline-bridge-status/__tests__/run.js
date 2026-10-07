/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/outline-bridge-status/__tests__/run.js
 *   node extensions/outline-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + httpPostJson SDK + 嵌套解析)
 *   - 加 --e2e 起临时 mock Outline HTTP server,验证 Bearer + POST JSON + ok/data/status 包装
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

// ── mock Outline HTTP server ────────────────────────────────
const DEMO_TOKEN = 'ol_api_demo1234567890abcdef1234567890abcdef12345';

function readBody(req) {
  return new Promise((resolve) => {
    let buf = '';
    req.setEncoding('utf8');
    req.on('data', (c) => { buf += c; });
    req.on('end', () => resolve(buf));
  });
}

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer(async (req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ ok: false, status: 401, error: 'Unauthorized', message: 'Invalid API key' }));
        return;
      }

      // Outline 端点全 POST + JSON body
      const body = await readBody(req);
      let parsed = {};
      try { parsed = JSON.parse(body); } catch {}

      if (req.method === 'POST' && req.url === '/api/auth.info') {
        res.end(JSON.stringify({
          ok: true, status: 200,
          data: {
            user: { id: 'u-1', email: 'octocat@example.com', name: 'Octocat', isAdmin: true },
            team: { id: 't-1', name: 'Prisir Team' },
          },
        }));
      } else if (req.method === 'POST' && req.url === '/api/collections.list') {
        res.end(JSON.stringify({
          ok: true, status: 200,
          data: [
            { id: 'c-eng',     name: 'Engineering', description: 'Tech docs',  color: '#FF6B6B', icon: '🔧', type: 'collection' },
            { id: 'c-product', name: 'Product',     description: 'PRD / RFC',  color: '#4ECDC4', icon: '📦', type: 'collection' },
            { id: 'c-ops',     name: 'Operations',  description: '',           color: '#45B7D1', icon: '⚙️', type: 'collection' },
          ],
        }));
      } else if (req.method === 'POST' && req.url === '/api/documents.list') {
        const limit = Number(parsed.limit) || 25;
        const offset = Number(parsed.offset) || 0;
        const allDocs = [
          { id: 'd-1', title: 'PrisirAI v3 Roadmap',     text: 'Phase C SDK 抽取...',     collectionId: 'c-product', updatedAt: '2026-10-05T10:00:00Z', createdAt: '2026-09-15T08:00:00Z', urlId: 'roadmap', published: true },
          { id: 'd-2', title: 'Extension SDK 协议',        text: '@prisir/extension-sdk...', collectionId: 'c-eng',     updatedAt: '2026-10-04T15:30:00Z', createdAt: '2026-08-20T09:00:00Z', urlId: 'sdk-proto', published: true },
          { id: 'd-3', title: 'Onboarding 流程',          text: '新成员 onboarding 步骤...', collectionId: 'c-ops',     updatedAt: '2026-10-01T11:15:00Z', createdAt: '2026-07-10T07:00:00Z', urlId: 'onboard',  published: true },
          { id: 'd-4', title: '设计原则',                text: '借鉴原则 1-10...',         collectionId: 'c-product', updatedAt: '2026-09-28T16:00:00Z', createdAt: '2026-06-01T08:00:00Z', urlId: 'principles', published: false },
        ];
        const filtered = parsed.collectionId ? allDocs.filter(d => d.collectionId === parsed.collectionId) : allDocs;
        const sliced = filtered.slice(offset, offset + limit);
        res.end(JSON.stringify({
          ok: true, status: 200,
          data: sliced,
          pagination: { offset: offset + sliced.length, limit },
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ ok: false, status: 404, error: 'not found' }));
      }
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
  console.log('\n[Phase A 只读扩展单测] outline-bridge-status(bearer SDK 第 7 用户 + httpPostJson SDK 抽取触发)\n');

  // 1. env 默认 + override
  t('env 默认 + override + olConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.olConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:3000');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_OUTLINE_URL: 'http://outline.local:9000',
      PRISIR_OUTLINE_API_KEY: 'ol_api_demo',
    });
    const c2 = m2.olConfig();
    assertEq(c2.baseUrl_(), 'http://outline.local:9000');
    assertEq(c2.token_(), 'ol_api_demo');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchCollections 无 token → ok=false
  await ta('fetchCollections 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchCollections();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchDocuments 无 token → ok=false
  await ta('fetchDocuments 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchDocuments();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_OUTLINE_URL: 'http://127.0.0.1:1',
      PRISIR_OUTLINE_API_KEY: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 增强验证:httpPostJson 抽进 SDK 共享
  await ta('SDK 增强验证:httpPostJson 抽进 bearer SDK 共享', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    // SDK 5 API 都在
    assertEq(typeof sdk.bearerHeader, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.httpPostJson, 'function', 'httpPostJson 必须抽进 SDK');
    assertEq(typeof sdk.describeAuth, 'function');
    // httpPostJson 缺 token 早退
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:3000', token: '' });
    const r = await sdk.httpPostJson({ config: cfg, path: '/api/auth.info', body: {} });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error));
  });

  // 7. fetchDocuments 入参钳制
  await ta('fetchDocuments 入参:limit 上限 100 + offset ≥ 0', async () => {
    const m = loadModule({
      PRISIR_OUTLINE_URL: 'http://127.0.0.1:1',
      PRISIR_OUTLINE_API_KEY: 'demo',
    });
    const r1 = await m.fetchDocuments({ limit: 999 });
    assertEq(r1.ok, false);
    const r2 = await m.fetchDocuments({ offset: -1 });
    assertEq(r2.ok, false);
  });

  // ── E2E(mock Outline server 模拟 Bearer + POST JSON + ok/data/status)──
  if (E2E) {
    console.log('\n[E2E] Mock Outline server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN.substring(0, 15)}... + POST + JSON\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_OUTLINE_URL: `http://127.0.0.1:${port}`,
        PRISIR_OUTLINE_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + user.email + is_admin', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.user.email, 'octocat@example.com');
        assertEq(r.user.is_admin, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchCollections → 3 collections(eng/product/ops)', async () => {
        const r = await m.fetchCollections();
        assertEq(r.ok, true);
        assertEq(r.collections.length, 3);
        assertEq(r.collections[0].name, 'Engineering');
        assertEq(r.collections[1].name, 'Product');
        assertEq(r.collections[2].name, 'Operations');
        assertEq(r.collections[0].color, '#FF6B6B');
      });

      await ta('E2E fetchDocuments → 4 docs(默认 limit=25)', async () => {
        const r = await m.fetchDocuments();
        assertEq(r.ok, true);
        assertEq(r.documents.length, 4);
        assertEq(r.documents[0].title, 'PrisirAI v3 Roadmap');
        assertEq(r.documents[0].collection_id, 'c-product');
        assertEq(r.documents[0].published, true);
        assertEq(r.documents[3].published, false);
        assertTrue(r.documents[0].text_preview.length <= 200, `text_preview 应截断: ${r.documents[0].text_preview.length}`);
      });

      await ta('E2E fetchDocuments(collectionId=c-eng) → 1 doc 过滤', async () => {
        const r = await m.fetchDocuments({ collectionId: 'c-eng' });
        assertEq(r.ok, true);
        assertEq(r.documents.length, 1);
        assertEq(r.documents[0].title, 'Extension SDK 协议');
        assertEq(r.documents[0].collection_id, 'c-eng');
      });

      await ta('E2E fetchDocuments(limit=2) → 2 docs + pagination', async () => {
        const r = await m.fetchDocuments({ limit: 2 });
        assertEq(r.ok, true);
        assertEq(r.documents.length, 2);
        assertEq(r.next_offset, 2, `next_offset 应为 2: ${r.next_offset}`);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_OUTLINE_URL: `http://127.0.0.1:${port}`,
        PRISIR_OUTLINE_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchCollections();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Outline server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
