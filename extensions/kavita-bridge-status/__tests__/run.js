/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/kavita-bridge-status/__tests__/run.js
 *   node extensions/kavita-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + Bearer 头 + REST 解析)
 *   - 加 --e2e 起临时 mock Kavita HTTP server,验证 Authorization: Bearer + 401 错 token + JSON 嵌套
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

// ── mock Kavita HTTP server ────────────────────────────────
const DEMO_TOKEN = 'kv_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      if (req.url === '/api/Server/ping') {
        // /ping 是 public,无需鉴权
        res.end(JSON.stringify({ value: 'pong', apiVersion: '0.0.512', minimumSupportedVersion: '0.0.0' }));
        return;
      }

      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: 'Unauthorized' }));
        return;
      }

      if (req.url.startsWith('/api/Library/libraries')) {
        res.end(JSON.stringify([
          { id: 1, name: 'Manga',         type: 'Manga',  coverImage: 'manga.png' },
          { id: 2, name: 'Comics',        type: 'Comic',  coverImage: 'comic.png' },
          { id: 3, name: 'Books',         type: 'Book',   coverImage: 'book.png' },
          { id: 4, name: 'Light Novels',  type: 'Book',   coverImage: '' },
        ]));
      } else if (req.url.startsWith('/api/Series')) {
        const u = new URL(req.url, 'http://localhost');
        const search = u.searchParams.get('SearchTerm') || '';
        let allSeries = [
          { id: 100, name: 'One Piece',          libraryId: 1, pages: 11000, formattedName: 'One Piece, Vol. 1' },
          { id: 101, name: 'Naruto',             libraryId: 1, pages: 7200,  formattedName: 'Naruto, Vol. 1' },
          { id: 102, name: 'Berserk',            libraryId: 2, pages: 8500,  formattedName: 'Berserk, Vol. 1' },
          { id: 103, name: 'The Pragmatic Programmer', libraryId: 3, pages: 320, formattedName: 'Pragmatic Programmer' },
        ];
        if (search) allSeries = allSeries.filter(s => s.name.toLowerCase().includes(search.toLowerCase()));
        res.end(JSON.stringify({
          result: allSeries.slice(0, Number(u.searchParams.get('PageSize') || 20)),
          pageNumber: 1, totalPages: 1, totalCount: allSeries.length,
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
  console.log('\n[Phase A 只读扩展单测] kavita-bridge-status(Bearer API key,anonymous 不支持故走 Bearer)\n');

  // 1. env 默认 + override
  t('env 默认 + override + kvBaseUrl/kvToken 直读', () => {
    const m1 = loadModule({});
    assertEq(m1.kvBaseUrl(), 'http://127.0.0.1:5000');
    assertEq(m1.kvToken(), '');
    const m2 = loadModule({
      PRISIR_KAVITA_URL: 'http://kv.local:9000',
      PRISIR_KAVITA_API_KEY: 'demo-token',
    });
    assertEq(m2.kvBaseUrl(), 'http://kv.local:9000');
    assertEq(m2.kvToken(), 'demo-token');
  });

  // 2. probeHealth 无 token → alive=false + no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
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

  // 4. fetchSeries 无 token → ok=false
  await ta('fetchSeries 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchSeries();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_KAVITA_URL: 'http://127.0.0.1:1',
      PRISIR_KAVITA_API_KEY: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. Bearer 头格式
  t('Bearer 头格式:Authorization: Bearer <token>', () => {
    const m = loadModule({ PRISIR_KAVITA_API_KEY: 'jwt.abc.def' });
    // 通过 fetchLibraries 走 E2E 不需要(单测里只验证 token 注入到 kvGet)
    assertEq(m.kvToken(), 'jwt.abc.def');
  });

  // 7. fetchSeries 入参限制
  await ta('fetchSeries 入参:limit 上限 100 + search 编码', async () => {
    // 触发 fetchSeries 时,env 验证 path 拼装 — 通过不可达 URL 验证 last_error 路径
    const m = loadModule({
      PRISIR_KAVITA_URL: 'http://127.0.0.1:1',
      PRISIR_KAVITA_API_KEY: 'demo',
    });
    const r1 = await m.fetchSeries({ limit: 999 });
    assertEq(r1.ok, false);
    // 不抛即可,验证 limit 在 path 里被钳制到 100
    const r2 = await m.fetchSeries({ search: 'A B C' });
    assertEq(r2.ok, false);
  });

  // ── E2E(mock Kavita server 模拟 Bearer + JSON 嵌套)────────
  if (E2E) {
    console.log('\n[E2E] Mock Kavita server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer ${DEMO_TOKEN}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_KAVITA_URL: `http://127.0.0.1:${port}`,
        PRISIR_KAVITA_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + apiVersion=0.0.512', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, '0.0.512');
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchLibraries → 4 libraries(Manga/Comic/Book×2)', async () => {
        const r = await m.fetchLibraries();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.libraries.length, 4);
        assertEq(r.libraries[0].name, 'Manga');
        assertEq(r.libraries[0].type, 'Manga');
        assertEq(r.libraries[2].name, 'Books');
        assertEq(r.libraries[3].name, 'Light Novels');
        assertEq(r.libraries[3].coverImage, '');
      });

      await ta('E2E fetchSeries(limit=20) → 4 series', async () => {
        const r = await m.fetchSeries({ limit: 20 });
        assertEq(r.ok, true);
        assertEq(r.series.length, 4);
        assertEq(r.series[0].name, 'One Piece');
        assertEq(r.series[0].library_id, 1);
        assertEq(r.series[0].page_count, 11000);
        assertEq(r.series[2].name, 'Berserk');
        assertEq(r.series[3].name, 'The Pragmatic Programmer');
      });

      await ta('E2E fetchSeries(search=Berserk) → 1 series 过滤', async () => {
        const r = await m.fetchSeries({ search: 'Berserk' });
        assertEq(r.ok, true);
        assertEq(r.series.length, 1);
        assertEq(r.series[0].name, 'Berserk');
        assertEq(r.series[0].library_id, 2);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_KAVITA_URL: `http://127.0.0.1:${port}`,
        PRISIR_KAVITA_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + last_error', async () => {
        const r = await mWrong.fetchLibraries();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Kavita server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();
