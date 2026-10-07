/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/navidrome-bridge-status/__tests__/run.js
 *   node extensions/navidrome-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 8 单测用 vm sandbox 注入 SDK stub,测试不可达 / Subsonic 失败 / token 派生
 *   - 加 --e2e 起一个临时 mock Subsonic HTTP server,验证 /ping /getNowPlaying /getLicense
 *
 * Subsonic 鉴权特性:
 *   - 服务端每次派新 salt,客户端 md5(pass+salt) 算新 token
 *   - 测试 mock server 用 'demo' 密码 + 'demoSalt' 计算 token demoToken=md5('demodemoSalt')
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
// Subsonic 协议: 客户端用 salt+pass 算 token = md5(password + salt),
//                salt 是**客户端生成**(每次新随机)并随请求发到服务端,服务端验证
//                md5(pass+server'd salt) == token。
// 这里为了模拟真实行为,我们接受任何 salt,但用客户端发的 salt 来重新算 token 验证。
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

      // 鉴权:用客户端发的 salt 计算期望 token,比较客户端发的 token
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
  const requireFn = (id) => {
    if (id === '@prisir/extension-sdk') return sdkStub;
    try { return require(id); } catch (e) { throw e; }
  };
  const sandbox = {
    module: captured,
    exports: captured.exports,
    require: requireFn,
    console,
    process,
    Buffer,
    setTimeout,
    clearTimeout,
    setImmediate,
    clearImmediate,
    http,
    crypto,
  };
  // override env on the sandbox's process copy
  sandbox.process.env = { ...process.env, ...extraEnv };
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
  console.log('\n[Phase A 只读扩展单测] navidrome-bridge-status\n');

  // 1. env 默认值
  t('ndBaseUrl 默认 + env override 切', () => {
    const m1 = loadModule({});
    assertEq(m1.ndBaseUrl(), 'http://127.0.0.1:4533');
    const m2 = loadModule({ PRISIR_NAVIDROME_URL: 'http://nd.local:9000' });
    assertEq(m2.ndBaseUrl(), 'http://nd.local:9000');
  });

  // 2. token 派生 md5(password + salt)
  t('makeToken → md5(password + salt)', () => {
    const m = loadModule({});
    const { salt, token } = m.makeToken('helloworld');
    assertEq(salt.length >= 12, true, 'salt too short');
    assertEq(token, crypto.createHash('md5').update('helloworld' + salt).digest('hex'));
  });

  // 3. fetchNowPlaying 无 creds → ok=false + last_error 含 'no credentials'
  await ta('fetchNowPlaying 无 creds → ok=false + last_error 含 no credentials', async () => {
    const m = loadModule({});
    const r = await m.fetchNowPlaying();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. probeHealth 无 creds → alive=false + last_error
  await ta('probeHealth 无 creds → alive=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. httpGetSubsonic endpoint 不可达 → ok=false
  await ta('httpGetSubsonic 不可达 → ok=false + status=0', async () => {
    const m = loadModule({
      PRISIR_NAVIDROME_URL: 'http://127.0.0.1:1',
      PRISIR_NAVIDROME_PASS: 'demo',
    });
    const r = await m.httpGetSubsonic({ password: 'demo', endpoint: 'ping' });
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.error || ''), `error 不对: ${r.error}`);
  });

  // 6. parseSubsonic status='failed' → ok=false + last_error 含 code/msg
  t('parseSubsonic status=failed → ok=false + code 40', () => {
    const m = loadModule({});
    const p = m.parseSubsonic({
      ok: true,
      status: 200,
      body: '',
      parsed: { 'subsonic-response': { status: 'failed', error: { code: 40, message: 'Wrong username' } } },
      url: '',
      endpoint: 'ping',
    });
    assertEq(p.ok, false);
    assertTrue(p.last_error.includes('code=40'), `last_error 漏 code: ${p.last_error}`);
    assertTrue(p.last_error.includes('Wrong username'), `last_error 漏 msg: ${p.last_error}`);
  });

  // 7. parseSubsonic missing root envelope → ok=false
  t('parseSubsonic missing root envelope → ok=false', () => {
    const m = loadModule({});
    const p = m.parseSubsonic({
      ok: true, status: 200, body: '', parsed: { foo: 'bar' }, url: '', endpoint: 'ping',
    });
    assertEq(p.ok, false);
    assertTrue(/missing root envelope/i.test(p.last_error), `last_error 不对: ${p.last_error}`);
  });

  // 8. fetchLicense 无 creds → ok=false + last_error 含 'no credentials'
  await ta('fetchLicense 无 creds → ok=false + last_error', async () => {
    const m = loadModule({});
    // 显式无 password + 无 token,直接调 httpGetSubsonic 验证 no-credentials 早退
    const r = await m.httpGetSubsonic({ password: undefined, preToken: '', preSalt: '', endpoint: 'getLicense' });
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.error || ''), `error 不对: ${r.error}`);
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
      // DEBUG: print first request result
      console.error('  DEBUG ndBaseUrl=', m.ndBaseUrl(), 'ndUser=', m.ndUser(), 'ndPass=', m.ndPass() ? '***' : '(empty)');
      await ta('E2E probeHealth → alive + latency < 500ms', async () => {
        const r = await m.probeHealth();
        if (!r.ok) throw new Error(`probeHealth returned ${JSON.stringify(r)}`);
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
        assertEq(r.http_status, 200);
      }).catch(e => { console.error('  DEBUG probeHealth:', e.message); throw e; });

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