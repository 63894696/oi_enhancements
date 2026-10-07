/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/firefly-iii-bridge-status/__tests__/run.js
 *   node extensions/firefly-iii-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 8 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + httpGet SDK + JSON:API 扁平化)
 *   - 加 --e2e 起临时 mock Firefly III HTTP server,验证 Bearer + REST GET + JSON:API envelope 解析
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

// ── mock Firefly III HTTP server ────────────────────────────────
const DEMO_TOKEN = 'ff_pat_demo1234567890abcdef1234567890abcdef12345678';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/vnd.api+json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ message: 'Unauthenticated' }));
        return;
      }

      // Firefly III 真 REST GET,JSON:API envelope
      if (req.method === 'GET' && req.url === '/api/v1/about') {
        res.end(JSON.stringify({
          data: {
            type: 'about', id: 'system',
            attributes: {
              version: '6.1.7', api_version: '2.0.0',
              php_version: '8.3.0', user_agent: 'FireflyIII/6.1.7',
              driver: 'sqlite', 'db-version': '3.45.0',
            },
          },
          meta: { permissions: [], route: 'about' },
        }));
      } else if (req.method === 'GET' && req.url === '/api/v1/about/user') {
        res.end(JSON.stringify({
          data: {
            type: 'users', id: '1',
            attributes: {
              email: 'me@example.com', role: 'owner',
              blocked: false, blocked_code: null,
            },
          },
        }));
      } else if (req.method === 'GET' && req.url === '/api/v1/accounts?limit=100') {
        res.end(JSON.stringify({
          data: [
            { type: 'accounts', id: '1',
              attributes: {
                name: 'Checking', type: 'asset', account_role: 'defaultAsset',
                currency_code: 'USD', current_balance: '1000.00', active: true, notes: 'Primary checking',
              } },
            { type: 'accounts', id: '2',
              attributes: {
                name: 'Savings', type: 'asset', account_role: 'savingAsset',
                currency_code: 'USD', current_balance: '5000.00', active: true, notes: '',
              } },
            { type: 'accounts', id: '3',
              attributes: {
                name: 'Groceries', type: 'expense', account_role: null,
                currency_code: 'USD', current_balance: '0.00', active: true, notes: 'Food budget',
              } },
            { type: 'accounts', id: '4',
              attributes: {
                name: 'Salary', type: 'revenue', account_role: null,
                currency_code: 'USD', current_balance: '0.00', active: true, notes: '',
              } },
          ],
          meta: { pagination: { total: 4, count: 4, per_page: 100, current_page: 1, total_pages: 1 } },
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ message: `Route ${req.method} ${req.url} not found` }));
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
  console.log('\n[Phase A 只读扩展单测] firefly-iii-bridge-status(bearer SDK 第 9 用户 + 跨入理财域 + JSON:API 扁平化)\n');

  // 1. env 默认 + override
  t('env 默认 + override + ffConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.ffConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:8080');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_FIREFLY_URL: 'http://firefly.local:9000',
      PRISIR_FIREFLY_API_KEY: 'ff_pat_demo',
    });
    const c2 = m2.ffConfig();
    assertEq(c2.baseUrl_(), 'http://firefly.local:9000');
    assertEq(c2.token_(), 'ff_pat_demo');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchUser 无 token → ok=false
  await ta('fetchUser 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchUser();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchAccounts 无 token → ok=false
  await ta('fetchAccounts 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchAccounts();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_FIREFLY_URL: 'http://127.0.0.1:1',
      PRISIR_FIREFLY_API_KEY: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 复用验证 + describeAuth
  await ta('SDK 复用验证:httpGet + describeAuth SDK 在场(JSON:API 解析)', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    const cfgEmpty = sdk.makeConfig({ baseUrl: 'http://x:8080', token: '' });
    assertEq(sdk.describeAuth(cfgEmpty).has_token, false);
  });

  // 7. flattenAttrs 单元测试(JSON:API envelope 扁平化)
  t('flattenAttrs 单元:单条 + 列表 + 空 + 非 JSON:API', () => {
    const m = loadModule({});
    // 单条
    const single = m.flattenAttrs({
      data: { type: 'users', id: '1', attributes: { email: 'a@b.c', role: 'owner' } },
    });
    assertEq(single.email, 'a@b.c');
    assertEq(single.role, 'owner');
    assertEq(single._id, '1');
    assertEq(single._type, 'users');
    // 列表
    const list = m.flattenAttrs({
      data: [
        { type: 'accounts', id: '1', attributes: { name: 'X' } },
        { type: 'accounts', id: '2', attributes: { name: 'Y' } },
      ],
    });
    assertEq(list.length, 2);
    assertEq(list[0].name, 'X');
    assertEq(list[0]._id, '1');
    // 非 JSON:API envelope
    assertEq(m.flattenAttrs(null), null);
    assertEq(m.flattenAttrs({ foo: 'bar' }), null);
  });

  // 8. fetchAccounts 解析健壮性(不可达场景)
  await ta('fetchAccounts 不可达场景 → ok=false', async () => {
    const m = loadModule({
      PRISIR_FIREFLY_URL: 'http://127.0.0.1:1',
      PRISIR_FIREFLY_API_KEY: 'demo',
    });
    const r = await m.fetchAccounts();
    assertEq(r.ok, false);
  });

  // ── E2E(mock Firefly III server 模拟 Bearer + REST GET + JSON:API)──
  if (E2E) {
    console.log('\n[E2E] Mock Firefly III server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN.substring(0, 12)}... + GET + application/vnd.api+json\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_FIREFLY_URL: `http://127.0.0.1:${port}`,
        PRISIR_FIREFLY_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + version + driver + php_version', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, '6.1.7');
        assertEq(r.api_version, '2.0.0');
        assertEq(r.driver, 'sqlite');
        assertEq(r.php_version, '8.3.0');
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchUser → email + role=owner + is_admin=true', async () => {
        const r = await m.fetchUser();
        assertEq(r.ok, true);
        assertEq(r.user.id, '1');
        assertEq(r.user.email, 'me@example.com');
        assertEq(r.user.role, 'owner');
        assertEq(r.user.is_admin, true);
        assertEq(r.user.blocked, false);
      });

      await ta('E2E fetchAccounts → 4 accounts(asset/expense/revenue)+ balance', async () => {
        const r = await m.fetchAccounts();
        assertEq(r.ok, true);
        assertEq(r.accounts.length, 4);
        assertEq(r.total, 4);
        assertEq(r.accounts[0].name, 'Checking');
        assertEq(r.accounts[0].account_type, 'asset');
        assertEq(r.accounts[0].current_balance, '1000.00');
        assertEq(r.accounts[1].name, 'Savings');
        assertEq(r.accounts[2].name, 'Groceries');
        assertEq(r.accounts[2].account_type, 'expense');
        assertEq(r.accounts[3].account_type, 'revenue');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_FIREFLY_URL: `http://127.0.0.1:${port}`,
        PRISIR_FIREFLY_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchAccounts();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Firefly III server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();