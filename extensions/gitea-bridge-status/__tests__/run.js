/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/gitea-bridge-status/__tests__/run.js
 *   node extensions/gitea-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + Bearer SDK 复用)
 *   - 加 --e2e 起临时 mock Gitea HTTP server,验证 Bearer 头 + JSON 嵌套 + 401 错 token
 *
 * Phase C (2026-10-07): Gitea 是 bearer-client.js SDK 第三个用户(触发 SDK 抽取)
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

// ── mock Gitea HTTP server ────────────────────────────────
const DEMO_TOKEN = 'gt_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (req.url === '/api/v1/version') {
        // public 端点,无需鉴权
        res.end(JSON.stringify({ version: '1.21.5' }));
        return;
      }

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ message: 'Unauthorized' }));
        return;
      }

      if (req.url.startsWith('/api/v1/repos/search')) {
        const u = new URL(req.url, 'http://localhost');
        const q = u.searchParams.get('q') || '';
        let allRepos = [
          { id: 1, name: 'prisir-extension-sdk', full_name: 'admin/prisir-extension-sdk', owner: { login: 'admin' }, description: 'Prisir extension SDK', stars_count: 12, forks_count: 2, private: false, fork: false },
          { id: 2, name: 'prisir-bridge-core',     full_name: 'admin/prisir-bridge-core',     owner: { login: 'admin' }, description: 'Bridge core',     stars_count: 5,  forks_count: 1, private: false, fork: false },
          { id: 3, name: 'prisir-fork-test',       full_name: 'contrib/prisir-fork-test',     owner: { login: 'contrib' }, description: 'Fork test',     stars_count: 0,  forks_count: 0, private: false, fork: true },
          { id: 4, name: 'prisir-private',         full_name: 'admin/prisir-private',         owner: { login: 'admin' }, description: '',               stars_count: 0,  forks_count: 0, private: true,  fork: false },
        ];
        if (q) allRepos = allRepos.filter(r => (r.name + r.full_name + r.description).toLowerCase().includes(q.toLowerCase()));
        res.end(JSON.stringify({ ok: true, data: allRepos.slice(0, Number(u.searchParams.get('limit') || 20)), total_count: allRepos.length }));
      } else if (req.url === '/api/v1/orgs') {
        res.end(JSON.stringify([
          { id: 100, username: 'prisir-team', full_name: 'Prisir Team',  description: 'Core team org', avatar_url: '', visibility: 'public' },
          { id: 101, username: 'prisir-lab',  full_name: 'Prisir Lab',   description: 'R&D',           avatar_url: '', visibility: 'private' },
        ]));
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
  console.log('\n[Phase A 只读扩展单测] gitea-bridge-status(bearer SDK 第 3 用户触发抽取)\n');

  // 1. env 默认 + override
  t('env 默认 + override + gtConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.gtConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:3000');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_GITEA_URL: 'http://gt.local:9000',
      PRISIR_GITEA_TOKEN: 'demo-token',
    });
    const c2 = m2.gtConfig();
    assertEq(c2.baseUrl_(), 'http://gt.local:9000');
    assertEq(c2.token_(), 'demo-token');
  });

  // 2. probeHealth 无 token → alive=false + no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchRepos 无 token → ok=false
  await ta('fetchRepos 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchRepos();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchOrgs 无 token → ok=false
  await ta('fetchOrgs 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchOrgs();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_GITEA_URL: 'http://127.0.0.1:1',
      PRISIR_GITEA_TOKEN: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:bearerHeader 格式
  t('SDK bearerHeader 构造 Authorization Bearer 头', () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const h1 = sdk.bearerHeader('t-123');
    assertEq(h1, { Authorization: 'Bearer t-123' });
    const h2 = sdk.bearerHeader('');
    assertEq(h2, {});
  });

  // 7. SDK 直测:httpGet 缺 token 早退
  await ta('SDK httpGet 缺 token 早退', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:3000', token: '' });
    const r = await sdk.httpGet({ config: cfg, path: '/api/v1/version' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error), `error 不对: ${r.error}`);
  });

  // ── E2E(mock Gitea server 模拟 Bearer + JSON 嵌套)─────────
  if (E2E) {
    console.log('\n[E2E] Mock Gitea server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_GITEA_URL: `http://127.0.0.1:${port}`,
        PRISIR_GITEA_TOKEN: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + version=1.21.5', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, '1.21.5');
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchRepos → 4 repos + stars/private/fork 字段', async () => {
        const r = await m.fetchRepos({ limit: 50 });
        assertEq(r.ok, true);
        assertEq(r.repos.length, 4);
        assertEq(r.repos[0].name, 'prisir-extension-sdk');
        assertEq(r.repos[0].owner, 'admin');
        assertEq(r.repos[0].stars, 12);
        assertEq(r.repos[2].fork, true);
        assertEq(r.repos[3].private, true);
      });

      await ta('E2E fetchRepos(q=fork) → 1 repo 过滤', async () => {
        const r = await m.fetchRepos({ q: 'fork' });
        assertEq(r.ok, true);
        assertEq(r.repos.length, 1);
        assertEq(r.repos[0].name, 'prisir-fork-test');
      });

      await ta('E2E fetchOrgs → 2 orgs(public+private)', async () => {
        const r = await m.fetchOrgs();
        assertEq(r.ok, true);
        assertEq(r.orgs.length, 2);
        assertEq(r.orgs[0].username, 'prisir-team');
        assertEq(r.orgs[0].visibility, 'public');
        assertEq(r.orgs[1].username, 'prisir-lab');
        assertEq(r.orgs[1].visibility, 'private');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_GITEA_URL: `http://127.0.0.1:${port}`,
        PRISIR_GITEA_TOKEN: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchRepos();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Gitea server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
