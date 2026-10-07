'use strict';

/**
 * firefly-iii-bridge-status v0.1.0 — Firefly III 自托管个人理财(只读, Bearer PAT)
 *
 * 数据来源: Firefly III REST API(自托管默认 http://127.0.0.1:8080/api/v1)。
 *            Firefly III 是 PHP+PostgreSQL 的自托管个人理财 / 预算追踪系统(JSON:API 风格 envelope)。
 * 鉴权:    `Authorization: Bearer <personal_access_token>` 标准 Bearer(RFC 6750),
 *          token 在 Firefly III Profile → OAuth → Personal Access Tokens 创建。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Firefly III 是 bearer-client.js SDK **第九个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud/Outline/BookStack)。
 * **跨入个人理财领域**(前 8 用户跨域 7 类)——bearer SDK 跨域累计 8 类:媒体/漫画/代码/CI/分析/云/知识/wiki/**理财**。
 *
 * JSON:API envelope 解析:Firefly 返 `{data: {type, id, attributes: {...}}, meta: {pagination: {...}}}`
 * 我们 flatten 成 attributes 字段直接对外,简化上层消费。
 *
 * 命令(L0 风险, 纯只读):
 *   firefly.health    {}  → { ok, alive, firefly_url, latency_ms, http_status, last_error, version }
 *   firefly.user      {}  → GET /api/v1/about/user 返当前用户(邮箱 + role + 是否 admin)
 *   firefly.accounts  {}  → GET /api/v1/accounts 返账户列表(asset / expense / revenue 等)
 *
 * **绝不**触碰 POST /api/v1/transactions / PUT /api/v1/accounts/{id} / POST /api/v1/budgets 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_FIREFLY_URL     默认 http://127.0.0.1:8080
 *   PRISIR_FIREFLY_API_KEY Bearer PAT(Firefly III Profile → OAuth → Personal Access Tokens)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function ffConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_FIREFLY_URL || 'http://127.0.0.1:8080',
    token: process.env.PRISIR_FIREFLY_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── 扁平化 JSON:API envelope: data.attributes → 直接展开 ─
function flattenAttrs(parsed) {
  // Firefly 单条对象: {data: {type, id, attributes: {...}}}
  if (parsed && parsed.data && typeof parsed.data === 'object' && !Array.isArray(parsed.data)
      && parsed.data.attributes && typeof parsed.data.attributes === 'object') {
    return { ...parsed.data.attributes, _id: parsed.data.id, _type: parsed.data.type };
  }
  // Firefly 列表: {data: [{type, id, attributes: {...}}], meta: {pagination: {...}}}
  if (parsed && Array.isArray(parsed.data)) {
    return parsed.data.map((it) => {
      const attrs = (it && it.attributes && typeof it.attributes === 'object') ? it.attributes : {};
      return { ...attrs, _id: it.id, _type: it.type };
    });
  }
  return null;
}

// ── GET /api/v1/about 系统版本 + driver(探活)─────────────
async function probeHealth() {
  const cfg = ffConfig();
  const t0 = Date.now();
  // /api/v1/about 真 REST GET,SDK httpGet 复用
  const r = await httpGet({ config: cfg, path: '/api/v1/about' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      firefly_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const flat = flattenAttrs(r.parsed);
  const attrs = flat || {};
  return {
    ok: true, alive: true,
    firefly_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    version: String(attrs.version || ''),
    api_version: String(attrs.api_version || ''),
    driver: String(attrs.driver || ''),
    php_version: String(attrs.php_version || ''),
    last_error: '',
  };
}

// ── GET /api/v1/about/user 当前用户(role + email)────────────
async function fetchUser() {
  const cfg = ffConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/about/user' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // 响应: {data: {type:'users', id:'1', attributes: {email, role, blocked, blocked_code}}}
  const flat = flattenAttrs(r.parsed);
  const attrs = flat || {};
  return {
    ok: true, alive: true,
    user: {
      id: String(attrs._id || ''),
      email: String(attrs.email || ''),
      role: String(attrs.role || ''),
      blocked: Boolean(attrs.blocked),
      is_admin: String(attrs.role || '').toLowerCase() === 'owner',
    },
    last_error: '',
  };
}

// ── GET /api/v1/accounts 账户列表(资产 / 支出 / 收入)───
async function fetchAccounts() {
  const cfg = ffConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/accounts?limit=100' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // 响应: {data: [{type:'accounts', id:..., attributes:{name, type, current_balance, currency_code, account_role}}], meta:{pagination:{total, count, per_page}}}
  const list = flattenAttrs(r.parsed) || [];
  const total = r.parsed && r.parsed.meta && r.parsed.meta.pagination && typeof r.parsed.meta.pagination.total === 'number'
    ? r.parsed.meta.pagination.total
    : list.length;
  return {
    ok: true, alive: true,
    accounts: list.map((a) => ({
      id: String(a._id || ''),
      type: String(a.type || ''),
      name: String(a.name || ''),
      account_type: String(a.type || ''),                       // Firefly 用 type 字段
      current_balance: String(a.current_balance || '0'),
      currency_code: String(a.currency_code || ''),
      account_role: String(a.account_role || ''),
      active: Boolean(a.active),
      notes: String(a.notes || ''),
    })),
    total,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'firefly-iii-bridge-status',
  name: 'Firefly III 自托管理财(只读, Bearer PAT)',
  version: '0.1.0',
});

ext.registerCommand('firefly.health', async () => probeHealth());
ext.registerCommand('firefly.user', async () => fetchUser());
ext.registerCommand('firefly.accounts', async () => fetchAccounts());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchUser, fetchAccounts,
    ffConfig, flattenAttrs,
  };
}