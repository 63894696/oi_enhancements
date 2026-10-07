/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/plex-bridge-status/__tests__/run.js
 *   node extensions/plex-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + mode='custom' + Plex MediaContainer 解析)
 *   - 加 --e2e 起临时 mock Plex HTTP server,验证 X-Plex-Token + MediaContainer 嵌套结构
 *
 * Phase C (2026-10-07): Plex 是 custom-auth SDK mode='custom' 第三个使用案例
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

// ── mock Plex HTTP server ────────────────────────────────
const DEMO_TOKEN = 'plx_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const token = req.headers['x-plex-token'];

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: 'Unauthorized' }));
        return;
      }

      if (req.url === '/identity') {
        res.end(JSON.stringify({
          MediaContainer: {
            size: 1,
            version: '1.40.0.1234',
            machineIdentifier: 'abc123def456',
          },
        }));
      } else if (req.url.startsWith('/library/sections')) {
        res.end(JSON.stringify({
          MediaContainer: {
            size: 2,
            Directory: [
              { key: '1', title: 'Movies',  type: 'movie', agent: 'com.plexapp.agents.imdb',    scanner: 'Plex Movie Scanner', language: 'en' },
              { key: '2', title: 'TV Shows', type: 'show',  agent: 'com.plexapp.agents.thetvdb', scanner: 'Plex TV Series Scanner', language: 'en' },
            ],
          },
        }));
      } else if (req.url.startsWith('/library/recentlyAdded')) {
        res.end(JSON.stringify({
          MediaContainer: {
            size: 3,
            Metadata: [
              { title: 'Inception',                type: 'movie', year: 2010, librarySectionTitle: 'Movies',  rating: 8.8, addedAt: 1728000000 },
              { title: 'Breaking Bad S01E01',     type: 'episode', year: 2008, librarySectionTitle: 'TV Shows', rating: 9.0, addedAt: 1727910000 },
              { title: 'The Matrix',              type: 'movie', year: 1999, librarySectionTitle: 'Movies',  rating: 8.7, addedAt: 1727820000 },
            ],
          },
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
  console.log('\n[Phase A 只读扩展单测] plex-bridge-status(Phase C SDK mode=\'custom\' 第三个)\n');

  // 1. pxConfig 默认 + env override
  t('pxConfig 默认 + env override 切 + mode 锁 custom', () => {
    const m1 = loadModule({});
    const c1 = m1.pxConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:32400');
    assertEq(c1.mode(), 'custom');
    assertEq(c1.customHeader(), 'X-Plex-Token');
    assertEq(c1.customToken(), '');
    const m2 = loadModule({
      PRISIR_PLEX_URL: 'http://px.local:9000',
      PRISIR_PLEX_TOKEN: 'mytoken',
    });
    const c2 = m2.pxConfig();
    assertEq(c2.baseUrl(), 'http://px.local:9000');
    assertEq(c2.customHeader(), 'X-Plex-Token');
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

  // 3. fetchLibraries 无 token → ok=false
  await ta('fetchLibraries 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchLibraries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchRecent 无 token → ok=false
  await ta('fetchRecent 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchRecent();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_PLEX_URL: 'http://127.0.0.1:1',
      PRISIR_PLEX_TOKEN: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:describeAuth 显示自定义 header
  t('SDK mode=\'custom\' describeAuth 显示 X-Plex-Token', () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({
      baseUrl: 'http://px:32400',
      mode: 'custom',
      customHeader: 'X-Plex-Token',
      customToken: 'tk-123',
    });
    const desc = sdk.describeAuth(cfg);
    assertTrue(/X-Plex-Token/.test(desc), `describeAuth 不对: ${desc}`);
    assertTrue(/set/.test(desc), `describeAuth 应显示已设置: ${desc}`);
  });

  // 7. SDK 直测:mode='custom' 空 token 早退
  await ta('SDK mode=\'custom\' 空 token + 空 header 早退', async () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:32400', mode: 'custom', customHeader: '', customToken: '' });
    const r = await sdk.httpGet({ config: cfg, path: '/identity' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error), `error 不对: ${r.error}`);
  });

  // ── E2E(mock Plex server 模拟 X-Plex-Token + MediaContainer 嵌套)──
  if (E2E) {
    console.log('\n[E2E] Mock Plex server @ 127.0.0.1:<random>');
    console.log(`  鉴权: X-Plex-Token=${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_PLEX_URL: `http://127.0.0.1:${port}`,
        PRISIR_PLEX_TOKEN: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + version=1.40.0.1234 + machineId', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, '1.40.0.1234');
        assertEq(r.machine_identifier, 'abc123def456');
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchLibraries → 2 libraries (Movies + TV Shows) + 类型映射', async () => {
        const r = await m.fetchLibraries();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.libraries.length, 2);
        assertEq(r.libraries[0].title, 'Movies');
        assertEq(r.libraries[0].type, 'movie');
        assertEq(r.libraries[0].agent, 'com.plexapp.agents.imdb');
        assertEq(r.libraries[1].title, 'TV Shows');
        assertEq(r.libraries[1].type, 'show');
      });

      await ta('E2E fetchRecent → 3 items + 影视/剧集分类', async () => {
        const r = await m.fetchRecent();
        assertEq(r.ok, true);
        assertEq(r.items.length, 3);
        assertEq(r.items[0].title, 'Inception');
        assertEq(r.items[0].type, 'movie');
        assertEq(r.items[0].year, 2010);
        assertEq(r.items[0].library, 'Movies');
        assertEq(r.items[1].type, 'episode');
        assertEq(r.items[1].library, 'TV Shows');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_PLEX_URL: `http://127.0.0.1:${port}`,
        PRISIR_PLEX_TOKEN: 'wrong-token',
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
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Plex server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();