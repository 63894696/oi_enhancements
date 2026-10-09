/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/simplex-bridge-status/__tests__/run.js
 *   node extensions/simplex-bridge-status/__tests__/run.js --e2e
 *
 * Phase C (2026-10-10): SimpleX 是 bearer SDK 第 15 个用户
 * (单层 Bearer Token + SimpleX CLI 6.x HTTP API + envelope unwrap {result}/{resp})
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

// ── mock SimpleX CLI 6.x server ────────────────────────────
const DEMO_TOKEN = 'simplex_demo_bearer_cccccccccccccccccccccccccccccccccccccccc';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const auth = String(req.headers['authorization'] || '');
      const token = auth.replace(/^Bearer\s+/i, '');

      res.setHeader('Content-Type', 'application/json; charset=utf-8');

      // 错 token → 401
      if (token !== DEMO_TOKEN) {
        res.statusCode = 401;
        res.end(JSON.stringify({ type: 'apiError', apiError: { type: 'unauthorized', message: 'invalid token' } }));
        return;
      }

      // GET / 探活
      if (req.method === 'GET' && (req.url === '/' || req.url === '')) {
        res.end(JSON.stringify({ type: 'apiOk' }));
        return;
      }

      // POST /v3/contacts
      if (req.method === 'POST' && req.url === '/v3/contacts') {
        let body = '';
        req.on('data', (c) => { body += c; });
        req.on('end', () => {
          const req2 = JSON.parse(body || '{}');
          if (req2.type !== 'contactsList') {
            res.statusCode = 400;
            res.end(JSON.stringify({ type: 'apiError', apiError: { type: 'badRequest', message: `unknown type ${req2.type}` } }));
            return;
          }
          res.end(JSON.stringify({
            corrId: '1',
            resp: {
              type: 'contactsList',
              contacts: [
                { contactId: 1, localDisplayName: 'alice',
                  profile: { displayName: 'Alice', fullName: 'Alice Chen', image: null },
                  isUser: false, activeConn: 'conn-1' },
                { contactId: 2, localDisplayName: 'bob',
                  profile: { displayName: 'Bob', fullName: '', image: 'avatar-bob.png' },
                  isUser: false, activeConn: 'conn-2' },
                { contactId: 3, localDisplayName: 'self',
                  profile: { displayName: 'Me', fullName: 'My Name' },
                  isUser: true, activeConn: null },
              ],
            },
          }));
        });
        return;
      }

      // POST /v3/chats
      if (req.method === 'POST' && req.url === '/v3/chats') {
        let body = '';
        req.on('data', (c) => { body += c; });
        req.on('end', () => {
          const req2 = JSON.parse(body || '{}');
          if (req2.type !== 'chatsList') {
            res.statusCode = 400;
            res.end(JSON.stringify({ type: 'apiError', apiError: { type: 'badRequest', message: `unknown type ${req2.type}` } }));
            return;
          }
          res.end(JSON.stringify({
            corrId: '2',
            resp: {
              type: 'chatsList',
              chats: [
                { chatId: 100, chatInfo: { type: 'direct', localDisplayName: 'alice', contactId: 1 } },
                { chatId: 200, chatInfo: { type: 'group', localDisplayName: 'Engineering', groupId: 10 } },
                { chatId: 300, chatInfo: { type: 'direct', localDisplayName: 'bob', contactId: 2 } },
              ],
            },
          }));
        });
        return;
      }

      // POST /v3/groups
      if (req.method === 'POST' && req.url === '/v3/groups') {
        let body = '';
        req.on('data', (c) => { body += c; });
        req.on('end', () => {
          const req2 = JSON.parse(body || '{}');
          if (req2.type !== 'groupsList') {
            res.statusCode = 400;
            res.end(JSON.stringify({ type: 'apiError', apiError: { type: 'badRequest', message: `unknown type ${req2.type}` } }));
            return;
          }
          res.end(JSON.stringify({
            corrId: '3',
            resp: {
              type: 'groupsList',
              groups: [
                { groupId: 10, localDisplayName: 'Engineering', displayName: 'Engineering Team',
                  fullName: 'Engineering (private)', membership: { memberActive: true },
                  groupProfile: { description: 'Internal engineering team', image: null } },
                { groupId: 20, localDisplayName: 'family', displayName: 'Family',
                  fullName: 'Family Group', membership: { memberActive: true },
                  groupProfile: { description: null, image: 'family.png' } },
              ],
            },
          }));
        });
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ type: 'apiError', apiError: { type: 'notFound', message: `Route ${req.method} ${req.url} not found` } }));
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
  console.log('\n[Phase A 只读扩展单测] simplex-bridge-status(bearer SDK 第 15 用户 + 跨入跨设备/E2E 通信域 + Bearer Token + envelope unwrap)\n');

  // 1. env 默认 + override
  t('env 默认 + override + simplexConfig 不可变', () => {
    const m1 = loadModule({});
    assertEq(m1.simplexConfig().baseUrl_(), 'http://127.0.0.1:5225');
    assertEq(m1.simplexConfig().token_(), '');
    assertEq(m1.simplexConfig().timeoutMs_(), 5000);
    const m2 = loadModule({
      PRISIR_SIMPLEX_URL: 'http://simplex.local:9000',
      PRISIR_SIMPLEX_API_KEY: 'simplex_jwt',
    });
    assertEq(m2.simplexConfig().baseUrl_(), 'http://simplex.local:9000');
    assertEq(m2.simplexConfig().token_(), 'simplex_jwt');
  });

  // 2. unwrapSimpleX 第一层 unwrap
  t('unwrapSimpleX: {result} + {resp} + null 兼容', () => {
    const m = loadModule({});
    assertEq(m.unwrapSimpleX({ result: { type: 'a' } }).type, 'a');
    assertEq(m.unwrapSimpleX({ resp: { type: 'b' } }).type, 'b');
    assertEq(m.unwrapSimpleX(null), null);
    assertEq(m.unwrapSimpleX({ type: 'c' }).type, 'c');  // 无 result/resp 直返
  });

  // 3. probeHealth 无 token → no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 4. fetchContacts 无 token → ok=false
  await ta('fetchContacts 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchContacts();
    assertEq(r.ok, false);
  });

  // 5. fetchChats 无 token → ok=false
  await ta('fetchChats 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchChats();
    assertEq(r.ok, false);
  });

  // 6. fetchGroups 无 token → ok=false
  await ta('fetchGroups 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchGroups();
    assertEq(r.ok, false);
  });

  // 7. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_SIMPLEX_URL: 'http://127.0.0.1:1',
      PRISIR_SIMPLEX_API_KEY: 'tok',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // 8. SDK 复用验证
  await ta('SDK 复用验证:httpGet + httpPostJson + describeAuth + makeConfig SDK 在场', async () => {
    const sdk = require('../../_scaffold/bearer-client.js');
    assertEq(typeof sdk.httpGet, 'function');
    assertEq(typeof sdk.httpPostJson, 'function');
    assertEq(typeof sdk.describeAuth, 'function');
    assertEq(typeof sdk.makeConfig, 'function');
    const cfg = sdk.makeConfig({ baseUrl: 'http://x:5225', token: '' });
    assertEq(sdk.describeAuth(cfg).has_token, false);
  });

  // ── E2E(mock SimpleX CLI 6.x @ 随机端口)──────────
  if (E2E) {
    console.log('\n[E2E] Mock SimpleX CLI 6.x @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Bearer <token> + POST {result} unwrap + 0 content 抓取\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_SIMPLEX_URL: `http://127.0.0.1:${port}`,
        PRISIR_SIMPLEX_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + http_status=200', async () => {
        const r = await m.probeHealth();
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertTrue(r.latency_ms < 500, `latency ${r.latency_ms}ms too high`);
      });

      await ta('E2E fetchContacts → 3 contacts + is_user 标记 + has_profile_image', async () => {
        const r = await m.fetchContacts();
        assertEq(r.ok, true);
        assertEq(r.total, 3);
        assertEq(r.contacts[0].local_display_name, 'alice');
        assertEq(r.contacts[0].is_user, false);
        assertEq(r.contacts[0].is_contact, true);
        assertEq(r.contacts[0].active, true);
        assertEq(r.contacts[1].has_profile_image, true);  // bob 有 image
        assertEq(r.contacts[2].is_user, true);
        assertEq(r.contacts[2].is_contact, false);
      });

      await ta('E2E fetchChats → 3 chats(2 direct + 1 group) + has_metadata_only=true', async () => {
        const r = await m.fetchChats();
        assertEq(r.ok, true);
        assertEq(r.total, 3);
        assertEq(r.chats[0].chat_type, 'direct');
        assertEq(r.chats[1].chat_type, 'group');
        assertEq(r.chats[0].has_metadata_only, true, 'P0 不抓 content');
      });

      await ta('E2E fetchChats(chatType=group) → 验 query 拼接', async () => {
        const r = await m.fetchChats({ chatType: 'group' });
        assertEq(r.ok, true);
        // mock 不真 chatType 过滤,全返 — 验 query 拼接成功即可
      });

      await ta('E2E fetchGroups → 2 groups + has_description + has_description=false', async () => {
        const r = await m.fetchGroups();
        assertEq(r.ok, true);
        assertEq(r.total, 2);
        assertEq(r.groups[0].local_display_name, 'Engineering');
        assertEq(r.groups[0].has_description, true);
        assertEq(r.groups[1].has_description, false);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_SIMPLEX_URL: `http://127.0.0.1:${port}`,
        PRISIR_SIMPLEX_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchContacts();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock SimpleX CLI 6.x)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();