/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/drone-bridge-status/__tests__/run.js
 *   node extensions/drone-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + Bearer SDK 跨域复用)
 *   - 加 --e2e 起临时 mock Drone CI HTTP server,验证 Bearer 头 + JSON + 401
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

// ── mock Drone CI HTTP server ────────────────────────────────
const DEMO_TOKEN = 'dr_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ message: 'Unauthorized' }));
        return;
      }

      if (req.url.startsWith('/api/user') && !req.url.startsWith('/api/user/repos') && !req.url.startsWith('/api/user/feed')) {
        res.end(JSON.stringify({
          id: 1, login: 'octocat', email: 'octocat@example.com',
          active: true, admin: true,
        }));
      } else if (req.url.startsWith('/api/user/repos')) {
        res.end(JSON.stringify([
          { id: 100, slug: 'octocat/hello-world', name: 'hello-world', full_name: 'octocat/hello-world', active: true, private: false, config: '.drone.yml',
            last_build: { number: 42, status: 'success', event: 'push' } },
          { id: 101, slug: 'octocat/prisir-bridge', name: 'prisir-bridge', full_name: 'octocat/prisir-bridge', active: true, private: true, config: '.drone.yml',
            last_build: { number: 17, status: 'running', event: 'pull_request' } },
          { id: 102, slug: 'octocat/archived', name: 'archived', full_name: 'octocat/archived', active: false, private: false, config: '',
            last_build: { number: 0, status: '', event: '' } },
        ]));
      } else if (req.url.startsWith('/api/user/feed')) {
        const u = new URL(req.url, 'http://localhost');
        const limit = Number(u.searchParams.get('limit') || 20);
        const all = [
          { build: { id: 42, number: 42, status: 'success', event: 'push',         branch: 'main',    created_at: 1728000000 }, repo: { slug: 'octocat/hello-world', name: 'hello-world' } },
          { build: { id: 41, number: 41, status: 'failure', event: 'pull_request', branch: 'feature', created_at: 1727900000 }, repo: { slug: 'octocat/prisir-bridge', name: 'prisir-bridge' } },
          { build: { id: 17, number: 17, status: 'running', event: 'pull_request', branch: 'main',    created_at: 1727800000 }, repo: { slug: 'octocat/prisir-bridge', name: 'prisir-bridge' } },
        ];
        res.end(JSON.stringify(all.slice(0, limit)));
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
  console.log('\n[Phase A 只读扩展单测] drone-bridge-status(bearer SDK 第 4 用户,跨域 CI/CD)\n');

  // 1. env 默认 + override
  t('env 默认 + override + drConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.drConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:8080');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_DRONE_URL: 'http://dr.local:9000',
      PRISIR_DRONE_TOKEN: 'demo-token',
    });
    const c2 = m2.drConfig();
    assertEq(c2.baseUrl_(), 'http://dr.local:9000');
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

  // 3. fetchRepos 无 token → ok=false
  await ta('fetchRepos 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchRepos();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchRecentBuilds 无 token → ok=false
  await ta('fetchRecentBuilds 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchRecentBuilds();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_DRONE_URL: 'http://127.0.0.1:1',
      PRISIR_DRONE_TOKEN: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:用 Gitea 同样的 SDK(零修改验证 bearer SDK 复用)
  t('SDK 复用验证 — 用 Gitea 已 ship 的 bearer-client.js', () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const h = sdk.bearerHeader('shared-token');
    assertEq(h, { Authorization: 'Bearer shared-token' });
  });

  // 7. SDK 直测:httpGet 缺 token 早退
  await ta('SDK httpGet 缺 token 早退', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:8080', token: '' });
    const r = await sdk.httpGet({ config: cfg, path: '/api/user' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error), `error 不对: ${r.error}`);
  });

  // ── E2E(mock Drone CI server 模拟 Bearer)─────────────────
  if (E2E) {
    console.log('\n[E2E] Mock Drone CI server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_DRONE_URL: `http://127.0.0.1:${port}`,
        PRISIR_DRONE_TOKEN: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + user.login=octocat + admin', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.user.login, 'octocat');
        assertEq(r.user.admin, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchRepos → 3 repos(active/private/archived)', async () => {
        const r = await m.fetchRepos();
        assertEq(r.ok, true);
        assertEq(r.repos.length, 3);
        assertEq(r.repos[0].slug, 'octocat/hello-world');
        assertEq(r.repos[0].active, true);
        assertEq(r.repos[0].last_build.status, 'success');
        assertEq(r.repos[1].private, true);
        assertEq(r.repos[2].active, false);
      });

      await ta('E2E fetchRecentBuilds → 3 builds(success/failure/running)', async () => {
        const r = await m.fetchRecentBuilds({ limit: 50 });
        assertEq(r.ok, true);
        assertEq(r.builds.length, 3);
        assertEq(r.builds[0].status, 'success');
        assertEq(r.builds[0].branch, 'main');
        assertEq(r.builds[1].status, 'failure');
        assertEq(r.builds[1].event, 'pull_request');
        assertEq(r.builds[2].status, 'running');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_DRONE_URL: `http://127.0.0.1:${port}`,
        PRISIR_DRONE_TOKEN: 'wrong-token',
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
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Drone CI server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
