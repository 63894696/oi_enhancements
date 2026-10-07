/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/plausible-bridge-status/__tests__/run.js
 *   node extensions/plausible-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + Bearer SDK 复用 + inline POST)
 *   - 加 --e2e 起临时 mock Plausible HTTP server,验证 GET(SDK) + POST JSON(inline) + 401
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

// ── mock Plausible HTTP server ────────────────────────────────
const DEMO_TOKEN = 'pl_demo1234567890abcdef';

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
        res.end(JSON.stringify({ error: 'Unauthorized' }));
        return;
      }

      if (req.method === 'GET' && req.url === '/api/v1/sites') {
        res.end(JSON.stringify([
          { domain: 'example.com',  timezone: 'UTC' },
          { domain: 'blog.example.com', timezone: 'Europe/Berlin' },
        ]));
      } else if (req.method === 'POST' && req.url === '/api/v2/query') {
        const body = await readBody(req);
        let parsed = {};
        try { parsed = JSON.parse(body); } catch {}
        // 简化 mock:根据 metrics 数组长度返回对应聚合数字
        const metrics = Array.isArray(parsed.metrics) ? parsed.metrics : [];
        res.end(JSON.stringify({
          results: metrics.map(() => Math.floor(Math.random() * 1000)),
          query: parsed,
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: 'not found' }));
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
  console.log('\n[Phase A 只读扩展单测] plausible-bridge-status(bearer SDK 第 5 用户,GET 复用 + POST inline)\n');

  // 1. env 默认 + override
  t('env 默认 + override + plConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.plConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:8000');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_PLAUSIBLE_URL: 'http://plausible.local:9000',
      PRISIR_PLAUSIBLE_API_KEY: 'demo-token',
    });
    const c2 = m2.plConfig();
    assertEq(c2.baseUrl_(), 'http://plausible.local:9000');
    assertEq(c2.token_(), 'demo-token');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchSites 无 token → ok=false
  await ta('fetchSites 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchSites();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchSummary 无 token → ok=false
  await ta('fetchSummary 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchSummary({ site_id: 'example.com' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. fetchSummary 缺 site_id → 友好提示
  await ta('fetchSummary 缺 site_id → last_error 含 missing', async () => {
    const m = loadModule({ PRISIR_PLAUSIBLE_API_KEY: 'demo' });
    const r = await m.fetchSummary({});
    assertEq(r.ok, false);
    assertTrue(/site_id/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 6. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_PLAUSIBLE_URL: 'http://127.0.0.1:1',
      PRISIR_PLAUSIBLE_API_KEY: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 7. SDK 复用验证 + httpPostJson SDK 抽取后直接调 SDK
  await ta('SDK 复用(httpGet + httpPostJson 抽进 SDK)+ SDK 缺 token 早退', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:8000', token: '' });
    // SDK 5 API 全用上
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.httpPostJson, 'function', 'httpPostJson 必须抽进 SDK');
    assertEq(typeof sdk.makeConfig, 'function');
    assertEq(typeof sdk.bearerHeader, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    const r1 = await sdk.httpGet({ config: cfg, path: '/api/v1/sites' });
    assertEq(r1.ok, false);
    assertTrue(/no credentials/i.test(r1.error));
    const r2 = await sdk.httpPostJson({ config: cfg, path: '/api/v2/query', body: {} });
    assertEq(r2.ok, false);
    assertTrue(/no credentials/i.test(r2.error));
  });

  // ── E2E(mock Plausible server 模拟 GET + POST JSON)───────
  if (E2E) {
    console.log('\n[E2E] Mock Plausible server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_PLAUSIBLE_URL: `http://127.0.0.1:${port}`,
        PRISIR_PLAUSIBLE_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive(GET /api/v1/sites SDK 复用)', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchSites → 2 sites(example.com + blog)', async () => {
        const r = await m.fetchSites();
        assertEq(r.ok, true);
        assertEq(r.sites.length, 2);
        assertEq(r.sites[0].domain, 'example.com');
        assertEq(r.sites[0].timezone, 'UTC');
        assertEq(r.sites[1].domain, 'blog.example.com');
        assertEq(r.sites[1].timezone, 'Europe/Berlin');
      });

      await ta('E2E fetchSummary → POST /api/v2/query + 聚合 metrics', async () => {
        const r = await m.fetchSummary({
          site_id: 'example.com',
          metrics: ['visitors', 'pageviews'],
          date_range: '7d',
        });
        assertEq(r.ok, true);
        assertEq(r.site_id, 'example.com');
        assertEq(r.metrics.length, 2);
        assertEq(r.date_range, '7d');
        assertTrue(typeof r.summary.visitors === 'number', `summary.visitors 应为 number: ${r.summary.visitors}`);
        assertTrue(typeof r.summary.pageviews === 'number', `summary.pageviews 应为 number: ${r.summary.pageviews}`);
      });

      // 错 token → 401(走 GET /api/v1/sites 验 SDK 错误传播)
      const mWrong = loadModule({
        PRISIR_PLAUSIBLE_URL: `http://127.0.0.1:${port}`,
        PRISIR_PLAUSIBLE_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchSites();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Plausible server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
