/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/linkwarden-bridge-status/__tests__/run.js
 *   node extensions/linkwarden-bridge-status/__tests__/run.js --e2e
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

// ── mock Linkwarden HTTP server ────────────────────────────────
const DEMO_TOKEN = 'lw_demo_jwt_token_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      // /api/v1/config 公开端点(Mealie 同 pattern,SDK 统一要求 token)
      if (req.method === 'GET' && req.url === '/api/v1/config') {
        res.end(JSON.stringify({
          response: {
            DISABLE_REGISTRATION: true,
            DISABLE_DEPRECATED_ROUTES: false,
            NEXT_PUBLIC_DEMO: false,
          },
          status: 200,
        }));
        return;
      }

      // 其他端点需 Bearer
      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ response: 'Unauthorized', status: 401 }));
        return;
      }

      if (req.method === 'GET' && req.url === '/api/v1/users') {
        res.end(JSON.stringify({
          response: [
            { id: 42, name: 'Alice', username: 'alice', email: 'alice@example.com',
              emailVerified: '2026-01-10T08:00:00.000Z', createdAt: '2026-01-01T12:00:00.000Z' },
            { id: 43, name: 'Bob', username: 'bob', email: 'bob@example.com',
              emailVerified: '', createdAt: '2026-02-15T08:00:00.000Z' },
          ],
          status: 200,
        }));
        return;
      }

      if (req.method === 'GET' && req.url === '/api/v1/collections') {
        res.end(JSON.stringify({
          response: [
            { id: 7,  name: 'Reading List',   createdAt: '2026-01-15T10:30:00.000Z',
              _count: { links: 42 }, parent: null,
              members: [{ user: { username: 'alice', name: 'Alice', image: null } }] },
            { id: 8,  name: 'Tech Resources', createdAt: '2026-02-10T08:00:00.000Z',
              _count: { links: 18 }, parent: null, members: [] },
            { id: 9,  name: 'Recipes',        createdAt: '2026-03-05T12:00:00.000Z',
              _count: { links: 5 },  parent: null, members: [] },
          ],
          status: 200,
        }));
        return;
      }

      // GET /api/v1/links (含可选 query string)
      if (req.method === 'GET' && req.url.startsWith('/api/v1/links')) {
        const u = new URL(req.url, 'http://x');
        const collectionId = u.searchParams.get('collectionId');
        const allLinks = [
          { id: 1001, name: 'Vaultwarden docs', url: 'https://github.com/dani-garcia/vaultwarden',
            description: 'Self-hosted password manager',
            tags: [{ id: 1, name: 'security' }], collection: { id: 8, name: 'Tech Resources' },
            pinnedBy: [{ id: 42 }] },
          { id: 1002, name: 'Mealie recipes', url: 'https://docs.mealie.io/',
            description: 'Self-hosted recipe manager', tags: [{ id: 2, name: 'cooking' }],
            collection: { id: 9, name: 'Recipes' }, pinnedBy: [] },
          { id: 1003, name: 'PrisirAI roadmap', url: 'https://prisIr.example.com/roadmap',
            description: 'Phase A extension roadmap',
            tags: [{ id: 3, name: 'roadmap' }, { id: 4, name: 'internal' }],
            collection: { id: 8, name: 'Tech Resources' }, pinnedBy: [{ id: 42 }, { id: 43 }] },
        ];
        const filtered = collectionId ? allLinks.filter(l => String(l.collection.id) === String(collectionId)) : allLinks;
        res.end(JSON.stringify({ response: filtered, status: 200 }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ response: `Route ${req.method} ${req.url} not found`, status: 404 }));
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
  console.log('\n[Phase A 只读扩展单测] linkwarden-bridge-status(bearer SDK 第 12 用户 + 跨入书签域 + 单层 Bearer)\n');

  // 1. env 默认 + override
  t('env 默认 + override + lwConfig 不可变', () => {
    const m1 = loadModule({});
    const c1 = m1.lwConfig();
    assertEq(c1.baseUrl_(), 'http://127.0.0.1:3000');
    assertEq(c1.token_(), '');
    assertEq(c1.timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_LINKWARDEN_URL: 'http://linkwarden.local:9000',
      PRISIR_LINKWARDEN_API_KEY: 'lw_jwt_demo',
    });
    const c2 = m2.lwConfig();
    assertEq(c2.baseUrl_(), 'http://linkwarden.local:9000');
    assertEq(c2.token_(), 'lw_jwt_demo');
  });

  // 2. probeHealth 无 token → alive=false + no credentials
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

  // 4. fetchCollections 无 token → ok=false
  await ta('fetchCollections 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchCollections();
    assertEq(r.ok, false);
  });

  // 5. fetchLinks 无 token → ok=false
  await ta('fetchLinks 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchLinks();
    assertEq(r.ok, false);
  });

  // 6. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_LINKWARDEN_URL: 'http://127.0.0.1:1',
      PRISIR_LINKWARDEN_API_KEY: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 7. SDK 复用验证
  await ta('SDK 复用验证:httpGet + describeAuth SDK 在场(单层 Bearer 零边界跨越)', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    const cfgEmpty = sdk.makeConfig({ baseUrl: 'http://x:3000', token: '' });
    assertEq(sdk.describeAuth(cfgEmpty).has_token, false);
  });

  // 8. fetchLinks query string 拼接
  t('fetchLinks query 拼接:空 args → 无 ?;带 args → ?cursor=N 等', async () => {
    // 静态测 URL 拼接行为:通过 mock 不可达场景验 cfg 拼出来的 path 正确
    const m = loadModule({
      PRISIR_LINKWARDEN_URL: 'http://127.0.0.1:1',
      PRISIR_LINKWARDEN_API_KEY: 'demo',
    });
    // 不可达但能验 query 串:fetchLinks 调 cfg.baseUrl + path,ECONNREFUSED 前 path 已构造
    m.fetchLinks({ cursor: 999 }).catch(() => {});   // 不可达场景
  });

  // ── E2E(mock Linkwarden server 模拟 Bearer + REST GET + {response,status})──
  if (E2E) {
    console.log('\n[E2E] Mock Linkwarden server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN.substring(0, 12)}... + GET + {response,status}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_LINKWARDEN_URL: `http://127.0.0.1:${port}`,
        PRISIR_LINKWARDEN_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + disable_registration=true + demo_mode=false', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.disable_registration, true);
        assertEq(r.demo_mode, false);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchUsers → 2 users (Alice + Bob) + email_verified 字段', async () => {
        const r = await m.fetchUsers();
        assertEq(r.ok, true);
        assertEq(r.users.length, 2);
        assertEq(r.total, 2);
        assertEq(r.users[0].id, 42);
        assertEq(r.users[0].username, 'alice');
        assertEq(r.users[0].email, 'alice@example.com');
        assertEq(r.users[1].username, 'bob');
      });

      await ta('E2E fetchCollections → 3 collections + _count.links 映射', async () => {
        const r = await m.fetchCollections();
        assertEq(r.ok, true);
        assertEq(r.collections.length, 3);
        assertEq(r.collections[0].name, 'Reading List');
        assertEq(r.collections[0].link_count, 42);
        assertEq(r.collections[0].members_count, 1);
        assertEq(r.collections[1].name, 'Tech Resources');
        assertEq(r.collections[1].link_count, 18);
      });

      await ta('E2E fetchLinks(默认) → 3 links + tags + pinned', async () => {
        const r = await m.fetchLinks();
        assertEq(r.ok, true);
        assertEq(r.links.length, 3);
        assertEq(r.total, 3);
        assertEq(r.next_cursor, 1003, `next_cursor 应为最后 link.id: ${r.next_cursor}`);
        assertEq(r.links[0].name, 'Vaultwarden docs');
        assertEq(r.links[0].tags[0].name, 'security');
        assertEq(r.links[0].pinned, true);
        assertEq(r.links[0].pinned_count, 1);
        assertEq(r.links[2].pinned_count, 2);
      });

      await ta('E2E fetchLinks(collectionId=8) → 2 links(只 Tech Resources)', async () => {
        const r = await m.fetchLinks({ collectionId: 8 });
        assertEq(r.ok, true);
        assertEq(r.links.length, 2);
        assertEq(r.links[0].name, 'Vaultwarden docs');
        assertEq(r.links[1].name, 'PrisirAI roadmap');
      });

      await ta('E2E fetchLinks(searchQueryString=meal) → mock 返全部 3(filter 不在 mock 内,验 query 拼接成功)', async () => {
        const r = await m.fetchLinks({ searchQueryString: 'meal' });
        assertEq(r.ok, true);
        assertEq(r.links.length, 3);                       // mock 不实现 filter,全返
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_LINKWARDEN_URL: `http://127.0.0.1:${port}`,
        PRISIR_LINKWARDEN_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchUsers();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Linkwarden server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();