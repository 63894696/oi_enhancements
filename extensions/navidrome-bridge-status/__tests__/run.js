/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/navidrome-bridge-status/__tests__/run.js
 *   node extensions/navidrome-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 7 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳 + SDK 鉴权派生
 *   - 加 --e2e 起临时 mock Subsonic HTTP server,验证 /ping /getNowPlaying /getLicense
 *
 * Subsonic 鉴权特性:
 *   - 客户端每次派新 salt,服务端用客户端发的 salt 算 md5(pass+salt) 验 token
 *   - 测试 mock server 用 'demo' 密码 + 客户端发的盐 (任何 salt 都接受) 重算验证
 *
 * Phase C(2026-10-07):Navidrome 重构为薄壳,Subsonic 协议细节移到 _scaffold/subsonic-client.js。
 * 测试主测扩展薄壳(env 注入 + 命令实现),SDK 单元被独立测。
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

// ── mock Subsonic HTTP server ────────────────────────────────
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

      if (url.pathname === '/rest/ping') {
        res.end(JSON.stringify({ 'subsonic-response': { status: 'ok', serverVersion: '0.54.5' } }));
      } else if (url.pathname === '/rest/getNowPlaying') {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'ok',
            nowPlaying: {
              entry: [{
                id: '1111',
                title: 'Apocalypse',
                album: 'Trench',
                artist: 'twenty one pilots',
                genre: 'Indie',
                year: 2018,
                minutesAgo: 1,
                playerId: 1,
                contentType: 'audio/flac',
                bitRate: 1100,
              }],
            },
          },
        }));
      } else if (url.pathname === '/rest/getLicense') {
        res.end(JSON.stringify({
          'subsonic-response': {
            status: 'ok',
            license: {
              valid: false,
              email: '',
              licenseExpires: '2099-12-31T00:00:00Z',
            },
            serverVersion: '0.54.5',
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
  // vm sandbox 内 require 用 module.createRequire(从 EXT_DIR 的 filename 锚)
  // 这样 '../_scaffold/subsonic-client' 自然走对的相对路径
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
  console.log('\n[Phase A 只读扩展单测] navidrome-bridge-status(Phase C SDK 重构版)\n');

  // 1. ndConfig 默认 + env override
  t('ndConfig 默认 + env override 切', () => {
    const m1 = loadModule({});
    const c1 = m1.ndConfig();
    assertEq(c1.baseUrl(), 'http://127.0.0.1:4533');
    assertEq(c1.user(), '');
    assertEq(c1.password(), '');
    const m2 = loadModule({
      PRISIR_NAVIDROME_URL: 'http://nd.local:9000',
      PRISIR_NAVIDROME_USER: 'u',
      PRISIR_NAVIDROME_PASS: 'p',
      PRISIR_NAVIDROME_CLIENT: 'test',
      PRISIR_NAVIDROME_API_V: '1.16.0',
    });
    const c2 = m2.ndConfig();
    assertEq(c2.baseUrl(), 'http://nd.local:9000');
    assertEq(c2.user(), 'u');
    assertEq(c2.password(), 'p');
    assertEq(c2.client(), 'test');
    assertEq(c2.apiV(), '1.16.0');
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
      PRISIR_NAVIDROME_URL: 'http://127.0.0.1:1',
      PRISIR_NAVIDROME_PASS: 'demo',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 6. SDK makeToken → md5(password + salt)
  t('SDK makeToken → md5(password + salt) 直通', () => {
    const sdk = require(path.resolve(__dirname, '..', '..', '_scaffold', 'subsonic-client.js'));
    const { salt, token } = sdk.makeToken('helloworld');
    assertEq(salt.length >= 12, true, 'salt too short');
    assertEq(token, crypto.createHash('md5').update('helloworld' + salt).digest('hex'));
  });

  // 7. SDK parseSubsonic status=failed + missing envelope
  t('SDK parseSubsonic 三层失败语义', () => {
    const sdk = require(path.resolve(__dirname, '..', '..', '_scaffold', 'subsonic-client.js'));
    const p1 = sdk.parseSubsonic({
      ok: true, status: 200, body: '', parsed: { 'subsonic-response': { status: 'failed', error: { code: 40, message: 'Wrong username' } } }, url: '', endpoint: 'ping',
    });
    assertEq(p1.ok, false);
    assertTrue(p1.last_error.includes('code=40'));
    assertTrue(p1.last_error.includes('Wrong username'));

    const p2 = sdk.parseSubsonic({ ok: true, status: 200, body: '', parsed: { foo: 'bar' }, url: '', endpoint: 'ping' });
    assertEq(p2.ok, false);
    assertTrue(/missing root envelope/i.test(p2.last_error));

    const p3 = sdk.parseSubsonic({ ok: false, status: 200, body: '', parsed: null, url: '', endpoint: 'ping' });
    assertEq(p3.ok, false);
    assertTrue(/non-JSON|missing/i.test(p3.last_error));
  });

  // ── E2E(mock Subsonic server 模拟 Navidrome)──────────────
  if (E2E) {
    console.log('\n[E2E] Mock Subsonic server @ 127.0.0.1:<random>');
    console.log('  demo creds: user=admin pass=demo (mock server 用每次客户端发的 salt 算 md5(pass+salt) 验 token)\n');
    const { server, port } = await startMockServer();
    const m = loadModule({
      PRISIR_NAVIDROME_URL: `http://127.0.0.1:${port}`,
      PRISIR_NAVIDROME_USER: 'admin',
      PRISIR_NAVIDROME_PASS: 'demo',
    });

    try {
      await ta('E2E probeHealth → alive + latency < 500ms', async () => {
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
        assertEq(r.now_playing[0].album, 'Trench');
        assertEq(r.now_playing[0].year, 2018);
        assertEq(r.now_playing[0].minutesAgo, 1);
      });

      await ta('E2E fetchLicense → valid=false + serverVersion=0.54.5', async () => {
        const r = await m.fetchLicense();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.valid, false);
        assertEq(r.serverVersion, '0.54.5');
        assertEq(r.licenseExpires, '2099-12-31T00:00:00Z');
      });

      await ta('E2E 错密码 → ok=false + last_error 含 Wrong username', async () => {
        const m2 = loadModule({
          PRISIR_NAVIDROME_URL: `http://127.0.0.1:${port}`,
          PRISIR_NAVIDROME_USER: 'admin',
          PRISIR_NAVIDROME_PASS: 'wrong',
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