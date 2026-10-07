/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/habitica-bridge-status/__tests__/run.js
 *   node extensions/habitica-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 8 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + 双自定义头 + envelope 解包)
 *   - 加 --e2e 起临时 mock Habitica HTTP server,验证 3 头鉴权 + {success, data, notifications} 解包
 *
 * Phase C (2026-10-08): Habitica 是 custom-auth SDK 第 6 个用户(双头 inline + x-client 常量)
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

// ── mock Habitica HTTP server ────────────────────────────────
const DEMO_USER_ID = '11111111-2222-3333-4444-555555555555';
const DEMO_API_KEY = 'hbt_demo_api_key_aaaaaaaaaaaa';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const userId = String(req.headers['x-api-user'] || '');
      const apiKey = String(req.headers['x-api-key'] || '');
      const xClient = String(req.headers['x-client'] || '');

      // 缺任一鉴权头 → 401
      if (!userId || !apiKey || !xClient) {
        res.statusCode = 401;
        res.end(JSON.stringify({ success: false, error: 'MissingAuth', notifications: [] }));
        return;
      }

      // 错 token / 错 user
      if (apiKey !== DEMO_API_KEY || userId !== DEMO_USER_ID) {
        res.statusCode = 401;
        res.end(JSON.stringify({ success: false, error: 'Unauthorized', notifications: [] }));
        return;
      }

      // /api/v3/status 公开探活
      if (req.method === 'GET' && req.url === '/api/v3/status') {
        res.end(JSON.stringify({
          data: { status: 'up', uptime: 12345.6, notifications: ['Welcome!'] },
          notifications: [],
        }));
        return;
      }

      // /api/v3/user 当前用户
      if (req.method === 'GET' && req.url === '/api/v3/user') {
        res.end(JSON.stringify({
          data: {
            _id: DEMO_USER_ID,
            username: 'alice_habitica',
            profile: { name: 'Alice' },
            stats: {
              hp: 42, maxHealth: 50,
              mp: 30, maxMP: 100,
              exp: 120, toNextLevel: 80,
              gp: 87, lvl: 8, class: 'rogue',
            },
            preferences: { sleep: true, hair: { color: 'blond' } },
          },
          notifications: [{ id: 'one', type: 'LOGIN_INCENTIVE' }],
        }));
        return;
      }

      // /api/v3/tasks/user(可带 type= 过滤)
      if (req.method === 'GET' && req.url.startsWith('/api/v3/tasks/user')) {
        const u = new URL(req.url, 'http://x');
        const typeFilter = u.searchParams.get('type');
        const allTasks = [
          { id: 't1', type: 'habit', text: 'Drink water', value: 1, priority: 1,
            counterUp: 12, counterDown: 1, tags: ['tag1'], completed: false },
          { id: 't2', type: 'daily', text: 'Morning meditation (long text truncated for test sensitivity boundary)',
            value: 5, priority: 2, completed: false, isDue: true, streak: 7,
            tags: [], createdAt: '2026-09-01T08:00:00.000Z' },
          { id: 't3', type: 'todo', text: 'Renew passport', value: 10, priority: 1,
            completed: false, tags: ['urgent'], createdAt: '2026-10-01T08:00:00.000Z' },
          { id: 't4', type: 'reward', text: 'Buy book', value: 50, priority: 1,
            tags: [], createdAt: '2026-08-15T08:00:00.000Z' },
        ];
        const filtered = typeFilter ? allTasks.filter(t => t.type + 's' === typeFilter) : allTasks;
        res.end(JSON.stringify({ data: filtered, notifications: [] }));
        return;
      }

      // /api/v3/tags 标签列表
      if (req.method === 'GET' && req.url === '/api/v3/tags') {
        res.end(JSON.stringify({
          data: [
            { id: 'tag1', name: 'health' },
            { id: 'tag2', name: 'work' },
            { id: 'tag3', name: 'personal' },
          ],
          notifications: [],
        }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ success: false, error: 'NotFound', notifications: [] }));
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
    URL,
    URLSearchParams,
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
  console.log('\n[Phase A 只读扩展单测] habitica-bridge-status(custom-auth SDK 第 6 用户 + 跨入习惯域 + 双自定义头)\n');

  // 1. env 默认 + override
  t('env 默认 + override + habiticaConfig + userId 不可变', () => {
    const m1 = loadModule({});
    assertEq(m1.habiticaConfig().baseUrl(), 'http://127.0.0.1:3000');
    assertEq(m1.habiticaConfig().mode(), 'custom');
    assertEq(m1.habiticaConfig().customHeader(), 'x-api-key');
    assertEq(m1.habiticaConfig().customToken(), '');
    assertEq(m1.habiticaUserId(), '');
    assertEq(m1.HABITICA_X_CLIENT, 'prisirai-prisirai');
    const m2 = loadModule({
      PRISIR_HABITICA_URL: 'http://habit.local:9000/api/v3',
      PRISIR_HABITICA_API_KEY: 'hbt_key',
      PRISIR_HABITICA_USER_ID: 'aaaa-bbbb',
    });
    assertEq(m2.habiticaConfig().baseUrl(), 'http://habit.local:9000/api/v3');
    assertEq(m2.habiticaConfig().customToken(), 'hbt_key');
    assertEq(m2.habiticaUserId(), 'aaaa-bbbb');
  });

  // 2. probeHealth 无 token → no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 3. probeHealth 有 token 但无 user_id → no credentials(SDK 早退要求 2 头)
  await ta('probeHealth 有 token 无 user_id → no credentials', async () => {
    const m = loadModule({ PRISIR_HABITICA_API_KEY: 'hbt_key' });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchUser 无 token → ok=false
  await ta('fetchUser 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchUser();
    assertEq(r.ok, false);
  });

  // 5. fetchTasks 无 token → ok=false
  await ta('fetchTasks 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchTasks();
    assertEq(r.ok, false);
  });

  // 6. fetchTags 无 token → ok=false
  await ta('fetchTags 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchTags();
    assertEq(r.ok, false);
  });

  // 7. unwrap envelope 解包
  t('unwrap envelope {success,data,notifications} → 返 data', () => {
    const m = loadModule({});
    assertEq(m.unwrap({ success: true, data: { hp: 42 }, notifications: [] }), { hp: 42 });
    assertEq(m.unwrap({ success: true, data: [1, 2, 3], notifications: [] }), [1, 2, 3]);
    assertEq(m.unwrap(null), null);
    assertEq(m.unwrap({ foo: 'bar' }), { foo: 'bar' });
  });

  // 8. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_HABITICA_URL: 'http://127.0.0.1:1/api/v3',
      PRISIR_HABITICA_API_KEY: 'hbt_key',
      PRISIR_HABITICA_USER_ID: 'u',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // ── E2E(mock Habitica server 模拟 3 头鉴权 + envelope 解包)──
  if (E2E) {
    console.log('\n[E2E] Mock Habitica server @ 127.0.0.1:<random>');
    console.log(`  鉴权: x-api-user + x-api-key + x-client + {success,data,notifications}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_HABITICA_URL: `http://127.0.0.1:${port}`,
        PRISIR_HABITICA_API_KEY: DEMO_API_KEY,
        PRISIR_HABITICA_USER_ID: DEMO_USER_ID,
      });

      await ta('E2E probeHealth → alive + x_client=prisirai-prisirai + notifications_count', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.auth.has_token, true);
        assertEq(r.auth.has_user_id, true);
        assertEq(r.auth.x_client, 'prisirai-prisirai');
        assertEq(r.status, 'up');
        assertEq(r.notifications_count, 1);
      });

      await ta('E2E fetchUser → stats.hp/mp/exp/gp + class + sleep_preference', async () => {
        const r = await m.fetchUser();
        assertEq(r.ok, true);
        assertEq(r.user_id, DEMO_USER_ID);
        assertEq(r.username, 'Alice');
        assertEq(r.hp, 42);
        assertEq(r.max_hp, 50);
        assertEq(r.mp, 30);
        assertEq(r.exp, 120);
        assertEq(r.to_next_level, 80);
        assertEq(r.gp, 87);
        assertEq(r.level, 8);
        assertEq(r.class, 'rogue');
        assertEq(r.sleep_preference, true);
        assertEq(r.notifications_count, 1);
      });

      await ta('E2E fetchTasks(默认) → 4 任务 + by_type {habit:1,daily:1,todo:1,reward:1}', async () => {
        const r = await m.fetchTasks();
        assertEq(r.ok, true);
        assertEq(r.total, 4);
        assertEq(r.by_type.habit, 1);
        assertEq(r.by_type.daily, 1);
        assertEq(r.by_type.todo, 1);
        assertEq(r.by_type.reward, 1);
        assertEq(r.tasks[0].type, 'habit');
        assertEq(r.tasks[0].counter_up, 12);
        assertEq(r.tasks[1].type, 'daily');
        assertEq(r.tasks[1].is_due, true);
        assertEq(r.tasks[1].streak, 7);
        assertEq(r.tasks[2].type, 'todo');
      });

      await ta('E2E fetchTasks(type=dailys) → 只 1 daily 任务', async () => {
        const r = await m.fetchTasks({ type: 'dailys' });
        assertEq(r.ok, true);
        assertEq(r.total, 1);
        assertEq(r.tasks[0].type, 'daily');
        assertEq(r.by_type.daily, 1);
        assertEq(r.by_type.habit, 0);
      });

      await ta('E2E fetchTags → 3 标签 + id/name', async () => {
        const r = await m.fetchTags();
        assertEq(r.ok, true);
        assertEq(r.total, 3);
        assertEq(r.tags[0].id, 'tag1');
        assertEq(r.tags[0].name, 'health');
        assertEq(r.tags[2].name, 'personal');
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_HABITICA_URL: `http://127.0.0.1:${port}`,
        PRISIR_HABITICA_API_KEY: 'wrong-token',
        PRISIR_HABITICA_USER_ID: DEMO_USER_ID,
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchUser();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });

      // 错 user_id → 401
      const mWrongUser = loadModule({
        PRISIR_HABITICA_URL: `http://127.0.0.1:${port}`,
        PRISIR_HABITICA_API_KEY: DEMO_API_KEY,
        PRISIR_HABITICA_USER_ID: 'wrong-user-id',
      });

      await ta('E2E 错 user_id → 401 → ok=false + http_status', async () => {
        const r = await mWrongUser.fetchUser();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Habitica server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();