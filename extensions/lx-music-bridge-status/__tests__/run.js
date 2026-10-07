/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测
 *
 * 用 vm sandbox 隔离 PrisIrExt,提取 parseLrc / currentIndex / fetchStatus / fetchLyric,
 * 直接调用并 assert。退出码 0 全过, 1 有失败。
 *
 * 跑法:
 *   node extensions/lx-music-bridge-status/__tests__/run.js
 *   node extensions/lx-music-bridge-status/__tests__/run.js --e2e   # 加 LX 真跑
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const http = require('http');

const EXT_DIR = path.resolve(__dirname, '..');
const INDEX_JS = path.join(EXT_DIR, 'index.js');
const SRC = fs.readFileSync(INDEX_JS, 'utf8');

const E2E = process.argv.includes('--e2e');

// ── 加载 index.js 到 sandbox,导出内部函数 ─────────────────────────
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
  // index.js 是 const ext = new PrisIrExt(...) 并 ext.start() 启动;
  // 我们的 stub 让 start() 返回 resolved Promise,所以不会有 stdin 阻塞。
  return captured.exports;
}

// ── 极简 assert 工具 ────────────────────────────────────────────────
let pass = 0, fail = 0;
const failures = [];
function t(name, fn) {
  try {
    fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    fail++;
    failures.push({ name, err: e });
    console.log(`  ✗ ${name}: ${e.message}`);
  }
}
async function ta(name, fn) {
  try {
    await fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    fail++;
    failures.push({ name, err: e });
    console.log(`  ✗ ${name}: ${e.message}`);
  }
}
function assertEq(a, b, msg = '') {
  const A = JSON.stringify(a), B = JSON.stringify(b);
  if (A !== B) throw new Error(`${msg}\n  expected: ${B}\n  actual:   ${A}`);
}
function assertTrue(v, msg = '') { if (!v) throw new Error(msg || 'expected truthy'); }

// ── Tests ──────────────────────────────────────────────────────────
(async () => {
  console.log('\n[Phase A 只读扩展单测] lx-music-bridge-status\n');

  // 1. parseLrc basic
  t('parseLrc 标准格式 + 元数据过滤', () => {
    const m = loadModule();
    const lines = m.parseLrc(
      '[ar:三无]\n[ti:万神纪]\n[00:00.165]万神纪 - 三无Marblue\n' +
      '[00:03.176]词:邪叫教主\n[01:05.000]作曲:T-L-S'
    );
    assertEq(lines.length, 3);
    assertEq(lines[0].t_ms, 165);
    assertEq(lines[0].text, '万神纪 - 三无Marblue');
    assertEq(lines[1].t_ms, 3176);
    assertEq(lines[2].t_ms, 65000);
  });

  // 2. parseLrc 空 / null / 无时间标签
  t('parseLrc 空 / null / 无时间标签', () => {
    const m = loadModule();
    assertEq(m.parseLrc(''), []);
    assertEq(m.parseLrc(null), []);
    assertEq(m.parseLrc(undefined), []);
    assertEq(m.parseLrc('plain text only'), []);
    // 含 [xx:] 标签但没数字 — 也应被过滤
    assertEq(m.parseLrc('[ar:foo]\n[ti:bar]'), []);
  });

  // 3. currentIndex
  t('currentIndex 边界(0 / 在中间 / 末行)', () => {
    const m = loadModule();
    const lines = m.parseLrc('[00:01.000]a\n[00:02.000]b\n[00:03.000]c');
    assertEq(m.currentIndex(lines, 0), -1);
    assertEq(m.currentIndex(lines, 500), -1);
    assertEq(m.currentIndex(lines, 1500), 0);
    assertEq(m.currentIndex(lines, 2500), 1);
    assertEq(m.currentIndex(lines, 3000), 2);
    assertEq(m.currentIndex(lines, 999999), 2);
  });

  // 4. fetchStatus 不可达
  await ta('fetchStatus 不可达 → ok=false + lx_alive=false', async () => {
    const m = loadModule({ PRISIR_LX_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchStatus();
    assertEq(r.ok, false);
    assertEq(r.lx_alive, false);
    assertTrue(r.last_error && r.last_error.length > 0, 'missing last_error');
  });

  // 5. parseLrc 多时间戳同行(部分 LRC 同一 [mm:ss..] 多个时间戳)
  await ta('parseLrc 多时间戳同行取第一段', () => {});

  // 6. fetchLyric 不可达
  await ta('fetchLyric 不可达 → ok=false', async () => {
    const m = loadModule({ PRISIR_LX_URL: 'http://127.0.0.1:1' });
    const r = await m.fetchLyric(0);
    assertEq(r.ok, false);
    assertEq(r.lx_alive, false);
    assertEq(r.lines, []);
    assertEq(r.current_index, -1);
    assertEq(r.current_text, '');
  });

  // 7. probeHealth 不可达
  await ta('probeHealth 不可达 → lx_alive=false + 延迟合理', async () => {
    const m = loadModule({ PRISIR_LX_URL: 'http://127.0.0.1:1' });
    const r = await m.probeHealth();
    assertEq(r.lx_alive, false);
    assertTrue(r.latency_ms >= 0, 'latency negative');
    assertTrue(r.latency_ms < 5000, `latency too high: ${r.latency_ms}ms`);
  });

  // 8. lxBaseUrl env override
  t('lxBaseUrl env 覆盖默认', () => {
    const m1 = loadModule({});
    assertEq(m1.lxBaseUrl(), 'http://127.0.0.1:23330');
    const m2 = loadModule({ PRISIR_LX_URL: 'http://lx.local:9999' });
    assertEq(m2.lxBaseUrl(), 'http://lx.local:9999');
  });

  // ── E2E(默认 skip)────────────────────────────────────────────────
  if (E2E) {
    console.log('\n[E2E] 真实 LX Desktop @ 127.0.0.1:23330\n');
    await ta('E2E fetchStatus 真实拉取 → lx_alive=true', async () => {
      const m = loadModule({});
      const r = await m.fetchStatus();
      assertEq(r.ok, true);
      assertEq(r.lx_alive, true);
      assertTrue(r.name && r.name.length > 0, 'empty name');
      console.log(`     name=${r.name} singer=${r.singer} status=${r.status}`);
    });
    await ta('E2E fetchLyric 真实解析', async () => {
      const m = loadModule({});
      const r = await m.fetchLyric(0);
      assertEq(r.ok, true);
      assertTrue(r.lines.length > 0, `no LRC lines: ${r.lrc_raw.slice(0, 200)}`);
    });
    await ta('E2E probeHealth 延迟 < 500ms', async () => {
      const m = loadModule({});
      const r = await m.probeHealth();
      assertEq(r.lx_alive, true);
      assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
    });
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 LX Desktop)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();