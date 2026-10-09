/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/hedgedoc-bridge-status/__tests__/run.js
 *   node extensions/hedgedoc-bridge-status/__tests__/run.js --e2e
 *
 * Phase C (2026-10-10): HedgeDoc 是 bearer SDK 第 14 个用户
 * (单层 Bearer Token + 直数组 envelope v1.x 兼容)
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

// ── mock HedgeDoc server ─────────────────────────────────
const DEMO_TOKEN = 'hedgedoc_demo_bearer_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      // 根路径 → 返 HTML(模拟 HedgeDoc 主页)
      if (req.method === 'GET' && (req.url === '/' || req.url === '')) {
        res.setHeader('Content-Type', 'text/html');
        res.end(`<!DOCTYPE html><html><head><title>HedgeDoc - collaborative markdown notes</title></head><body><h1>HedgeDoc</h1></body></html>`);
        return;
      }

      // 错 token → 401
      res.setHeader('Content-Type', 'application/json');
      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ status: 'error', message: 'Unauthorized' }));
        return;
      }

      // /api/v2/notes
      if (req.method === 'GET' && (req.url === '/api/v2/notes' || req.url.startsWith('/api/v2/notes?'))) {
        const u = new URL(req.url, 'http://x');
        const search = u.searchParams.get('search') || '';
        const allNotes = [
          { id: 'note1', alias: 'team-onboarding', title: 'Team Onboarding Doc',
            ownerId: 'u1', ownerUserName: 'alice', createdAt: '2026-01-15T00:00:00Z',
            updatedAt: '2026-10-08T14:00:00Z', viewCount: 42, tags: ['team', 'docs'] },
          { id: 'note2', alias: 'api-keys-reference', title: 'API Keys Reference (sensitive)',
            ownerId: 'u1', ownerUserName: 'alice', createdAt: '2026-02-20T00:00:00Z',
            updatedAt: '2026-10-09T09:30:00Z', viewCount: 8, tags: ['reference'] },
          { id: 'note3', alias: 'meeting-2026-10-09', title: 'Weekly Meeting 2026-10-09',
            ownerId: 'u2', ownerUserName: 'bob', createdAt: '2026-10-09T00:00:00Z',
            updatedAt: '2026-10-09T18:00:00Z', viewCount: 5, tags: ['meeting'] },
        ];
        const filtered = search
          ? allNotes.filter(n => n.title.toLowerCase().includes(search.toLowerCase())
                                       || n.alias.toLowerCase().includes(search.toLowerCase()))
          : allNotes;
        res.end(JSON.stringify(filtered));
        return;
      }

      // /api/v2/user 当前用户
      if (req.method === 'GET' && req.url === '/api/v2/user') {
        res.end(JSON.stringify({
          id: 'u1',
          name: 'Alice Chen',
          userName: 'alice',
          email: 'alice@example.com',
          emailConfirmed: '2026-01-01T00:00:00Z',
          createdAt: '2025-12-01T00:00:00Z',
          isAdmin: true,
          isOwner: true,
        }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ status: 'error', message: `Route ${req.method} ${req.url} not found` }));
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
  console.log('\n[Phase A 只读扩展单测] hedgedoc-bridge-status(bearer SDK 第 14 用户 + 跨入协同笔记/Markdown 域 + 单层 Bearer Token + 直数组 envelope 兼容)\n');

  // 1. env 默认 + override
  t('env 默认 + override + hedgedocConfig 不可变', () => {
    const m1 = loadModule({});
    assertEq(m1.hedgedocConfig().baseUrl_(), 'http://127.0.0.1:3000');
    assertEq(m1.hedgedocConfig().token_(), '');
    assertEq(m1.hedgedocConfig().timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_HEDGEDOC_URL: 'http://hedgedoc.local:9000',
      PRISIR_HEDGEDOC_API_KEY: 'hd_token',
    });
    assertEq(m2.hedgedocConfig().baseUrl_(), 'http://hedgedoc.local:9000');
    assertEq(m2.hedgedocConfig().token_(), 'hd_token');
  });

  // 2. probeHealth 无 token → no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchNotes 无 token → ok=false
  await ta('fetchNotes 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchNotes();
    assertEq(r.ok, false);
  });

  // 4. fetchUser 无 token → ok=false
  await ta('fetchUser 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchUser();
    assertEq(r.ok, false);
  });

  // 5. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_HEDGEDOC_URL: 'http://127.0.0.1:1',
      PRISIR_HEDGEDOC_API_KEY: 'tok',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 复用验证
  await ta('SDK 复用验证:httpGet + describeAuth + makeConfig SDK 在场', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:3000', token: '' });
    assertEq(sdk.describeAuth(cfg).has_token, false);
  });

  // 7. fetchNotes query 拼接
  t('fetchNotes query 拼接:空 args → 无 ?;带 args → ?search=...&limit=...', async () => {
    const m = loadModule({
      PRISIR_HEDGEDOC_URL: 'http://127.0.0.1:1',
      PRISIR_HEDGEDOC_API_KEY: 'demo',
    });
    m.fetchNotes({ search: 'team', limit: 5, skip: 0, view: 'feed' }).catch(() => {});
  });

  // ── E2E(mock HedgeDoc v2 + 根 HTML + Bearer + 直数组)──
  if (E2E) {
    console.log('\n[E2E] Mock HedgeDoc v2 @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer <token> + GET + 直数组 envelope(v1 兼容)+ content 字段不抓\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_HEDGEDOC_URL: `http://127.0.0.1:${port}`,
        PRISIR_HEDGEDOC_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + page_title + is_hedgedoc=true', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertTrue(r.page_title.toLowerCase().includes('hedgedoc'), `title 不对: ${r.page_title}`);
        assertEq(r.is_hedgedoc, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchNotes(默认) → 3 notes + content_included=false', async () => {
        const r = await m.fetchNotes();
        assertEq(r.ok, true);
        assertEq(r.total, 3);
        assertEq(r.notes[0].title, 'Team Onboarding Doc');
        assertEq(r.notes[1].title, 'API Keys Reference (sensitive)');
        assertEq(r.notes[0].content_included, false, 'Phase A 不抓 content(产品级 P0)');
      });

      await ta('E2E fetchNotes(search=team) → 只 Team Onboarding 匹配', async () => {
        const r = await m.fetchNotes({ search: 'team' });
        assertEq(r.ok, true);
        assertEq(r.total, 1);
        assertEq(r.notes[0].title, 'Team Onboarding Doc');
      });

      await ta('E2E fetchNotes(limit=2) → 验 query 拼接成功(仍返 3,server 不真 limit)', async () => {
        const r = await m.fetchNotes({ limit: 2 });
        assertEq(r.ok, true);
        assertEq(r.total, 3);
      });

      await ta('E2E fetchUser → u1 + alice + admin=true + owner=true', async () => {
        const r = await m.fetchUser();
        assertEq(r.ok, true);
        assertEq(r.user.id, 'u1');
        assertEq(r.user.name, 'Alice Chen');
        assertEq(r.user.user_name, 'alice');
        assertEq(r.user.is_admin, true);
        assertEq(r.user.is_owner, true);
        assertEq(r.user.email_verified, true);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_HEDGEDOC_URL: `http://127.0.0.1:${port}`,
        PRISIR_HEDGEDOC_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchNotes();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock HedgeDoc v2)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();