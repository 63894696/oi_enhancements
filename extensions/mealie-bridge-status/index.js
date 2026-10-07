'use strict';

/**
 * mealie-bridge-status v0.1.0 — Mealie 自托管食谱管理(只读, Bearer JWT)
 *
 * 数据来源: Mealie 自家 REST API(自托管默认 http://127.0.0.1:9000/api)。
 *            Mealie 是 Python+FastAPI+SQLite 的自托管食谱管理(类似 Paprika / CopyMeThat)。
 * 鉴权:    `Authorization: Bearer <long_lived_api_token | JWT>` 标准 Bearer(RFC 6750),
 *          长效 API token 在 Mealie User Profile → API Tokens 创建,
 *          或短期 JWT 由 `POST /api/auth/token` (OAuth2PasswordBearer form) 颁发。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Mealie 是 bearer-client.js SDK **第十个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud/Outline/BookStack/Firefly III)。
 * **跨入食谱/料理域**(前 9 用户跨 8 类)——bearer SDK 跨域累计 9 类:媒体/漫画/代码/CI/分析/云/wiki/知识/理财/**料理**。
 *
 * Mealie 端点风格: GET 公开端点(`/api/app/about`)无需 Bearer,鉴权与提权端点需 Bearer。
 * 响应包装: `{ page, per_page, total, total_pages, data: [...], next, previous }`(轻量 JSON:API 风格)。
 *
 * 命令(L0 风险, 纯只读):
 *   mealie.health   {}  → GET /api/app/about 公开探活(version + demoStatus + allowSignup)
 *   mealie.user     {}  → GET /api/users/self 鉴权后取当前用户(id + email + admin + groupId)
 *   mealie.recipes  {page?, perPage?}  → GET /api/recipes 鉴权后分页列食谱(slug + name + totalPages)
 *
 * **绝不**触碰 POST /api/recipes / PUT /api/recipes/{slug} / DELETE /api/recipes/{slug} 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_MEALIE_URL     默认 http://127.0.0.1:9000
 *   PRISIR_MEALIE_API_KEY Bearer 长效 API token 或 JWT
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function mlConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_MEALIE_URL || 'http://127.0.0.1:9000',
    token: process.env.PRISIR_MEALIE_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── GET /api/app/about 公开探活(无需鉴权,但 SDK 设计统一要 token)──────
async function probeHealth() {
  const cfg = mlConfig();
  const t0 = Date.now();
  // /api/app/about 公开端点 Mealie 不检查 auth,但 Bearer SDK 统一要求 token 非空
  // → 用户完全没配 token 时 SDK 早退 last_error='no credentials'(正确边界行为,提示用户去配)
  // → 用户配了 token 直接走 + Mealie 公开端点忽略 token
  const r = await httpGet({ config: cfg, path: '/api/app/about' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      mealie_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // 响应: { production, version, demoStatus, allowSignup, allowPasswordLogin, enableOidc, oidcProviderName, tokenTime, ... }
  const a = r.parsed || {};
  return {
    ok: true, alive: true,
    mealie_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    version: String(a.version || ''),
    demo_status: Boolean(a.demoStatus),
    allow_signup: Boolean(a.allowSignup),
    allow_password_login: Boolean(a.allowPasswordLogin),
    enable_oidc: Boolean(a.enableOidc),
    oidc_provider_name: String(a.oidcProviderName || ''),
    token_time_minutes: Number(a.tokenTime) || 0,
    last_error: '',
  };
}

// ── GET /api/users/self 当前用户(id + email + admin)────
async function fetchUser() {
  const cfg = mlConfig();
  const r = await httpGet({ config: cfg, path: '/api/users/self' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // 响应: { id, username, email, admin, groupId, householdId, fullName }
  const u = r.parsed || {};
  return {
    ok: true, alive: true,
    user: {
      id: String(u.id || ''),
      username: String(u.username || ''),
      email: String(u.email || ''),
      full_name: String(u.fullName || ''),
      admin: Boolean(u.admin),
      group_id: String(u.groupId || ''),
      household_id: String(u.householdId || ''),
    },
    last_error: '',
  };
}

// ── GET /api/recipes 鉴权后分页列食谱(轻量 JSON:API)───
async function fetchRecipes(args = {}) {
  const cfg = mlConfig();
  const page = Math.max(Number(args.page) || 1, 1);
  const perPage = Math.min(Math.max(Number(args.perPage) || 10, 1), 100);
  const path = `/api/recipes?page=${page}&perPage=${perPage}`;
  const r = await httpGet({ config: cfg, path });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // 响应: { page, per_page, total, total_pages, data: [{slug, name, description, ...}], next, previous }
  const list = Array.isArray(r.parsed && r.parsed.data) ? r.parsed.data : [];
  return {
    ok: true, alive: true,
    recipes: list.map((rp) => ({
      slug: String(rp.slug || ''),
      name: String(rp.name || ''),
      description: String(rp.description || '').slice(0, 300),
      image_url: String(rp.image || ''),
      prep_time: String(rp.prepTime || ''),
      perform_time: String(rp.performTime || ''),
      total_time: String(rp.totalTime || ''),
      recipe_category: Array.isArray(rp.recipeCategory) ? rp.recipeCategory.map((c) => String(c.name || c.slug || '')) : [],
      tags: Array.isArray(rp.tags) ? rp.tags.map((t) => String(t.name || t.slug || '')) : [],
      rating: Number(rp.rating) || 0,
      last_made: String(rp.lastMade || ''),
    })),
    page,
    per_page: perPage,
    total: r.parsed && typeof r.parsed.total === 'number' ? r.parsed.total : list.length,
    total_pages: r.parsed && typeof r.parsed.total_pages === 'number' ? r.parsed.total_pages : 1,
    next: String(r.parsed && r.parsed.next || ''),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'mealie-bridge-status',
  name: 'Mealie 自托管食谱(只读, Bearer JWT/API token)',
  version: '0.1.0',
});

ext.registerCommand('mealie.health', async () => probeHealth());
ext.registerCommand('mealie.user', async () => fetchUser());
ext.registerCommand('mealie.recipes', async (args) => fetchRecipes(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchUser, fetchRecipes,
    mlConfig,
  };
}