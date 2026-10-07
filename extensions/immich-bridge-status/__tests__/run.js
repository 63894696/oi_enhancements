/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/immich-bridge-status/__tests__/run.js
 *   node extensions/immich-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + mode='apiKey' + 嵌套解析)
 *   - 加 --e2e 起临时 mock Immich HTTP server,验证 x-api-key 自定义鉴权 + 嵌套 albums/assets 结构
 *
 * Phase C (2026-10-07): Immich 是 custom-auth SDK mode='apiKey' 第二个用户(前 Komga)
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

// ── mock Immich HTTP server ────────────────────────────────
const DEMO_API_KEY = 'imx_demo1234567890abcdef';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const apiKey = req.headers['x-api-key'] || req.headers['X-API-Key'];

      if (apiKey !== DEMO_API_KEY) {
        res.statusCode = 401;
        res.end(JSON.stringify({ message: 'Invalid API key', error: 'Unauthorized', statusCode: 401 }));
        return;
      }

      if (req.url === '/api/server/ping') {
        // public 端点,无需鉴权
        res.end(JSON.stringify({ res: 'pong' }));
      } else if (req.url === '/api/server/version') {
        res.end(JSON.stringify({ version: 'v1.111.0', build: 'main', repository: 'immich-app/immich' }));
      } else if (req.url.startsWith('/api/albums')) {
        res.end(JSON.stringify({
          albums: [
            { id: 'alb-1', albumName: 'Vacation 2025', assetCount: 23, createdAt: '2025-07-01T00:00:00Z', shared: false },
            { id: 'alb-2', albumName: 'Family Photos',  assetCount: 145, createdAt: '2024-12-15T00:00:00Z', shared: true },
            { id: 'alb-3', albumName: 'Work Screenshots', assetCount: 67, createdAt: '2025-09-10T00:00:00Z', shared: false },
          ],
        }));
      } else if (req.url.startsWith('/api/assets')) {
        const u = new URL(req.url, 'http://localhost');
        const take = Number(u.searchParams.get('take') || 20);
        const allAssets = [
          { id: 'a-1', type: 'IMAGE', originalFileName: 'sunset.jpg',        isFavorite: true,  fileCreatedAt: '2025-10-01T10:00:00Z', duration: '' },
          { id: 'a-2', type: 'IMAGE', originalFileName: 'mountain.png',      isFavorite: false, fileCreatedAt: '2025-09-30T08:30:00Z', duration: '' },
          { id: 'a-3', type: 'VIDEO', originalFileName: 'concert.mp4',       isFavorite: false, fileCreatedAt: '2025-09-28T19:45:00Z', duration: '00:03:42' },
        ];
        res.end(JSON.stringify({
          assets: allAssets.slice(0, take),
          total: allAssets.length,
        }));
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
  console.log('\n[Phase A 只读扩展单测] immich-bridge-status(Phase C SDK mode=\'apiKey\' 第二个)\n');

  // 1. imxConfig 默认 + env override
  t('imxConfig 默认 + env override 切 + mode 锁 apiKey', () => {
    const m1 = loadModule({});
    const c1 = m1.imxConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:2283');
    assertEq(c1.mode(), 'apiKey');
    assertEq(c1.key(), '');
    const m2 = loadModule({
      PRISIR_IMMICH_URL: 'http://imx.local:9000',
      PRISIR_IMMICH_API_KEY: 'k-123',
    });
    const c2 = m2.imxConfig();
    assertEq(c2.baseUrl(), 'http://imx.local:9000');
    assertEq(c2.mode(), 'apiKey');
    assertEq(c2.key(), 'k-123');
  });

  // 2. probeHealth 无 Key → alive=false
  await ta('probeHealth 无 Key → alive=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchAlbums 无 Key → ok=false
  await ta('fetchAlbums 无 Key → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchAlbums();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchAssets 无 Key → ok=false
  await ta('fetchAssets 无 Key → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchAssets();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → alive=false + ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_IMMICH_URL: 'http://127.0.0.1:1',
      PRISIR_IMMICH_API_KEY: 'dummy',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK 直测:apiKeyHeader 大小写不敏感
  t('SDK apiKeyHeader 直接构造 X-API-Key header', () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const h1 = sdk.apiKeyHeader('k-123');
    assertEq(h1, { 'X-API-Key': 'k-123' });
    const h2 = sdk.apiKeyHeader('');
    assertEq(h2, {});
  });

  // 7. SDK 直测:mode='apiKey' 空 key 早退
  await ta('SDK mode=\'apiKey\' 空 key 早退', async () => {
    const sdk = require('../../_scaffold/custom-auth-client.js');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:2283', mode: 'apiKey', key: '' });
    const r = await sdk.httpGet({ config: cfg, path: '/api/server/version' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error), `error 不对: ${r.error}`);
  });

  // ── E2E(mock Immich server 模拟 x-api-key + 嵌套结构)──────
  if (E2E) {
    console.log('\n[E2E] Mock Immich server @ 127.0.0.1:<random>');
    console.log(`  鉴权: x-api-key=${DEMO_API_KEY}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_IMMICH_URL: `http://127.0.0.1:${port}`,
        PRISIR_IMMICH_API_KEY: DEMO_API_KEY,
      });

      await ta('E2E probeHealth → alive + version=v1.111.0', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.version, 'v1.111.0');
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchAlbums → 3 albums + 嵌套解析', async () => {
        const r = await m.fetchAlbums();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.albums.length, 3);
        assertEq(r.albums[0].name, 'Vacation 2025');
        assertEq(r.albums[0].asset_count, 23);
        assertEq(r.albums[1].is_shared, true);
        assertEq(r.albums[2].name, 'Work Screenshots');
      });

      await ta('E2E fetchAssets → 3 assets + IMAGE/VIDEO 类型', async () => {
        const r = await m.fetchAssets();
        assertEq(r.ok, true);
        assertEq(r.assets.length, 3);
        assertEq(r.assets[0].type, 'IMAGE');
        assertEq(r.assets[0].is_favorite, true);
        assertEq(r.assets[0].original_file_name, 'sunset.jpg');
        assertEq(r.assets[2].type, 'VIDEO');
        assertEq(r.assets[2].duration, '00:03:42');
      });

      // 错 Key → 401
      const mWrong = loadModule({
        PRISIR_IMMICH_URL: `http://127.0.0.1:${port}`,
        PRISIR_IMMICH_API_KEY: 'wrong-key',
      });

      await ta('E2E 错 Key → 401 → ok=false + last_error', async () => {
        const r = await mWrong.probeHealth();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401|Unauthorized/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Immich server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();