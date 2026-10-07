/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/komga-bridge-status/__tests__/run.js
 *   node extensions/komga-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + auth_mode 解析 + 命令实现)
 *   - 加 --e2e 起临时 mock Komga HTTP server,验证 X-API-Key 和 Basic Auth 两种鉴权都生效
 *
 * Phase C(2026-10-07):Komga 是 _scaffold/custom-auth-client.js SDK **首个受益者**
 * 测试同时验证 X-API-Key 模式 + Basic Auth 模式
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

// ── mock Komga HTTP server ────────────────────────────
const DEMO_USER = 'demo';
const DEMO_PASS = 'demo';
const DEMO_API_KEY = 'kmg_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const apiKey = req.headers['x-api-key'];
      const auth = req.headers['authorization'];

      // 鉴权:任一方式通过即可
      let authOk = false;
      if (apiKey === DEMO_API_KEY) authOk = true;
      if (auth && auth.startsWith('Basic ')) {
        const decoded = Buffer.from(auth.slice(6), 'base64').toString('utf8');
        if (decoded === `${DEMO_USER}:${DEMO_PASS}`) authOk = true;
      }
      if (!authOk) {
        res.statusCode = 401;
        res.end(JSON.stringify({ message: 'Unauthorized' }));
        return;
      }

      if (req.url === '/actuator/health') {
        res.end(JSON.stringify({ status: 'UP', groups: ['liveness', 'readiness'] }));
      } else if (req.url.startsWith('/api/v1/libraries')) {
        res.end(JSON.stringify([
          { id: 'lib-manga', name: 'Manga', root: '/data/manga', scanInterval: 'DAILY', unavailable: false },
          { id: 'lib-bd',    name: 'Bandes Dessinées', root: '/data/bd', scanInterval: 'WEEKLY', unavailable: false },
        ]));
      } else if (req.url.startsWith('/api/v1/series')) {
        const u = new URL(req.url, 'http://localhost');
        const page = Number(u.searchParams.get('page') || 0);
        const size = Number(u.searchParams.get('size') || 20);
        const search = u.searchParams.get('search') || '';
        const allSeries = [
          { id: 's-1', name: 'Akira Vol.1', libraryId: 'lib-manga', booksCount: 1, status: 'ENDED' },
          { id: 's-2', name: 'Akira Vol.2', libraryId: 'lib-manga', booksCount: 1, status: 'ENDED' },
          { id: 's-3', name: 'Berserk', libraryId: 'lib-manga', booksCount: 41, status: 'ONGOING' },
        ];
        const filtered = search ? allSeries.filter((s) => s.name.toLowerCase().includes(search.toLowerCase())) : allSeries;
        const content = filtered.slice(page * size, (page + 1) * size);
        res.end(JSON.stringify({
          content,
          totalElements: filtered.length,
          totalPages: Math.max(1, Math.ceil(filtered.length / size)),
          number: page,
          size,
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ message: 'not found' }));
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
  console.log('\n[Phase A 只读扩展单测] komga-bridge-status(Phase C SDK 第三个:custom-auth-client)\n');

  // 1. kmConfig 默认 + auto mode 解析
  t('kmConfig 默认 + auth_mode auto 优先 API Key', () => {
    const m1 = loadModule({});
    const c1 = m1.kmConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:25600');
    assertEq(c1.mode(), 'apiKey');  // 默认 + 无任何 creds 走 apiKey 让 SDK 早退
    assertEq(c1.key(), '');
    assertEq(c1.user(), '');
    const m2 = loadModule({
      PRISIR_KOMGA_API_KEY: 'k-123',
      PRISIR_KOMGA_USER: 'u',
      PRISIR_KOMGA_PASS: 'p',
    });
    const c2 = m2.kmConfig();
    assertEq(c2.mode(), 'apiKey');  // auto:有 Key 优先
    assertEq(c2.key(), 'k-123');
    const m3 = loadModule({ PRISIR_KOMGA_USER: 'u', PRISIR_KOMGA_PASS: 'p' });
    const c3 = m3.kmConfig();
    assertEq(c3.mode(), 'basic');   // auto:无 Key 回退 basic
    assertEq(c3.user(), 'u');
    const m4 = loadModule({ PRISIR_KOMGA_AUTH_MODE: 'basic', PRISIR_KOMGA_API_KEY: 'k' });
    const c4 = m4.kmConfig();
    assertEq(c4.mode(), 'basic');   // explicit 覆盖 auto
  });

  // 2. probeHealth 无 creds → alive=false
  await ta('probeHealth 无 creds → alive=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchLibraries 无 creds → ok=false
  await ta('fetchLibraries 无 creds → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchLibraries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchSeries 无 creds → ok=false
  await ta('fetchSeries 无 creds → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchSeries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_KOMGA_URL: 'http://127.0.0.1:1',
      PRISIR_KOMGA_API_KEY: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:apiKeyHeader / basicAuthHeader
  t('SDK apiKeyHeader / basicAuthHeader 直测', () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const h1 = sdk.apiKeyHeader('k-123');
    assertEq(h1, { 'X-API-Key': 'k-123' });
    const h2 = sdk.apiKeyHeader('');  // empty → no header
    assertEq(h2, {});
    const h3 = sdk.basicAuthHeader('u', 'p');
    const expectedB64 = Buffer.from('u:p').toString('base64');
    assertEq(h3, { Authorization: `Basic ${expectedB64}` });
  });

  // 7. SDK httpGet 不可达 + 无 creds 早退
  await ta('SDK httpGet 无 creds 早退 + 不可达两路', async () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://127.0.0.1:1', mode: 'apiKey', key: '' });
    const r1 = await sdk.httpGet({ config: cfg, path: '/x' });
    assertEq(r1.ok, false);
    assertTrue(/no credentials/i.test(r1.error), `no-cred error: ${r1.error}`);
    const cfg2 = sdk.makeConfig({ baseUrl: 'http://127.0.0.1:1', mode: 'apiKey', key: 'k' });
    const r2 = await sdk.httpGet({ config: cfg2, path: '/x' });
    assertEq(r2.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r2.error), `unref error: ${r2.error}`);
  });

  // ── E2E(mock Komga server 模拟两种鉴权模式)──────────
  if (E2E) {
    console.log('\n[E2E] Mock Komga server @ 127.0.0.1:<random>');
    console.log(`  支持鉴权: X-API-Key=${DEMO_API_KEY} 或 Basic ${DEMO_USER}:${DEMO_PASS}\n`);
    const { server, port } = await startMockServer();

    try {
      // E2E-A: X-API-Key 模式
      const mKey = loadModule({
        PRISIR_KOMGA_URL: `http://127.0.0.1:${port}`,
        PRISIR_KOMGA_API_KEY: DEMO_API_KEY,
      });

      await ta('E2E probeHealth (API Key) → alive + status=UP + latency < 500ms', async () => {
        const r = await mKey.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchLibraries (API Key) → 2 libraries (Manga + BD)', async () => {
        const r = await mKey.fetchLibraries();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.libraries.length, 2);
        assertEq(r.libraries[0].id, 'lib-manga');
        assertEq(r.libraries[0].name, 'Manga');
        assertEq(r.libraries[0].scan_interval, 'DAILY');
        assertEq(r.libraries[1].id, 'lib-bd');
      });

      await ta('E2E fetchSeries (API Key) → 3 series + search 过滤', async () => {
        const r1 = await mKey.fetchSeries();
        assertEq(r1.ok, true);
        assertEq(r1.total_elements, 3);
        assertEq(r1.series.length, 3);
        assertEq(r1.series[2].name, 'Berserk');
        assertEq(r1.series[2].books_count, 41);
        const r2 = await mKey.fetchSeries({ search: 'akira' });
        assertEq(r2.total_elements, 2);
        assertEq(r2.series[0].name, 'Akira Vol.1');
      });

      // E2E-B: Basic Auth 模式
      const mBasic = loadModule({
        PRISIR_KOMGA_URL: `http://127.0.0.1:${port}`,
        PRISIR_KOMGA_USER: DEMO_USER,
        PRISIR_KOMGA_PASS: DEMO_PASS,
        PRISIR_KOMGA_AUTH_MODE: 'basic',
      });

      await ta('E2E probeHealth (Basic Auth) → alive = true', async () => {
        const r = await mBasic.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
      });

      await ta('E2E fetchLibraries (Basic Auth) → 2 libraries 同 API Key 路径', async () => {
        const r = await mBasic.fetchLibraries();
        assertEq(r.ok, true);
        assertEq(r.libraries.length, 2);
      });

      // E2E-C: 错 Key → 401 → ok=false
      const mWrong = loadModule({
        PRISIR_KOMGA_URL: `http://127.0.0.1:${port}`,
        PRISIR_KOMGA_API_KEY: 'wrong-key',
      });

      await ta('E2E 错 Key → 401 → ok=false + last_error', async () => {
        const r = await mWrong.probeHealth();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Komga server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();