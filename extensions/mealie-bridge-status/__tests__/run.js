/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/mealie-bridge-status/__tests__/run.js
 *   node extensions/mealie-bridge-status/__tests__/run.js --e2e
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

// ── mock Mealie HTTP server ────────────────────────────────
const DEMO_TOKEN = 'ml_pat_demo1234567890abcdef1234567890abcdef12345678';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      // /api/app/about 是公开端点(Mealie 不检查 token)
      if (req.method === 'GET' && req.url === '/api/app/about') {
        res.end(JSON.stringify({
          production: true, version: 'v3.17.0', demoStatus: false,
          allowSignup: false, allowPasswordLogin: true,
          enableOidc: false, oidcProviderName: 'Mealie',
          tokenTime: 4320,
          defaultGroupSlug: null, defaultHouseholdSlug: null,
          allowedIframeHosts: [],
        }));
        return;
      }

      // 其他端点需 Bearer
      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ detail: 'Not Found' }));     // Mealie 鉴权失败返通用 detail
        return;
      }

      if (req.method === 'GET' && req.url === '/api/users/self') {
        res.end(JSON.stringify({
          id: 'u-uuid-1', username: 'chef', email: 'chef@example.com',
          fullName: 'Chef Owner', admin: true,
          groupId: 'g-uuid-1', householdId: 'h-uuid-1',
        }));
        return;
      }

      // GET /api/recipes?page=N&perPage=M
      if (req.method === 'GET' && req.url.startsWith('/api/recipes')) {
        const u = new URL(req.url, 'http://x');
        const page = Number(u.searchParams.get('page')) || 1;
        const perPage = Number(u.searchParams.get('perPage')) || 10;
        const allRecipes = [
          { slug: 'pasta-carbonara', name: 'Pasta Carbonara', description: '经典意式培根蛋意面,30 分钟搞定。', prepTime: 'PT10M', performTime: 'PT20M', totalTime: 'PT30M', recipeCategory: [{name: '主餐'}], tags: [{name: '意大利'},{name: '快手菜'}], rating: 5, lastMade: '2026-10-05T10:00:00Z', image: '' },
          { slug: 'kimchi-stew',    name: '泡菜汤',         description: '韩式泡菜豆腐锅,暖胃一锅出。',         prepTime: 'PT5M',  performTime: 'PT15M', totalTime: 'PT20M', recipeCategory: [{name: '汤'}],   tags: [{name: '韩式'}],         rating: 4, lastMade: '',                              image: '' },
          { slug: 'miso-soup',      name: '味噌汤',         description: '日式味噌汤,5 分钟搞定。',             prepTime: 'PT2M',  performTime: 'PT3M',  totalTime: 'PT5M',  recipeCategory: [{name: '汤'}],   tags: [{name: '日式'}],         rating: 5, lastMade: '2026-10-06T08:00:00Z', image: '' },
        ];
        const start = (page - 1) * perPage;
        const sliced = allRecipes.slice(start, start + perPage);
        res.end(JSON.stringify({
          page, per_page: perPage,
          total: allRecipes.length, total_pages: Math.ceil(allRecipes.length / perPage),
          data: sliced,
          next: (start + perPage < allRecipes.length) ? `?page=${page + 1}` : '',
          previous: page > 1 ? `?page=${page - 1}` : '',
        }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ detail: `Route ${req.method} ${req.url} not found` }));
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
  console.log('\n[Phase A 只读扩展单测] mealie-bridge-status(bearer SDK 第 10 用户 + 跨入料理域)\n');

  // 1. env 默认 + override
  t('env 默认 + override + mlConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.mlConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:9000');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_MEALIE_URL: 'http://mealie.local:9000',
      PRISIR_MEALIE_API_KEY: 'ml_pat_demo',
    });
    const c2 = m2.mlConfig();
    assertEq(c2.baseUrl_(), 'http://mealie.local:9000');
    assertEq(c2.token_(), 'ml_pat_demo');
  });

  // 2. probeHealth 无 token → 公开端点仍走,但需 placeholder 让 SDK 验证通过
  await ta('probeHealth 无 token → alive=false + no credentials(SDK 早退,需 token)', async () => {
    // SDK 设计:缺 token 早退。但 /api/app/about 是公开的 — 我们用临时 'public-probe-no-token-required'
    // 占位让 SDK 通过,服务端忽略。这是设计选择:用户若完全不配 token,probeHealth 会 SDK 早退。
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

  // 4. fetchRecipes 无 token → ok=false
  await ta('fetchRecipes 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchRecipes();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_MEALIE_URL: 'http://127.0.0.1:1',
      PRISIR_MEALIE_API_KEY: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 复用验证
  await ta('SDK 复用验证:httpGet + describeAuth SDK 在场(料理域零边界跨越)', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    const cfgEmpty = sdk.makeConfig({ baseUrl: 'http://x:9000', token: '' });
    assertEq(sdk.describeAuth(cfgEmpty).has_token, false);
  });

  // 7. fetchRecipes 入参钳制
  await ta('fetchRecipes 入参:page ≥ 1 + perPage 1-100 钳制', async () => {
    const m = loadModule({
      PRISIR_MEALIE_URL: 'http://127.0.0.1:1',
      PRISIR_MEALIE_API_KEY: 'demo',
    });
    // 不可达场景验证钳制不崩
    const r1 = await m.fetchRecipes({ page: 0 });
    assertEq(r1.ok, false);
    const r2 = await m.fetchRecipes({ perPage: 999 });
    assertEq(r2.ok, false);
    const r3 = await m.fetchRecipes({ page: -1 });
    assertEq(r3.ok, false);
  });

  // ── E2E(mock Mealie server 模拟 Bearer + REST GET + 公开端点 + 鉴权端点)──
  if (E2E) {
    console.log('\n[E2E] Mock Mealie server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN.substring(0, 12)}... + GET\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_MEALIE_URL: `http://127.0.0.1:${port}`,
        PRISIR_MEALIE_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + version=v3.17.0 + demo_status=false', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, 'v3.17.0');
        assertEq(r.demo_status, false);
        assertEq(r.allow_password_login, true);
        assertEq(r.token_time_minutes, 4320);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchUser → email + admin=true + household_id', async () => {
        const r = await m.fetchUser();
        assertEq(r.ok, true);
        assertEq(r.user.id, 'u-uuid-1');
        assertEq(r.user.username, 'chef');
        assertEq(r.user.email, 'chef@example.com');
        assertEq(r.user.admin, true);
        assertEq(r.user.household_id, 'h-uuid-1');
      });

      await ta('E2E fetchRecipes(默认) → 3 recipes + rating + tags + total_pages', async () => {
        const r = await m.fetchRecipes();
        assertEq(r.ok, true);
        assertEq(r.recipes.length, 3);
        assertEq(r.total, 3);
        assertEq(r.total_pages, 1);
        assertEq(r.recipes[0].slug, 'pasta-carbonara');
        assertEq(r.recipes[0].rating, 5);
        assertEq(r.recipes[0].tags.length, 2);
        assertEq(r.recipes[0].tags[0], '意大利');
        assertEq(r.recipes[0].recipe_category[0], '主餐');
        assertEq(r.recipes[2].slug, 'miso-soup');
      });

      await ta('E2E fetchRecipes(page=1, perPage=2) → 2 recipes + total_pages=2 + next 存在', async () => {
        const r = await m.fetchRecipes({ page: 1, perPage: 2 });
        assertEq(r.ok, true);
        assertEq(r.recipes.length, 2);
        assertEq(r.total, 3);
        assertEq(r.total_pages, 2);
        assertEq(r.next, '?page=2');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_MEALIE_URL: `http://127.0.0.1:${port}`,
        PRISIR_MEALIE_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchUser();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Mealie server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();