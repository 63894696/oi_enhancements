/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/miniflux-bridge-status/__tests__/run.js
 *   node extensions/miniflux-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + mode='custom' + 命令实现)
 *   - 加 --e2e 起临时 mock Miniflux HTTP server,验证 X-Auth-Token 自定义鉴权 + 401
 *
 * Phase C (2026-10-07): Miniflux 是 custom-auth-client SDK mode='custom' 第一个使用案例
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

// ── mock Miniflux HTTP server ────────────────────────────
const DEMO_TOKEN = 'mfx_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const token = req.headers['x-auth-token'];

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error_message: 'Unauthorized' }));
        return;
      }

      if (req.url === '/v1/me') {
        res.end(JSON.stringify({
          id: 1,
          username: 'demo',
          is_admin: true,
          theme: 'system_serif',
          language: 'en_US',
          entries_per_page: 100,
        }));
      } else if (req.url.startsWith('/v1/feeds')) {
        res.end(JSON.stringify([
          {
            id: 1, user_id: 1,
            title: 'Hacker News',
            site_url: 'https://news.ycombinator.com',
            feed_url: 'https://news.ycombinator.com/rss',
            disabled: false,
            parsing_error_count: 0,
            category: { id: 1, user_id: 1, title: 'Tech' },
          },
          {
            id: 2, user_id: 1,
            title: 'LWN.net',
            site_url: 'https://lwn.net',
            feed_url: 'https://lwn.net/headlines/rss',
            disabled: false,
            parsing_error_count: 0,
            category: { id: 1, user_id: 1, title: 'Tech' },
          },
        ]));
      } else if (req.url.startsWith('/v1/entries')) {
        const u = new URL(req.url, 'http://localhost');
        const limit = Number(u.searchParams.get('limit') || 20);
        const status = u.searchParams.get('status') || 'unread';
        const search = (u.searchParams.get('search') || '').toLowerCase();
        const all = [
          { id: 101, feed_id: 1, title: 'Show HN: Miniflux fork', url: 'https://h.example/101', author: 'alice', status: 'unread', starred: false, published_at: '2026-10-01T12:00:00Z', reading_time: 5 },
          { id: 102, feed_id: 1, title: 'Show HN: RSS parser',   url: 'https://h.example/102', author: 'bob',   status: 'unread', starred: true,  published_at: '2026-10-02T12:00:00Z', reading_time: 8 },
          { id: 103, feed_id: 2, title: 'Kernel 6.10 released',  url: 'https://l.example/103', author: 'lwn',   status: 'read',   starred: false, published_at: '2026-09-29T12:00:00Z', reading_time: 12 },
        ];
        let filtered = status === 'all' ? all : all.filter((e) => e.status === status);
        if (search) filtered = filtered.filter((e) => e.title.toLowerCase().includes(search));
        res.end(JSON.stringify({
          total: filtered.length,
          entries: filtered.slice(0, limit),
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error_message: 'not found' }));
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
  console.log('\n[Phase A 只读扩展单测] miniflux-bridge-status(Phase C SDK mode=\'custom\' 验证)\n');

  // 1. mfxConfig 默认 + env override
  t('mfxConfig 默认 + env override 切 + mode 锁 custom', () => {
    const m1 = loadModule({});
    const c1 = m1.mfxConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:8080');
    assertEq(c1.mode(), 'custom');
    assertEq(c1.customHeader(), 'X-Auth-Token');
    assertEq(c1.customToken(), '');
    const m2 = loadModule({
      PRISIR_MINIFLUX_URL: 'http://mfx.local:9000',
      PRISIR_MINIFLUX_TOKEN: 'mytoken',
    });
    const c2 = m2.mfxConfig();
    assertEq(c2.baseUrl(), 'http://mfx.local:9000');
    assertEq(c2.customHeader(), 'X-Auth-Token');
    assertEq(c2.customToken(), 'mytoken');
  });

  // 2. probeHealth 无 token → alive=false
  await ta('probeHealth 无 token → alive=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchFeeds 无 token → ok=false
  await ta('fetchFeeds 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchFeeds();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchEntries 无 token → ok=false
  await ta('fetchEntries 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchEntries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_MINIFLUX_URL: 'http://127.0.0.1:1',
      PRISIR_MINIFLUX_TOKEN: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:mode='custom' 构造头
  t('SDK mode=\'custom\' 注入 X-Auth-Token 自定义头', () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({
      baseUrl: 'http://x:8080',
      mode: 'custom',
      customHeader: 'X-Auth-Token',
      customToken: 'tk-123',
    });
    assertEq(cfg.mode(), 'custom');
    assertEq(cfg.customHeader(), 'X-Auth-Token');
    assertEq(cfg.customToken(), 'tk-123');
    // describeAuth 应正确显示
    const desc = sdk.describeAuth(cfg);
    assertTrue(/X-Auth-Token/.test(desc), `describeAuth 不对: ${desc}`);
  });

  // 7. SDK 直测:mode='custom' 空 token + 空 header 早退
  await ta('SDK mode=\'custom\" 空 token + 空 header 早退', async () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:8080', mode: 'custom', customHeader: '', customToken: '' });
    const r = await sdk.httpGet({ config: cfg, path: '/v1/me' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error), `error 不对: ${r.error}`);
  });

  // ── E2E(mock Miniflux server 模拟 X-Auth-Token)──────────
  if (E2E) {
    console.log('\n[E2E] Mock Miniflux server @ 127.0.0.1:<random>');
    console.log(`  鉴权: X-Auth-Token=${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_MINIFLUX_URL: `http://127.0.0.1:${port}`,
        PRISIR_MINIFLUX_TOKEN: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + username=demo + is_admin=true', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.username, 'demo');
        assertEq(r.is_admin, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchFeeds → 2 feeds (Hacker News + LWN) + category=Tech', async () => {
        const r = await m.fetchFeeds();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.feeds.length, 2);
        assertEq(r.feeds[0].title, 'Hacker News');
        assertEq(r.feeds[0].category, 'Tech');
        assertEq(r.feeds[1].title, 'LWN.net');
      });

      await ta('E2E fetchEntries (status=unread 默认) → 2 entries', async () => {
        const r = await m.fetchEntries();
        assertEq(r.ok, true);
        assertEq(r.total, 2);
        assertEq(r.entries.length, 2);
        assertEq(r.entries[0].title, 'Show HN: Miniflux fork');
        assertEq(r.entries[1].starred, true);
      });

      await ta('E2E fetchEntries (status=read) → 1 entry + search 过滤', async () => {
        const r1 = await m.fetchEntries({ status: 'read' });
        assertEq(r1.total, 1);
        assertEq(r1.entries[0].title, 'Kernel 6.10 released');
        const r2 = await m.fetchEntries({ status: 'all', search: 'kernel' });
        assertEq(r2.entries.length, 1);
        assertEq(r2.entries[0].title, 'Kernel 6.10 released');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_MINIFLUX_URL: `http://127.0.0.1:${port}`,
        PRISIR_MINIFLUX_TOKEN: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + last_error', async () => {
        const r = await mWrong.probeHealth();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Miniflux server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();