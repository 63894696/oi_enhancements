/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测
 *
 * 跑法:
 *   node extensions/yesplaymusic-bridge-status/__tests__/run.js
 *   node extensions/yesplaymusic-bridge-status/__tests__/run.js --e2e   # 加 mock server 端到端
 *
 * 测试策略:
 *   - 默认 8 单测用 vm sandbox 注入 SDK stub,测试不可达 / 数据解析 / env 覆盖
 *   - 加 --e2e 起一个临时 mock HTTP server 模拟 YesPlayMusic /status + /current-track 响应
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

// ── mock YesPlayMusic HTTP server ────────────────────────────────
// 模拟 YesPlayMusic 的 { data: { ... } } 响应结构
function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/status') {
        res.end(JSON.stringify({
          data: {
            playing: true,
            currentTime: 42,
            duration: 215,
            volume: 80,
            loop: false,
            shuffle: true,
            player: {
              id: 123456,
              name: '万神纪',
              artist: '三无Marblue',
              album: '万神纪',
            },
          },
        }));
      } else if (req.url === '/current-track') {
        res.end(JSON.stringify({
          data: {
            id: 123456,
            name: '万神纪',
            album: '万神纪',
            artist: '三无Marblue',
            picUrl: 'https://p1.music.126.net/abc/abc.jpg',
            duration: 215000,
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

// ── sandbox 加载 index.js ─────────────────────────────────────────
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
    process: { env: { ...process.env, ...extraEnv } },
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

// ── 极简 assert ──────────────────────────────────────────────────
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
  console.log('\n[Phase A 只读扩展单测] yesplaymusic-bridge-status\n');

  // 1. ypmBaseUrl env 覆盖
  t('ypmBaseUrl env 覆盖默认', () => {
    const m1 = loadModule({});
    assertEq(m1.ypmBaseUrl(), 'http://127.0.0.1:27232');
    const m2 = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://ypm.local:9999' });
    assertEq(m2.ypmBaseUrl(), 'http://ypm.local:9999');
  });

  // 2. fetchStatus 不可达 → ok=false + alive=false
  await ta('fetchStatus 不可达 → ok=false + alive=false', async () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchStatus();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 3. fetchCurrentTrack 不可达 → ok=false
  await ta('fetchCurrentTrack 不可达 → ok=false', async () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchCurrentTrack();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 4. probeHealth 不可达 → alive=false + 延迟合理
  await ta('probeHealth 不可达 → alive=false + 延迟 < 5000ms', async () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.probeHealth();
    assertEq(r.alive, false);
    assertTrue(r.latency_ms >= 0, 'latency negative');
    assertTrue(r.latency_ms < 5000, `latency too high: ${r.latency_ms}ms`);
  });

  // 5. httpGet 非 JSON → parsed=null
  t('httpGet 404 不可达解析', () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    return m.httpGet('/status').then((r) => {
      assertEq(r.ok, false);
      assertEq(r.parsed, null);
    });
  });

  // 6. fetchStatus data 字段解析(data 包裹)
  await ta('fetchStatus data 包裹解析(offline mock 失败 → ok=false)', async () => {
    // 不起 mock server,直接走不可达 — 仅验证错误处理路径
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchStatus();
    assertEq(r.ok, false);
    // playing / volume 等字段不应出现,因为没数据
    assertTrue(r.playing === undefined, 'playing should be undefined on fail');
  });

  // 7. fetchStatus 不可达时 last_error 含路径
  await ta('fetchStatus 不可达 last_error 含 HTTP/error 描述', async () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchStatus();
    assertTrue(/HTTP|ECONNREFUSED|connect/i.test(r.last_error), `last_error 不够详细: ${r.last_error}`);
  });

  // 8. fetchCurrentTrack 不可达 last_error 含路径
  await ta('fetchCurrentTrack 不可达 last_error 含描述', async () => {
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchCurrentTrack();
    assertTrue(/HTTP|ECONNREFUSED|connect/i.test(r.last_error), `last_error 不够详细: ${r.last_error}`);
  });

  // ── E2E(mock HTTP server 模拟 YesPlayMusic)─────────────────
  if (E2E) {
    console.log('\n[E2E] Mock YesPlayMusic server @ 127.0.0.1:<random>\n');
    const { server, port } = await startMockServer();
    const m = loadModule({ PRISIR_YESPLAYMUSIC_URL: `http://127.0.0.1:${port}` });

    try {
      await ta('E2E probeHealth → alive + latency < 500ms', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchStatus → playing=true + 万神纪', async () => {
        const r = await m.fetchStatus();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.playing, true);
        assertEq(r.currentTime, 42);
        assertEq(r.volume, 80);
        assertEq(r.loop, false);
        assertEq(r.shuffle, true);
        assertEq(r.track.name, '万神纪');
        assertEq(r.track.artist, '三无Marblue');
      });

      await ta('E2E fetchCurrentTrack → 万神纪 + 215s 时长', async () => {
        const r = await m.fetchCurrentTrack();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.track.id, 123456);
        assertEq(r.track.name, '万神纪');
        assertEq(r.track.duration, 215000);
        assertTrue(r.track.picUrl.includes('music.126.net'), 'picUrl format');
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock HTTP server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();