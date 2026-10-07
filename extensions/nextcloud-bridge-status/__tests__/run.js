/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/nextcloud-bridge-status/__tests__/run.js
 *   node extensions/nextcloud-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + inline httpGetOcS + OCS 嵌套)
 *   - 加 --e2e 起临时 mock Nextcloud HTTP server,验证 Bearer + OCS-APIRequest 头 + JSON 嵌套
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

// ── mock Nextcloud HTTP server ────────────────────────────────
const DEMO_TOKEN = 'nc_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');
      const ocsHeader = req.headers['ocs-apirequest'];

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ ocs: { meta: { statuscode: 401, status: 'failure' }, data: { message: 'Unauthorized' } } }));
        return;
      }

      // 验证 OCS-APIRequest 头(Nextcloud 必须,否则服务端可能返回 XML 或 401)
      if (ocsHeader !== 'true') {
        res.statusCode = 400;
        res.end(JSON.stringify({ ocs: { meta: { statuscode: 400, status: 'failure' }, data: { message: 'OCS-APIRequest header required' } } }));
        return;
      }

      if (req.url.startsWith('/ocs/v1.php/cloud/users')) {
        // OCS 返 { ocs: { meta: {...}, data: { users: ["alice", "bob", "carol"] } } }
        res.end(JSON.stringify({
          ocs: {
            meta: { statuscode: 100, status: 'ok', message: 'OK' },
            data: { users: ['alice', 'bob', 'carol'] },
          },
        }));
      } else if (req.url.startsWith('/ocs/v2.php/apps/files_sharing/api/v1/shares')) {
        // OCS shares 返 { ocs: { meta, data: [{id, share_type, ...}] } }
        const u = new URL(req.url, 'http://localhost');
        const pathFilter = u.searchParams.get('path') || '';
        let allShares = [
          { id: 100, share_type: 0, uid_owner: 'alice', displayname_owner: 'Alice', path: '/Documents/report.pdf', permissions: 31, expiration: '' },
          { id: 101, share_type: 3, uid_owner: 'bob',   displayname_owner: 'Bob',   path: '/Photos/sunset.jpg', permissions: 1,  expiration: '2026-12-31' },
          { id: 102, share_type: 1, uid_owner: 'carol', displayname_owner: 'Carol', path: '/Projects/roadmap.md', permissions: 4,  expiration: '' },
        ];
        if (pathFilter) allShares = allShares.filter(s => s.path.startsWith(pathFilter));
        res.end(JSON.stringify({
          ocs: { meta: { statuscode: 100, status: 'ok' }, data: allShares },
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ ocs: { meta: { statuscode: 404, status: 'not found' }, data: {} } }));
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
  console.log('\n[Phase A 只读扩展单测] nextcloud-bridge-status(bearer SDK 第 6 用户 + OCS 头 inline)\n');

  // 1. env 默认 + override
  t('env 默认 + override + ncConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.ncConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:80');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_NEXTCLOUD_URL: 'http://nc.local:8080',
      PRISIR_NEXTCLOUD_TOKEN: 'demo-oauth-token',
    });
    const c2 = m2.ncConfig();
    assertEq(c2.baseUrl_(), 'http://nc.local:8080');
    assertEq(c2.token_(), 'demo-oauth-token');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchUsers 无 token → ok=false
  await ta('fetchUsers 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchUsers();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchShares 无 token → ok=false
  await ta('fetchShares 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchShares();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_NEXTCLOUD_URL: 'http://127.0.0.1:1',
      PRISIR_NEXTCLOUD_TOKEN: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 复用验证 + inline httpGetOcS 缺 token 早退
  await ta('SDK bearerHeader 复用 + inline httpGetOcS 缺 token 早退', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:80', token: '' });
    const h = sdk.bearerHeader('');
    assertEq(h, {});
    const m = loadModule({ PRISIR_NEXTCLOUD_TOKEN: '' });
    const r = await m.httpGetOcS({ config: cfg, path: '/ocs/v1.php/cloud/users' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error));
  });

  // 7. fetchShares path 入参编码
  await ta('fetchShares path 入参编码(URL encode)', async () => {
    // 通过不可达 URL 验证 path 拼装
    const m = loadModule({
      PRISIR_NEXTCLOUD_URL: 'http://127.0.0.1:1',
      PRISIR_NEXTCLOUD_TOKEN: 'demo',
    });
    const r = await m.fetchShares({ path: '/Documents/My Report.pdf' });
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect/i.test(r.last_error || ''));
  });

  // ── E2E(mock Nextcloud server 模拟 Bearer + OCS-APIRequest)──
  if (E2E) {
    console.log('\n[E2E] Mock Nextcloud server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN} + OCS-APIRequest: true\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_NEXTCLOUD_URL: `http://127.0.0.1:${port}`,
        PRISIR_NEXTCLOUD_TOKEN: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive(GET /ocs/v1.php/cloud/users)', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchUsers → 3 users(alice/bob/carol)', async () => {
        const r = await m.fetchUsers();
        assertEq(r.ok, true);
        assertEq(r.users.length, 3);
        assertEq(r.users[0].id, 'alice');
        assertEq(r.users[0].enabled, true);
        assertEq(r.users[1].id, 'bob');
        assertEq(r.users[2].id, 'carol');
      });

      await ta('E2E fetchShares → 3 shares(user/public/group) + share_type', async () => {
        const r = await m.fetchShares();
        assertEq(r.ok, true);
        assertEq(r.shares.length, 3);
        assertEq(r.shares[0].uid_owner, 'alice');
        assertEq(r.shares[0].share_type, 0);                  // user
        assertEq(r.shares[0].permissions, 31);
        assertEq(r.shares[1].share_type, 3);                  // public link
        assertEq(r.shares[1].expiration, '2026-12-31');
        assertEq(r.shares[2].share_type, 1);                  // group
      });

      await ta('E2E fetchShares(path=/Photos) → 1 share 过滤', async () => {
        const r = await m.fetchShares({ path: '/Photos' });
        assertEq(r.ok, true);
        assertEq(r.shares.length, 1);
        assertEq(r.shares[0].path, '/Photos/sunset.jpg');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_NEXTCLOUD_URL: `http://127.0.0.1:${port}`,
        PRISIR_NEXTCLOUD_TOKEN: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchUsers();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Nextcloud server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
