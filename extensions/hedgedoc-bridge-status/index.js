'use strict';

/**
 * hedgedoc-bridge-status v0.1.0 — HedgeDoc 协同 Markdown(只读, Bearer Token)
 *
 * 数据来源: HedgeDoc REST API v2(自托管默认 http://127.0.0.1:3000)。
 *            HedgeDoc 是 hedgedoc/hedgedoc 开发的自托管协同 Markdown 笔记
 *            (原名 CodiMD / HackMD 开源版, 2021 改名,2024+ 主线 v2.x 走 NestJS 重写)。
 *            支持多人实时协同编辑,常被团队用来记录 API key/凭据/会议纪要。
 * 鉴权:    **v2.x 推荐 Bearer Token**(`ApiTokenGuard` + `ensureTokenIsValid()`,仅 validUntil 过期检查,
 *          无签名)。admin 在 UI → Profile → API tokens 创建 long-lived token(可设过期)。
 *          v1.x 主流是 session cookie(s:` 前缀),**我们明确不接** — 需 inline dance,违反 SDK 零边界跨越。
 *
 * **Phase C SDK 复用(2026-10-10)**:HedgeDoc 是 bearer-client.js SDK **第十四个用户**
 * (前 13 用户跨 13 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签/笔记)。
 * **跨入协同笔记/Markdown 域**(作为第 14 类,与 SiYuan/Trilium 形成笔记三件套:个人/层级/协同)。
 * **零 SDK 边界跨越**(与 Linkwarden/Mealie/BookStack/Trilium 同 pattern:
 * `httpGet + describeAuth + makeConfig`)。
 *
 * **envelope 形状**:`/api/v2/notes` → 直数组(v1.x docs 明示:「returns an array of notes
 * owned by the logged-in user」)。NesJS v2.x 可能返 `{data, meta}`,**第一层兼容直数组**。
 *
 * **License** AGPL-3.0 — 纯只读客户端代理不触发传染(见 [[agpl-ship-boundary]] 决策)。
 *
 * **rate limit**:150 req / 300s 默认(`HD_SECURITY_RATE_LIMIT_PUBLIC_API_MAX`),够 Phase A。
 *
 * 借鉴原则 5「Token≠密码」中档(6/10)— API token 可重放到 validUntil 过期(默认 long-lived)。
 *
 * 命令(L0 风险, 纯只读):
 *   hedgedoc.health    {}  → GET / 主页 HTML 探活(无 /api/status 官方端点)
 *   hedgedoc.notes     {search?, limit?, skip?, view?}  → GET /api/v2/notes 列/搜索笔记
 *   hedgedoc.user      {}  → GET /api/v2/user 当前用户(替代 v1.x /me)
 *
 * **绝不**触碰:
 *   - POST /api/v2/notes / PUT / DELETE
 *   - GET /api/v2/notes/{id}/content(笔记正文,可能含密码)— Phase A 不抓
 *   - /api/private/* (v1.x session cookie,我们不接)
 *
 * P0 安全约束(产品级):
 *   1. **只白名单 GET**(`/`, `/api/v2/notes`, `/api/v2/user`)
 *   2. **不抓 content 字段**(协同笔记可能含密码/API key/凭据/医疗)
 *   3. 借鉴 [[agpl-ship-boundary]] 客户端代理 ship 边界(进程级隔离,不动服务端)
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_HEDGEDOC_URL     默认 http://127.0.0.1:3000
 *   PRISIR_HEDGEDOC_API_KEY Bearer API token(Profile → API tokens)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function hedgedocConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_HEDGEDOC_URL || 'http://127.0.0.1:3000',
    token: process.env.PRISIR_HEDGEDOC_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── 探活:无 /api/status 端点,fallback 到根 HTML ────────────
async function probeHealth() {
  const cfg = hedgedocConfig();
  const t0 = Date.now();
  // HedgeDoc 无 /api/status 官方端点(v1.x/v2.x 都无);用根路径 GET / 验活
  // 根路径返 HTML,SDK 解析为字符串而非 JSON — 我们只验 HTTP 状态 + 标题
  const r = await httpGet({ config: cfg, path: '/' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      hedgedoc_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // SDK body 字段是 utf8 string,HTML 解析提取 <title>
  const body = String(r.body || '');
  const titleMatch = body.match(/<title>([^<]+)<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : '';
  return {
    ok: true, alive: true,
    hedgedoc_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    page_title: title,
    is_hedgedoc: /hedgedoc|codi[_-]?md/i.test(title) || body.includes('hedgedoc') || body.includes('CodiMD'),
    last_error: '',
  };
}

// ── /api/v2/notes 列/搜索笔记 ──────────────────────────────
async function fetchNotes(args = {}) {
  const cfg = hedgedocConfig();
  const qs = new URLSearchParams();
  if (args.search) qs.set('search', String(args.search));
  if (args.limit) qs.set('limit', String(Math.max(1, Math.min(200, Number(args.limit) || 50))));
  if (args.skip !== undefined && args.skip !== null) qs.set('skip', String(Math.max(0, Number(args.skip) || 0)));
  if (args.view) qs.set('view', String(args.view));        // 'feed' | 'user' | 'all' 等
  if (args.ownedByUser === true) qs.set('ownedByUser', 'true');
  if (args.ally === true) qs.set('ally', 'true');
  const path = `/api/v2/notes${qs.toString() ? '?' + qs.toString() : ''}`;
  const r = await httpGet({ config: cfg, path });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // v1.x 返直数组,v2.x 可能返 {data, meta} — 第一层兼容
  let data = [];
  if (Array.isArray(r.parsed)) {
    data = r.parsed;
  } else if (r.parsed && Array.isArray(r.parsed.data)) {
    data = r.parsed.data;
  } else if (r.parsed && Array.isArray(r.parsed.notes)) {
    data = r.parsed.notes;
  }
  return {
    ok: true, alive: true,
    notes: data.map((n) => ({
      id: String(n.id || n.alias || ''),
      alias: String(n.alias || ''),
      title: String(n.title || n.alias || '(untitled)').slice(0, 300),
      owner_id: String(n.ownerId || n.owner || ''),
      owner_user_name: String(n.ownerUserName || n.userName || ''),
      created_at: String(n.createdAt || ''),
      updated_at: String(n.updatedAt || n.lastChanged || ''),
      view_count: Number(n.viewCount) || 0,
      tags: Array.isArray(n.tags) ? n.tags.map(String) : [],
      // 不抓 content(可能含密码)— 产品级 P0
      content_included: false,
    })),
    total: data.length,
    last_error: '',
  };
}

// ── /api/v2/user 当前用户 ──────────────────────────────────
async function fetchUser() {
  const cfg = hedgedocConfig();
  const r = await httpGet({ config: cfg, path: '/api/v2/user' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // v2.x 返 {id, name, userName, email, createdAt, ...} 直对象
  const u = r.parsed || {};
  return {
    ok: true, alive: true,
    user: {
      id: String(u.id || ''),
      name: String(u.name || u.displayName || ''),
      user_name: String(u.userName || u.username || ''),
      email: String(u.email || ''),
      email_verified: Boolean(u.emailConfirmed || u.emailVerified),
      created_at: String(u.createdAt || ''),
      is_admin: Boolean(u.isAdmin || u.admin),
      is_owner: Boolean(u.isOwner || u.owner),
    },
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'hedgedoc-bridge-status',
  name: 'HedgeDoc 协同 Markdown(只读, Bearer Token)',
  version: '0.1.0',
});

ext.registerCommand('hedgedoc.health', async () => probeHealth());
ext.registerCommand('hedgedoc.notes', async (args) => fetchNotes(args || {}));
ext.registerCommand('hedgedoc.user', async () => fetchUser());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchNotes, fetchUser,
    hedgedocConfig,
  };
}