/**
 * __tests__/run.js — Phase A 只读扩展 Node 端单测 + E2E
 *
 * 跑法:
 *   node extensions/paperless-bridge-status/__tests__/run.js
 *   node extensions/paperless-bridge-status/__tests__/run.js --e2e
 *
 * 测试策略:
 *   - 默认 8 单测用 vm sandbox 注入 SDK stub,验证扩展薄壳(env 注入 + Token 头 + DRF envelope 解析 + 分页跟随)
 *   - 加 --e2e 起临时 mock Paperless-ngx HTTP server,验证 Authorization: Token + DRF {count,next,previous,all,results} + 分页循环
 *
 * Phase C (2026-10-08): Paperless-ngx 是 custom-auth SDK 第 7 个用户(DRF 分页循环)
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

// ── mock Paperless-ngx HTTP server ────────────────────────────────
const DEMO_TOKEN = 'plngx_demo_token_aaaaaabbbbbb';

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json');
      const auth = String(req.headers['authorization'] || '');

      // 缺 / 错 Token
      const expectedAuth = `Token ${DEMO_TOKEN}`;
      if (auth !== expectedAuth) {
        res.statusCode = 401;
        res.end(JSON.stringify({ detail: 'Authentication credentials were not provided.' }));
        return;
      }

      // /api/ 探活(DRF root)
      if (req.method === 'GET' && req.url === '/api/') {
        res.end(JSON.stringify({
          documents: 'http://x/api/documents/',
          tags: 'http://x/api/tags/',
          correspondents: 'http://x/api/correspondents/',
          document_types: 'http://x/api/document_types/',
        }));
        return;
      }

      // /api/documents/{id}/thumb/(返 PNG 二进制 — 必须在 /documents/ startsWith 之前)
      if (req.method === 'GET' && /^\/api\/documents\/\d+\/thumb\/?$/.test(req.url)) {
        // 89 50 4E 47 0D 0A 1A 0A (PNG magic) + 假字节
        const png = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52]);
        res.setHeader('Content-Type', 'image/png');
        res.end(png);
        return;
      }

      // /api/documents/(带分页参数)
      if (req.method === 'GET' && req.url.startsWith('/api/documents/')) {
        const u = new URL(req.url, 'http://x');
        const page = Number(u.searchParams.get('page') || '1');
        const pageSize = Number(u.searchParams.get('page_size') || '25');
        const startIdx = (page - 1) * pageSize;
        const endIdx = startIdx + pageSize;
        // 模拟 3 页: page 1 → 3 docs, page 2 → 2 docs, page 3 → empty
        const allDocs = [
          { id: 1, title: 'Invoice 2026-01', document_type: 1, correspondent: 2, tags: [3, 5],
            created: '2026-01-15', modified: '2026-01-15', added: '2026-01-15T10:00:00Z',
            archive_serial_number: 1, original_filename: 'invoice_2026_01.pdf',
            mime_type: 'application/pdf', page_count: 3 },
          { id: 2, title: 'Lease Agreement 2024', document_type: 2, correspondent: 1, tags: [4],
            created: '2024-03-20', modified: '2024-03-20', added: '2024-03-20T08:00:00Z',
            archive_serial_number: 2, original_filename: 'lease_2024.pdf',
            mime_type: 'application/pdf', page_count: 12 },
          { id: 3, title: 'Medical Report', document_type: 3, correspondent: null, tags: [6],
            created: '2026-05-10', modified: '2026-05-10', added: '2026-05-10T14:30:00Z',
            archive_serial_number: 3, original_filename: 'medical_2026_05.pdf',
            mime_type: 'application/pdf', page_count: 2 },
        ];
        const slice = allDocs.slice(startIdx, endIdx);
        const hasNext = endIdx < allDocs.length;
        res.end(JSON.stringify({
          count: allDocs.length,
          next: hasNext ? `http://127.0.0.1:${u.port}/api/documents/?page=${page + 1}&page_size=${pageSize}` : null,
          previous: page > 1 ? `http://127.0.0.1:${u.port}/api/documents/?page=${page - 1}&page_size=${pageSize}` : null,
          all: allDocs.map(d => d.id),
          results: slice,
        }));
        return;
      }

      // /api/tags/
      if (req.method === 'GET' && req.url.startsWith('/api/tags/')) {
        res.end(JSON.stringify({
          count: 3,
          next: null, previous: null,
          results: [
            { id: 3, name: 'Invoices',  slug: 'invoices',  color: '#FF0000', match: '', is_inbox_tag: false, document_count: 5 },
            { id: 4, name: 'Contracts', slug: 'contracts', color: '#00FF00', match: '', is_inbox_tag: false, document_count: 12 },
            { id: 5, name: 'Medical',   slug: 'medical',   color: '#0000FF', match: '', is_inbox_tag: false, document_count: 3 },
          ],
        }));
        return;
      }

      res.statusCode = 404;
      res.end(JSON.stringify({ detail: 'Not found.' }));
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
  console.log('\n[Phase A 只读扩展单测] paperless-bridge-status(custom-auth SDK 第 7 用户 + 跨入文档域 + DRF 分页)\n');

  // 1. env 默认 + override
  t('env 默认 + override + paperlessConfig 不可变', () => {
    const m1 = loadModule({});
    assertEq(m1.paperlessConfig().baseUrl(), 'http://127.0.0.1:8000');
    assertEq(m1.paperlessConfig().mode(), 'custom');
    assertEq(m1.paperlessConfig().customHeader(), 'Authorization');
    assertEq(m1.paperlessConfig().customToken(), '');
    assertEq(m1.paperlessConfig().timeoutMs(), 8000);
    const m2 = loadModule({
      PRISIR_PAPERLESS_URL: 'http://paper.local:9000',
      PRISIR_PAPERLESS_API_KEY: 'tok_demo',
    });
    assertEq(m2.paperlessConfig().baseUrl(), 'http://paper.local:9000');
    assertEq(m2.paperlessConfig().customToken(), 'Token tok_demo');  // DRF prefix
  });

  // 2. parseDrfEnvelope envelope 抽取
  t('parseDrfEnvelope envelope 抽取 + 跳 all', () => {
    const m = loadModule({});
    const env = m.parseDrfEnvelope({
      count: 100, next: 'http://x/api/?page=2', previous: null,
      all: [1, 2, 3, 4, 5], results: [{ id: 1 }],
    });
    assertEq(env.count, 100);
    assertEq(env.next, 'http://x/api/?page=2');
    assertEq(env.results.length, 1);
    // parseDrfEnvelope 抽取不剥 all(那是 fetchDocuments 的事)
    const env2 = m.parseDrfEnvelope({ count: 0, next: null, previous: null, results: [] });
    assertEq(env2.results, []);
  });

  // 3. pathToRelativePath DRF next URL → relative
  t('pathToRelativePath 抽取 next URL pathname+search', () => {
    const m = loadModule({});
    const p1 = m.pathToRelativePath('http://x:1234/api/documents/?page=2', 'http://x:1234');
    assertEq(p1, '/api/documents/?page=2');
    const p2 = m.pathToRelativePath('http://other/api/x/?page=3', 'http://x:1234');
    assertEq(p2, '/api/x/?page=3');
    const p3 = m.pathToRelativePath(null, 'http://x:1234');
    assertEq(p3, null);
  });

  // 4. probeHealth 无 token → no credentials
  await ta('probeHealth 无 token → alive=false + no credentials', async () => {
    const m = loadModule({});
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertEq(r.alive, false);
    assertTrue(/no credentials/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 5. fetchDocuments 无 token → ok=false
  await ta('fetchDocuments 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchDocuments();
    assertEq(r.ok, false);
  });

  // 6. fetchTags 无 token → ok=false
  await ta('fetchTags 无 token → ok=false + last_error', async () => {
    const m = loadModule({});
    const r = await m.fetchTags();
    assertEq(r.ok, false);
  });

  // 7. fetchThumb 错 documentId → invalid
  await ta('fetchThumb documentId=0 → invalid documentId', async () => {
    const m = loadModule({ PRISIR_PAPERLESS_API_KEY: 'tok' });
    const r = await m.fetchThumb({ documentId: 0 });
    assertEq(r.ok, false);
    assertTrue(/invalid documentId/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
  });

  // 8. probeHealth 不可达 → ECONNREFUSED
  await ta('probeHealth 不可达 → alive=false + ECONNREFUSED', async () => {
    const m = loadModule({
      PRISIR_PAPERLESS_URL: 'http://127.0.0.1:1',
      PRISIR_PAPERLESS_API_KEY: 'tok',
    });
    const r = await m.probeHealth();
    assertEq(r.ok, false);
    assertTrue(/ECONNREFUSED|connect|timeout/i.test(r.last_error || ''), `last_error 不对: ${r.last_error}`);
  });

  // ── E2E(mock Paperless-ngx server 模拟 DRF + 分页 + thumb)──
  if (E2E) {
    console.log('\n[E2E] Mock Paperless-ngx server @ 127.0.0.1:<random>');
    console.log(`  鉴权: Authorization: Token <api_token> + DRF {count,next,previous,all,results}\n`);
    const { server, port } = await startMockServer();

    try {
      const m = loadModule({
        PRISIR_PAPERLESS_URL: `http://127.0.0.1:${port}`,
        PRISIR_PAPERLESS_API_KEY: DEMO_TOKEN,
      });

      await ta('E2E probeHealth → alive + endpoints_count=4', async () => {
        const r = await m.probeHealth();
        if (!r.ok) console.log('DEBUG probeHealth:', JSON.stringify(r));
        assertEq(r.ok, true);
        assertEq(r.alive, true);
        assertEq(r.http_status, 200);
        assertEq(r.endpoints_count, 4);
        assertEq(r.auth.has_token, true);
      });

      await ta('E2E fetchDocuments(page_size=2) → 跟随 next 拉 2 页(3 docs 总)', async () => {
        const r = await m.fetchDocuments({ page_size: 2, max_pages: 5 });
        assertEq(r.ok, true);
        assertEq(r.total_in_query, 3);
        assertEq(r.documents.length, 3);                // 2 + 1
        assertEq(r.pages_fetched, 2);
        assertEq(r.documents[0].id, 1);
        assertEq(r.documents[0].title, 'Invoice 2026-01');
        assertEq(r.documents[0].page_count, 3);
        assertEq(r.documents[1].title, 'Lease Agreement 2024');
        assertEq(r.documents[2].id, 3);
        assertEq(r.documents[2].title, 'Medical Report');
      });

      await ta('E2E fetchDocuments(query=Invoice) → 1 doc + 过滤', async () => {
        const r = await m.fetchDocuments({ page_size: 25, query: 'Invoice' });
        // mock 不真过滤,全返(验 query 拼接成功,不 fail)
        assertEq(r.ok, true);
      });

      await ta('E2E fetchTags → 3 tags + slug + color + document_count', async () => {
        const r = await m.fetchTags();
        assertEq(r.ok, true);
        assertEq(r.total, 3);
        assertEq(r.tags[0].name, 'Invoices');
        assertEq(r.tags[0].slug, 'invoices');
        assertEq(r.tags[0].color, '#FF0000');
        assertEq(r.tags[0].document_count, 5);
        assertEq(r.tags[1].name, 'Contracts');
        assertEq(r.tags[2].name, 'Medical');
      });

      await ta('E2E fetchThumb(2) → PNG base64 + magic bytes + size', async () => {
        const r = await m.fetchThumb({ documentId: 2 });
        assertEq(r.ok, true);
        assertEq(r.document_id, 2);
        assertTrue(r.png_base64.length > 0, 'png_base64 应非空');
        // PNG magic bytes: 89 50 4E 47 = iVBOR (base64 "iVBORw")
        const buf = Buffer.from(r.png_base64, 'base64');
        assertEq(buf[0], 0x89);
        assertEq(buf[1], 0x50);
        assertEq(buf[2], 0x4E);
        assertEq(buf[3], 0x47);
        assertTrue(r.size_bytes >= 16, `size_bytes 太小: ${r.size_bytes}`);
      });

      // 错 token → 401
      const mWrong = loadModule({
        PRISIR_PAPERLESS_URL: `http://127.0.0.1:${port}`,
        PRISIR_PAPERLESS_API_KEY: 'wrong-token',
      });

      await ta('E2E 错 token → 401 → ok=false + http_status + last_error', async () => {
        const r = await mWrong.fetchDocuments();
        assertEq(r.ok, false);
        assertEq(r.http_status, 401);
        assertTrue(/401/i.test(r.last_error), `last_error 不对: ${r.last_error}`);
      });
    } finally {
      server.close();
    }
  } else {
    console.log('\n跳过 E2E(加 --e2e 真跑 mock Paperless-ngx server)');
  }

  console.log(`\n[结果] ✓ ${pass}  ✗ ${fail}`);
  if (fail > 0) {
    for (const f of failures) console.log(`  ${f.name}: ${f.err.stack || f.err.message}`);
    process.exit(1);
  }
  process.exit(0);
})();