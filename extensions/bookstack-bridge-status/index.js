'use strict';

/**
 * bookstack-bridge-status v0.1.0 — BookStack 自托管 wiki / 文档(只读, Bearer token)
 *
 * 数据来源: BookStack REST API(自托管默认 http://<host>/api,80/443)。
 *            BookStack 是 PHP+MySQL 的自托管 wiki / 文档系统,典型用作家目录结构知识库
 *            (Shelves → Books → Chapters → Pages,类似图书馆分类)。
 * 鉴权:    `Authorization: Bearer <token_id>:<token_secret>` 标准 Bearer(RFC 6750),
 *          token 在 user profile → API Tokens 生成,格式 id:secret。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:BookStack 是 bearer-client.js SDK **第八个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud/Outline)。
 * **首个纯 REST + Bearer 扩展**(Outline 是 RPC-style POST,BookStack 是真 REST GET),
 * 完美适配 SDK httpGet 一招鲜,**零 SDK 边界跨越**。
 *
 * 命令(L0 风险, 纯只读):
 *   bookstack.health     {}  → { ok, alive, bookstack_url, latency_ms, http_status, last_error }
 *   bookstack.shelves    {}  → { ok, alive, shelves: [{id, slug, name, description}] }
 *   bookstack.books      {}  → { ok, alive, books: [{id, slug, name, description}] }
 *
 * **绝不**触碰 POST /api/books / PUT /api/books/{id} / DELETE /api/books/{id} 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_BOOKSTACK_URL     默认 http://127.0.0.1
 *   PRISIR_BOOKSTACK_API_KEY Bearer token_id:token_secret(BookStack user profile → API Tokens)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function bsConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_BOOKSTACK_URL || 'http://127.0.0.1',
    token: process.env.PRISIR_BOOKSTACK_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── GET /api/books 探活(鉴权 + 列出全部书籍探活 + 数据双用途)──
async function probeHealth() {
  const cfg = bsConfig();
  const t0 = Date.now();
  // 真 REST GET,SDK httpGet 复用(无需任何 SDK 边界跨越)
  const r = await httpGet({ config: cfg, path: '/api/books' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      bookstack_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/books 返 { data: [{id, slug, name, description, ...}], total: N }
  const total = r.parsed && typeof r.parsed.total === 'number' ? r.parsed.total : 0;
  return {
    ok: true, alive: true,
    bookstack_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    total_books: total,
    last_error: '',
  };
}

// ── GET /api/shelves 列出所有书架 ──────────────────────
async function fetchShelves() {
  const cfg = bsConfig();
  const r = await httpGet({ config: cfg, path: '/api/shelves' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/shelves 返 { data: [{id, slug, name, description, ...}], total: N }
  const list = r.parsed && Array.isArray(r.parsed.data) ? r.parsed.data : [];
  return {
    ok: true, alive: true,
    shelves: list.map((s) => ({
      id: String(s.id || ''),
      slug: String(s.slug || ''),
      name: String(s.name || ''),
      description: String(s.description || ''),
      book_count: Number(s.books_count) || 0,
    })),
    last_error: '',
  };
}

// ── GET /api/books 列出所有书籍 ────────────────────────────
async function fetchBooks() {
  const cfg = bsConfig();
  const r = await httpGet({ config: cfg, path: '/api/books' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/books 返 { data: [{id, slug, name, description, ...}], total: N }
  const list = r.parsed && Array.isArray(r.parsed.data) ? r.parsed.data : [];
  return {
    ok: true, alive: true,
    books: list.map((b) => ({
      id: String(b.id || ''),
      slug: String(b.slug || ''),
      name: String(b.name || ''),
      description: String(b.description || ''),
      created_at: String(b.created_at || ''),
      updated_at: String(b.updated_at || ''),
    })),
    total: r.parsed && typeof r.parsed.total === 'number' ? r.parsed.total : list.length,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'bookstack-bridge-status',
  name: 'BookStack 自托管 wiki(只读, Bearer token)',
  version: '0.1.0',
});

ext.registerCommand('bookstack.health', async () => probeHealth());
ext.registerCommand('bookstack.shelves', async () => fetchShelves());
ext.registerCommand('bookstack.books', async () => fetchBooks());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchShelves, fetchBooks,
    bsConfig,
  };
}