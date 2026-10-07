'use strict';

/**
 * outline-bridge-status v0.1.0 — Outline 知识库(只读, Bearer API key)
 *
 * 数据来源: Outline 自家 REST API(自托管默认 127.0.0.1:3000/api,云版 https://app.getoutline.com/api)。
 *            Outline 是 Notion 替代品(自托管知识库 / 团队 wiki),REST API 设计类似 RPC。
 * 鉴权:    `Authorization: Bearer ol_api_<key>` 标准 Bearer(RFC 6750),
 *          token 在 Outline Settings → API & Apps 生成。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Outline 是 bearer-client.js SDK **第七个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud)。
 *
 * **SDK 增强触发**(第 2 个 inline POST):
 * Plausible 是首个 inline httpPostJson(30 行),Outline 是第 2 个——SDK 抽取阈值触发,
 * **httpPostJson 抽进 bearer SDK**(与 httpGet / makeConfig 并列 API 5)。
 * 后续 Plausible 可重构用 SDK(零扩展代码改动)。
 *
 * 命令(L0 风险, 纯只读):
 *   outline.health       {}  → { ok, alive, outline_url, latency_ms, http_status, user, last_error }
 *   outline.collections  {}  → { ok, alive, collections: [{id, name, description}] }
 *   outline.documents    {collectionId?, limit?, offset?}  → { ok, alive, documents: [...] }
 *
 * **绝不**触碰 POST /api/documents.create / DELETE /api/documents.delete / PUT /api/documents.update 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_OUTLINE_URL     默认 http://127.0.0.1:3000
 *   PRISIR_OUTLINE_API_KEY Bearer API key(Outline Settings → API & Apps)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, httpPostJson, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function olConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_OUTLINE_URL || 'http://127.0.0.1:3000',
    token: process.env.PRISIR_OUTLINE_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── POST /api/auth.info 健康检查(鉴权 + 当前用户探活合并)──
async function probeHealth() {
  const cfg = olConfig();
  const t0 = Date.now();
  // Outline 所有端点 POST,SDK httpPostJson 复用(无 inline helper 重复)
  const r = await httpPostJson({ config: cfg, path: '/api/auth.info', body: {} });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      outline_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/auth.info 返 { ok: true, data: { user: {id, email, name}, team }, status: 200 }
  const data = r.parsed && r.parsed.data;
  return {
    ok: true, alive: true,
    outline_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    user: data && data.user ? {
      id: String(data.user.id || ''),
      email: String(data.user.email || ''),
      name: String(data.user.name || ''),
      is_admin: Boolean(data.user.isAdmin),
    } : null,
    last_error: '',
  };
}

// ── POST /api/collections.list 列出所有 collections ─────
async function fetchCollections() {
  const cfg = olConfig();
  const r = await httpPostJson({ config: cfg, path: '/api/collections.list', body: { limit: 100 } });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/collections.list 返 { ok: true, data: [{id, name, description, color, icon, type}], pagination: {...} }
  const data = Array.isArray(r.parsed && r.parsed.data) ? r.parsed.data : [];
  return {
    ok: true, alive: true,
    collections: data.map((c) => ({
      id: String(c.id || ''),
      name: String(c.name || ''),
      description: String(c.description || ''),
      color: String(c.color || ''),
      icon: String(c.icon || ''),
      type: String(c.type || ''),
    })),
    last_error: '',
  };
}

// ── POST /api/documents.list 列出文档(支持 collectionId 过滤)─────
async function fetchDocuments(args = {}) {
  const cfg = olConfig();
  const limit = Math.min(Math.max(Number(args.limit) || 25, 1), 100);
  const offset = Math.max(Number(args.offset) || 0, 0);
  const body = { limit, offset };
  if (args.collectionId) body.collectionId = String(args.collectionId);
  const r = await httpPostJson({ config: cfg, path: '/api/documents.list', body });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/documents.list 返 { ok: true, data: [{id, title, text, updatedAt, createdAt, ...}], pagination: {nextPath, offset} }
  const data = Array.isArray(r.parsed && r.parsed.data) ? r.parsed.data : [];
  return {
    ok: true, alive: true,
    documents: data.map((d) => ({
      id: String(d.id || ''),
      title: String(d.title || ''),
      text_preview: String(d.text || '').slice(0, 200),     // 截断避免大 body
      collection_id: String(d.collectionId || ''),
      updated_at: String(d.updatedAt || ''),
      created_at: String(d.createdAt || ''),
      url_id: String(d.urlId || ''),
      published: Boolean(d.published),
    })),
    next_offset: r.parsed && r.parsed.pagination && r.parsed.pagination.offset ? r.parsed.pagination.offset : null,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'outline-bridge-status',
  name: 'Outline 知识库(只读, Bearer API key)',
  version: '0.1.0',
});

ext.registerCommand('outline.health', async () => probeHealth());
ext.registerCommand('outline.collections', async () => fetchCollections());
ext.registerCommand('outline.documents', async (args) => fetchDocuments(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchCollections, fetchDocuments,
    olConfig,
  };
}
