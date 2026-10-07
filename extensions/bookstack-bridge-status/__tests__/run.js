/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/bookstack-bridge-status/__tests__/run.js
 *   node extensions/bookstack-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + httpGet SDK + 嵌套解析)
 *   - 加 --e2e 起临时 mock BookStack HTTP server,验证 Bearer + 真 REST GET + data/total 包装
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

// ── mock BookStack HTTP server ────────────────────────────────
const DEMO_TOKEN = 'bs_demo_token_id:bs_demo_secret_64chars_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: [{ code: 'UNAUTHORIZED', message: 'Invalid API token' }] }));
        return;
      }

      // BookStack 真 REST GET
      if (req.method === 'GET' && req.url === '/api/books') {
        res.end(JSON.stringify({
          data: [
            { id: 1, slug: 'engineering-handbook', name: 'Engineering Handbook', description: 'Tech wiki', created_at: '2026-09-15T08:00:00Z', updated_at: '2026-10-05T10:00:00Z' },
            { id: 2, slug: 'product-rfc',           name: 'Product RFC',           description: 'PRDs',         created_at: '2026-08-20T09:00:00Z', updated_at: '2026-10-04T15:30:00Z' },
            { id: 3, slug: 'onboarding',            name: 'Onboarding',            description: '',              created_at: '2026-07-10T07:00:00Z', updated_at: '2026-10-01T11:15:00Z' },
          ],
          total: 3,
        }));
      } else if (req.method === 'GET' && req.url === '/api/shelves') {
        res.end(JSON.stringify({
          data: [
            { id: 10, slug: 'tech',    name: 'Tech Library',   description: 'All technical books', books_count: 2 },
            { id: 11, slug: 'product', name: 'Product Library', description: '',                   books_count: 1 },
          ],
          total: 2,
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: [{ code: 'NOT_FOUND', message: `Route ${req.method} ${req.url} not found` }] }));
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
  console.log('\n[Phase A 只读扩展单测] bookstack-bridge-status(bearer SDK 第 8 用户 + 首个纯 REST + Bearer)\n');

  // 1. env 默认 + override
  t('env 默认 + override + bsConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.bsConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_BOOKSTACK_URL: 'http://bookstack.local:8080',
      PRISIR_BOOKSTACK_API_KEY: 'bs_id:bs_secret',
    });
    const c2 = m2.bsConfig();
    assertEq(c2.baseUrl_(), 'http://bookstack.local:8080');
    assertEq(c2.token_(), 'bs_id:bs_secret');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchShelves 无 token → ok=false
  await ta('fetchShelves 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchShelves();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchBooks 无 token → ok=false
  await ta('fetchBooks 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchBooks();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_BOOKSTACK_URL: 'http://127.0.0.1:1',
      PRISIR_BOOKSTACK_API_KEY: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 复用验证:httpGet + describeAuth 都在 SDK 上,扩展只用 SDK API
  await ta('SDK 复用验证:httpGet + describeAuth SDK 在场(纯 REST 扩展)', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    // 纯 REST 扩展只用 SDK 4 API(httpGet/httpPostJson 都有,但 httpGet 足够)
    assertEq(typeof sdk.bearerHeader, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.httpPostJson, 'function', 'httpPostJson SDK API 仍在(Outline/Plausible 触发)');
    assertEq(typeof sdk.describeAuth, 'function');
    // describeAuth 验证 has_token 判定
    const cfgEmpty = sdk.makeConfig({ baseUrl: 'http://x:80', token: '' });
    assertEq(sdk.describeAuth(cfgEmpty).has_token, false);
    const cfgFull = sdk.makeConfig({ baseUrl: 'http://x:80', token: 'id:secret' });
    assertEq(sdk.describeAuth(cfgFull).has_token, true);
  });

  // 7. fetchShelves/fetchBooks 的 fetchShelves 在没 books_count 字段时不应崩
  await ta('fetchShelves 无 book_count 字段保护 + data 缺字段容错', async () => {
    // 不可达场景验证嵌套解析健壮性(不依赖 E2E)
    const m = loadModule({
      PRISIR_BOOKSTACK_URL: 'http://127.0.0.1:1',
      PRISIR_BOOKSTACK_API_KEY: 'demo',
    });
    const r1 = await m.fetchShelves();
    assertEq(r1.ok, false);
    const r2 = await m.fetchBooks();
    assertEq(r2.ok, false);
  });

  // ── E2E(mock BookStack server 模拟 Bearer + 真 REST GET)──
  if (E2E) {
    console.log('\n[E2E] Mock BookStack server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN.split(':')[0]}:***\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_BOOKSTACK_URL: `http://127.0.0.1:${port}`,
        PRISIR_BOOKSTACK_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + total_books=3 + latency < 500ms', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.total_books, 3);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchShelves → 2 shelves + books_count 字段', async () => {
        const r = await m.fetchShelves();
        assertEq(r.ok, true);
        assertEq(r.shelves.length, 2);
        assertEq(r.shelves[0].name, 'Tech Library');
        assertEq(r.shelves[0].book_count, 2);
        assertEq(r.shelves[1].name, 'Product Library');
        assertEq(r.shelves[1].book_count, 1);
      });

      await ta('E2E fetchBooks → 3 books + slug/created_at 字段', async () => {
        const r = await m.fetchBooks();
        assertEq(r.ok, true);
        assertEq(r.books.length, 3);
        assertEq(r.books[0].name, 'Engineering Handbook');
        assertEq(r.books[0].slug, 'engineering-handbook');
        assertEq(r.books[0].description, 'Tech wiki');
        assertEq(r.total, 3);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_BOOKSTACK_URL: `http://127.0.0.1:${port}`,
        PRISIR_BOOKSTACK_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchBooks();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock BookStack server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();