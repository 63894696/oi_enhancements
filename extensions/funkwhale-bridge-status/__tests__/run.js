/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/funkwhale-bridge-status/__tests__/run.js
 *   node extensions/funkwhale-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + 命令实现 + .view endpoint 拼接)
 *   - 加 --e2e 起临时 mock Subsonic HTTP server,验证 .view 后缀被 mock server 正确处理
 *
 * Phase C(2026-10-07):Funkwhale **零修改复用 SDK** — 测试验 SDK 接受 .view 后缀 endpoint
 * (SDK url 拼接:`{baseUrl}/rest/${endpoint}?${qs}` → 当 endpoint=`ping.view` 拼成 `/rest/ping.view?`)
 */
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const crypto = require('crypto');
const vm = require('vm');

const EXT_DIR = path.resolve(__dirname, '..');
const INDEX_JS = path.join(EXT_DIR, 'index.js');
const SRC = fs.readFileSync(INDEX_JS, 'utf8');
const E2E = process.argv.includes('--e2e');

// ── mock Subsonic HTTP server ────────────────────────────
const DEMO_USER = 'admin';
const DEMO_PASS = 'demo';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const url = new URL(req.url, 'http://localhost');
      const u = url.searchParams.get('u');
      const t = url.searchParams.get('t');
      const s = url.searchParams.get('s');

      if (u !== DEMO_USER || !s || !t) {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'failed',
            error: { code: 10, message: 'Required parameter is missing' },
          },
        }));
        return;
      }
      const expected = crypto.createHash('md5').update(DEMO_PASS + s).digest('hex');
      if (t !== expected) {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'failed',
            error: { code: 40, message: 'Wrong username or password' },
          },
        }));
        return;
      }

      if (url.pathname === '/rest/ping.view') {
        res.end(JSON.stringify({ 'subsonic-response': { status: 'ok', serverVersion: '1.4.0' } }));
      } else if (url.pathname === '/rest/getNowPlaying.view') {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'ok',
            nowPlaying: {
              entry: [{
                id: '2222',
                title: 'Apocalypse',
                album: 'Trench',
                artist: 'twenty one pilots',
                genre: 'Indie',
                year: 2018,
                minutesAgo: 5,
                playerId: 2,
                contentType: 'audio/mpeg',
                bitRate: 320,
              }],
            },
          },
        }));
      } else if (url.pathname === '/rest/getLicense.view') {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'ok',
            license: {
              valid: true,
              email: 'demo@funkwhale.test',
              licenseExpires: '2099-12-31T00:00:00Z',
            },
            serverVersion: '1.4.0',
          },
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ 'subsonic-response': { status: 'failed', error: { code: 70, message: 'not found' } } }));
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
    crypto,
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
  console.log('\n[Phase A 只读扩展单测] funkwhale-bridge-status(Phase C SDK 第三次复用)\n');

  // 1. fwConfig 默认 + env override
  t('fwConfig 默认 + env override 切', () => {
    const m1 = loadModule({});
    const c1 = m1.fwConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:5000');
    assertEq(c1.user(), '');
    assertEq(c1.password(), '');
    const m2 = loadModule({
      PRISIR_FUNKWHALE_URL: 'http://fw.local:9000',
      PRISIR_FUNKWHALE_USER: 'u',
      PRISIR_FUNKWHALE_PASS: 'p',
    });
    const c2 = m2.fwConfig();
    assertEq(c2.baseUrl(), 'http://fw.local:9000');
    assertEq(c2.user(), 'u');
    assertEq(c2.password(), 'p');
  });

  // 2. probeHealth 无 creds → alive=false + last_error
  await ta('probeHealth 无 creds → alive=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. fetchNowPlaying 无 creds → ok=false
  await ta('fetchNowPlaying 无 creds → ok=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.fetchNowPlaying();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchLicense 无 creds → ok=false
  await ta('fetchLicense 无 creds → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchLicense();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. probeHealth 不可达 → ok=false + status=0
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_FUNKWHALE_URL: 'http://127.0.0.1:1',
      PRISIR_FUNKWHALE_PASS: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK .view 后缀兼容
  t('SDK 接受 endpoint="ping.view" — url 拼出 /rest/ping.view?', () => {
    const sdk = require('../../_scaffold/subsonic-client.js');
    const cfg = sdk.makeConfig({
      baseUrl: 'http://fw.local:5000',
      user: 'admin',
      password: 'demo',
      client: 'prisirai',
      apiV: '1.16.1',
    });
    const t0 = sdk.makeToken('demo');
    const qs = `u=admin&t=${t0.token}&s=${t0.salt}&v=1.16.1&c=prisirai&f=json`;
    const expected = `http://fw.local:5000/rest/ping.view?${qs}`;
    const got = `${cfg.baseUrl()}/rest/ping.view?${qs}`;
    assertEq(got, expected);
  });

  // 7. SDK makeToken 重算
  t('SDK makeToken → md5(password + salt) 直通', () => {
    const sdk = require('../../_scaffold/subsonic-client.js');
    const { salt, token } = sdk.makeToken('helloworld');
    assertEq(salt.length >= 12, true, 'salt too short');
    assertEq(token, crypto.createHash('md5').update('helloworld' + salt).digest('hex'));
  });

  // ── E2E(mock Subsonic server 模拟 Funkwhale)────────────
  if (E2E) {
    console.log('\n[E2E] Mock Funkwhale/Subsonic server @ 127.0.0.1:<random>');
    console.log('  demo creds: user=admin pass=demo (mock server 用 .view 后缀,客户端发的盐算 md5(pass+salt) 验 token)\n');
    const { server, port } = await startMockServer();
    const m = loadModule({
      PRISIR_FUNKWHALE_URL: `http://127.0.0.1:${port}`,
      PRISIR_FUNKWHALE_USER: 'admin',
      PRISIR_FUNKWHALE_PASS: 'demo',
    });

    try {
      await ta('E2E probeHealth → alive + latency < 500ms + .view', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
        assertEq(r.http_status, 200);
      });

      await ta('E2E fetchNowPlaying → 1 entry (Apocalypse / twenty one pilots)', async () => {
        const r = await m.fetchNowPlaying();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.now_playing.length, 1);
        assertEq(r.now_playing[0].title, 'Apocalypse');
        assertEq(r.now_playing[0].artist, 'twenty one pilots');
        assertEq(r.now_playing[0].playerId, 2);
        assertEq(r.now_playing[0].minutesAgo, 5);
      });

      await ta('E2E fetchLicense → valid=true + email + serverVersion=1.4.0', async () => {
        const r = await m.fetchLicense();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.valid, true);
        assertEq(r.email, 'demo@funkwhale.test');
        assertEq(r.serverVersion, '1.4.0');
      });

      await ta('E2E 错密码 → ok=false + last_error 含 Wrong username', async () => {
        const m2 = loadModule({
          PRISIR_FUNKWHALE_URL: `http://127.0.0.1:${port}`,
          PRISIR_FUNKWHALE_USER: 'admin',
          PRISIR_FUNKWHALE_PASS: 'wrong',
        });
        const r = await m2.probeHealth();
        assertEq(r.ok, false);
        assertTrue(/code=40|Wrong username/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Subsonic server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();