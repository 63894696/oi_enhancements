/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/audiobookshelf-bridge-status/__tests__/run.js
 *   node extensions/audiobookshelf-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 6 单测用 vm sandbox 注入 SDK stub,验证 Bearer token 鉴权 + 不可达 + env 覆盖
 *   - 加 --e2e 起临时 mock Audiobookshelf server,验证 healthcheck / libraries / sessions
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

// ── mock Audiobookshelf HTTP server ────────────────────────────
const DEMO_TOKEN = 'demoBearerTokenABC123';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');

      // Bearer token 校验
      if (!auth.startsWith('Bearer ') || auth.slice(7) !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: 'Unauthorized' }));
        return;
      }

      if (req.url === '/healthcheck') {
        res.end(JSON.stringify({ success: true }));
      } else if (req.url === '/api/libraries') {
        res.end(JSON.stringify([
          { id: 'lib_book_1', name: 'Books', mediaType: 'book', icon: 'book' },
          { id: 'lib_pod_1', name: 'Podcasts', mediaType: 'podcast', icon: 'pod' },
        ]));
      } else if (req.url === '/api/sessions') {
        res.end(JSON.stringify([
          {
            id: 'sess_1',
            userId: 'user_1',
            mediaType: 'book',
            currentResourceId: 'res_xyz',
            position: 1500,
            duration: 3600,
          },
        ]));
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
  const requireFn = (id) => {
    if (id === '@prisir/extension-sdk') return sdkStub;
    try { return require(id); } catch (e) { throw e; }
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
  console.log('\n[Phase A 只读扩展单测] audiobookshelf-bridge-status\n');

  // 1. env 默认值
  t('absBaseUrl 默认 + env override 切', () => {
    const m1 = loadModule({});
    assertEq(m1.absBaseUrl(), 'http://127.0.0.1:8181');
    const m2 = loadModule({ PRISIR_AUDIOBOOKSHELF_URL: 'http://abs.local:9999' });
    assertEq(m2.absBaseUrl(), 'http://abs.local:9999');
  });

  // 2. absToken env override
  t('absToken 默认空 + env 覆盖', () => {
    const m1 = loadModule({});
    assertEq(m1.absToken(), '');
    const m2 = loadModule({ PRISIR_AUDIOBOOKSHELF_TOKEN: 'mysecret' });
    assertEq(m2.absToken(), 'mysecret');
  });

  // 3. probeHealth 无 token → ok=false + last_error 含 'no credentials'
  await ta('probeHealth 无 token → ok=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
    assertTrue(/PRISIR_AUDIOBOOKSHELF_TOKEN/.test(r.last_error), `last_error 不指 env 变量: ${r.last_error}`);
  });

  // 4. fetchLibraries 无 token → ok=false + last_error
  await ta('fetchLibraries 无 token → ok=false', async () => {
    const m = loadModule({});
    const r = await m.fetchLibraries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_AUDIOBOOKSHELF_URL: 'http://127.0.0.1:1',
      PRISIR_AUDIOBOOKSHELF_TOKEN: 'mysecret',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. fetchSessions 无 token → ok=false
  await ta('fetchSessions 无 token → ok=false', async () => {
    const m = loadModule({});
    const r = await m.fetchSessions();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // ── E2E(mock Audiobookshelf server)─────────────────────
  if (E2E) {
    console.log('\n[E2E] Mock Audiobookshelf server @ 127.0.0.1:<random>');
    console.log('  demo creds: Bearer token = demoBearerTokenABC123\n');
    const { server, port } = await startMockServer();
    const m = loadModule({
      PRISIR_AUDIOBOOKSHELF_URL: `http://127.0.0.1:${port}`,
      PRISIR_AUDIOBOOKSHELF_TOKEN: DEMO_TOKEN,
    });

    try {
      await ta('E2E probeHealth → alive + latency < 500ms', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
        assertEq(r.http_status, 200);
      });

      await ta('E2E fetchLibraries → 2 lib (Books / Podcasts)', async () => {
        const r = await m.fetchLibraries();
        assertEq(r.ok, true);
        assertEq(r.libraries.length, 2);
        assertEq(r.libraries[0].name, 'Books');
        assertEq(r.libraries[0].mediaType, 'book');
        assertEq(r.libraries[1].name, 'Podcasts');
        assertEq(r.libraries[1].mediaType, 'podcast');
      });

      await ta('E2E fetchSessions → 1 session (res_xyz, position=1500)', async () => {
        const r = await m.fetchSessions();
        assertEq(r.ok, true);
        assertEq(r.sessions.length, 1);
        assertEq(r.sessions[0].userId, 'user_1');
        assertEq(r.sessions[0].currentResourceId, 'res_xyz');
        assertEq(r.sessions[0].position, 1500);
        assertEq(r.sessions[0].duration, 3600);
      });

      await ta('E2E 错 token → 401 + ok=false', async () => {
        const m2 = loadModule({
          PRISIR_AUDIOBOOKSHELF_URL: `http://127.0.0.1:${port}`,
          PRISIR_AUDIOBOOKSHELF_TOKEN: 'wrong-token',
        });
        const r = await m2.probeHealth();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Audiobookshelf server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();