'use strict';

/**
 * linkwarden-bridge-status v0.1.0 — Linkwarden 自托管书签/稍后读(只读, Bearer JWT)
 *
 * 数据来源: Linkwarden REST API(自托管默认 http://127.0.0.1:3000/api/v1)。
 *            Linkwarden 是 Next.js 15 + Prisma + NextAuth v5 的自托管书签/稍后读管理器
 *            (类似 Pocket / Raindrop / Pinboard)。
 * 鉴权:    `Authorization: Bearer <jwt>` 标准 Bearer(RFC 6750,JWT 格式),
 *          token 在 Linkwarden Settings → API Tokens 创建(选 oneWeek/oneMonth/twoMonths/threeMonths/never 过期),
 *          JWT 用 next-auth/jwt 签发,载荷 { id, iat, exp, jti },server 仅存 jti 用于撤销判断。
 *          借鉴原则 5「Token≠密码」中档(6/10)— JWT 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Linkwarden 是 bearer-client.js SDK **第十二个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud/Outline/BookStack/Firefly III/Mealie/Vaultwarden)。
 * **跨入书签/稍后读域**(前 11 用户跨 10 类)——bearer SDK 跨域累计 11 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/**书签**。
 *
 * **单层 Bearer 模式**(与 Vaultwarden OAuth2 dance 不同):
 * - 无 OAuth dance / 无 refresh token / 无 scope
 * - JWT 过期即失效,过期 token 用户在 UI 重新创建
 * - token 一旦签发,继承该用户**所有权限**(包括 admin 路由)— 严禁 ship 任何写端点
 *
 * 当前用户身份解析:无 `/users/me`,Linkwarden 需用 JWT 载荷解码拿 `user.id`(本项目 Phase A 简化:
 * 调用 `/api/v1/users` 返同订阅成员列表,从中匹配 JWT 签发者;或留待 Phase B 用 next-auth/jwt decode 工具)
 *
 * 命令(L0 风险, 纯只读):
 *   linkwarden.health    {}  → GET /api/v1/config 公开探活(DISABLE_REGISTRATION 等配置)
 *   linkwarden.user      {}  → GET /api/v1/users 鉴权后列同订阅成员(Phase A 用 fetchUsers 解析)
 *   linkwarden.collections {}  → GET /api/v1/collections 鉴权后列 collections(name + _count.links)
 *   linkwarden.links     {cursor?, collectionId?, searchQueryString?}  → GET /api/v1/links 鉴权后分页列链接
 *
 * **绝不**触碰 POST /api/v1/links / PUT /api/v1/links/{id} / DELETE /api/v1/links 等 mutating
 * **绝不**触碰 POST /api/v1/tokens / DELETE /api/v1/tokens(即使持有 token 也不应签发新 token)
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_LINKWARDEN_URL     默认 http://127.0.0.1:3000
 *   PRISIR_LINKWARDEN_API_KEY Bearer JWT(Linkwarden Settings → API Tokens)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function lwConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_LINKWARDEN_URL || 'http://127.0.0.1:3000',
    token: process.env.PRISIR_LINKWARDEN_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── /api/v1/config 公开探活(无需 Bearer,但 SDK 统一要求 token 非空)──
async function probeHealth() {
  const cfg = lwConfig();
  const t0 = Date.now();
  const r = await httpGet({ config: cfg, path: '/api/v1/config' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      linkwarden_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v1/config 返 {response:{...}, status} 或直接 {...} 取决于版本
  const data = r.parsed && r.parsed.response ? r.parsed.response : r.parsed || {};
  return {
    ok: true, alive: true,
    linkwarden_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    disable_registration: Boolean(data.DISABLE_REGISTRATION),
    disable_deprecated_routes: Boolean(data.DISABLE_DEPRECATED_ROUTES),
    demo_mode: Boolean(data.NEXT_PUBLIC_DEMO),
    last_error: '',
  };
}

// ── GET /api/v1/users 鉴权后列同订阅成员 ───────────────────
async function fetchUsers() {
  const cfg = lwConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/users' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // 响应: {response:[{id, name, username, email, emailVerified, createdAt}], status:200}
  const data = r.parsed && r.parsed.response ? r.parsed.response : (Array.isArray(r.parsed) ? r.parsed : []);
  return {
    ok: true, alive: true,
    users: data.map((u) => ({
      id: Number(u.id) || 0,
      name: String(u.name || ''),
      username: String(u.username || ''),
      email: String(u.email || ''),
      email_verified: u.emailVerified ? String(u.emailVerified) : '',
      created_at: String(u.createdAt || ''),
    })),
    total: data.length,
    last_error: '',
  };
}

// ── GET /api/v1/collections 鉴权后列 collections ─────────────
async function fetchCollections() {
  const cfg = lwConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/collections' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // 响应: {response:[{id, name, createdAt, _count:{links:N}, parent, members:[...]}], status:200}
  const data = r.parsed && r.parsed.response ? r.parsed.response : (Array.isArray(r.parsed) ? r.parsed : []);
  return {
    ok: true, alive: true,
    collections: data.map((c) => ({
      id: Number(c.id) || 0,
      name: String(c.name || ''),
      created_at: String(c.createdAt || ''),
      link_count: c._count && typeof c._count.links === 'number' ? c._count.links : 0,
      parent_id: c.parent && c.parent.id ? Number(c.parent.id) : null,
      members_count: Array.isArray(c.members) ? c.members.length : 0,
    })),
    total: data.length,
    last_error: '',
  };
}

// ── GET /api/v1/links 鉴权后分页列链接(cursor-based 分页)───
async function fetchLinks(args = {}) {
  const cfg = lwConfig();
  const qs = new URLSearchParams();
  if (args.cursor !== undefined && args.cursor !== null) qs.set('cursor', String(args.cursor));
  if (args.collectionId !== undefined && args.collectionId !== null) qs.set('collectionId', String(args.collectionId));
  if (args.searchQueryString) qs.set('searchQueryString', String(args.searchQueryString));
  if (args.pinnedOnly === true) qs.set('pinnedOnly', 'true');
  const path = `/api/v1/links${qs.toString() ? '?' + qs.toString() : ''}`;
  const r = await httpGet({ config: cfg, path });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // 响应: {response:[{id, name, url, description, tags:[{id,name}], collection:{id,name}, pinnedBy:[...]}], status:200}
  const data = r.parsed && r.parsed.response ? r.parsed.response : (Array.isArray(r.parsed) ? r.parsed : []);
  const nextCursor = data.length > 0 ? Number(data[data.length - 1].id) : null;
  return {
    ok: true, alive: true,
    links: data.map((l) => ({
      id: Number(l.id) || 0,
      name: String(l.name || ''),
      url: String(l.url || ''),
      description: String(l.description || '').slice(0, 500),
      tags: Array.isArray(l.tags) ? l.tags.map((t) => ({ id: Number(t.id) || 0, name: String(t.name || '') })) : [],
      collection_id: l.collection && l.collection.id ? Number(l.collection.id) : null,
      collection_name: l.collection && l.collection.name ? String(l.collection.name) : '',
      pinned: Array.isArray(l.pinnedBy) && l.pinnedBy.length > 0,
      pinned_count: Array.isArray(l.pinnedBy) ? l.pinnedBy.length : 0,
    })),
    total: data.length,
    next_cursor: nextCursor,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'linkwarden-bridge-status',
  name: 'Linkwarden 自托管书签/稍后读(只读, Bearer JWT)',
  version: '0.1.0',
});

ext.registerCommand('linkwarden.health', async () => probeHealth());
ext.registerCommand('linkwarden.user', async () => fetchUsers());
ext.registerCommand('linkwarden.collections', async () => fetchCollections());
ext.registerCommand('linkwarden.links', async (args) => fetchLinks(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchUsers, fetchCollections, fetchLinks,
    lwConfig,
  };
}