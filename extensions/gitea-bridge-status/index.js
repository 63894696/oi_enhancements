'use strict';

/**
 * gitea-bridge-status v0.1.0 — Gitea 代码托管(只读, Bearer token)
 *
 * 数据来源: Gitea 自家 REST API(默认 127.0.0.1:3000/api/v1)。
 *            Gitea 是自托管 Git 服务(GitHub/GitLab 替代),REST API 完整。
 * 鉴权:    `Authorization: Bearer <access-token>` 标准 Bearer(RFC 6750),
 *          token 在 Gitea User Settings → Applications 生成(也叫 Personal Access Token)。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Gitea 是 bearer-client.js SDK **第三个用户**
 * (前 Audiobookshelf + Kavita)— 触发 SDK 抽取。SDK 标准 Bearer scheme,
 * 与 custom-auth SDK 自定义 header 模式并存。
 *
 * 命令(L0 风险, 纯只读):
 *   gitea.health    {}  → { ok, alive, gitea_url, latency_ms, http_status, version, last_error }
 *   gitea.repos     {limit?, q?}  → { ok, alive, repos: [{id, name, full_name, owner, stars}], total, last_error }
 *   gitea.orgs      {}  → { ok, alive, orgs: [{id, username, full_name}], last_error }
 *
 * **绝不**触碰 POST /user/repos 创建仓库 / DELETE /api/v1/repos/{owner}/{repo} 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_GITEA_URL        默认 http://127.0.0.1:3000
 *   PRISIR_GITEA_TOKEN      Bearer access token(Gitea User Settings → Applications)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function gtConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_GITEA_URL || 'http://127.0.0.1:3000',
    token: process.env.PRISIR_GITEA_TOKEN || '',
    timeoutMs: 5000,
  });
}

// ── /api/v1/version 健康检查(无需鉴权)──────────────────
async function probeHealth() {
  const cfg = gtConfig();
  const t0 = Date.now();
  // /api/v1/version 是 public,无需 Bearer,也能用于探活
  const r = await httpGet({ config: cfg, path: '/api/v1/version' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      gitea_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v1/version 返 {version: '1.21.x'}
  return {
    ok: true, alive: true,
    gitea_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    version: String(r.parsed && r.parsed.version ? r.parsed.version : ''),
    last_error: '',
  };
}

// ── /api/v1/repos/search 搜索/列出仓库 ─────────────────
async function fetchRepos(args = {}) {
  const cfg = gtConfig();
  const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 50);
  const q = String(args.q || '');
  let path = `/api/v1/repos/search?limit=${limit}`;
  if (q) path += `&q=${encodeURIComponent(q)}`;
  const r = await httpGet({ config: cfg, path });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v1/repos/search 返 { data: Repo[], ok: true, total: N }
  const top = r.parsed || {};
  const data = Array.isArray(top.data) ? top.data : (Array.isArray(top) ? top : []);
  return {
    ok: true, alive: true,
    repos: data.map((r) => ({
      id: Number(r.id || 0),
      name: String(r.name || ''),
      full_name: String(r.full_name || ''),
      owner: String(r.owner && r.owner.login ? r.owner.login : ''),
      description: String(r.description || ''),
      stars: Number(r.stars_count || 0),
      forks: Number(r.forks_count || 0),
      private: Boolean(r.private),
      fork: Boolean(r.fork),
    })),
    total: Number(top.total_count || data.length),
    last_error: '',
  };
}

// ── /api/v1/orgs 当前用户所属组织 ───────────────────────
async function fetchOrgs() {
  const cfg = gtConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/orgs' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v1/orgs 返 array of Organization
  const list = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    orgs: list.map((o) => ({
      id: Number(o.id || 0),
      username: String(o.username || ''),
      full_name: String(o.full_name || ''),
      description: String(o.description || ''),
      avatar_url: String(o.avatar_url || ''),
      visibility: String(o.visibility || 'public'),
    })),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'gitea-bridge-status',
  name: 'Gitea 代码托管(只读, Bearer token)',
  version: '0.1.0',
});

ext.registerCommand('gitea.health', async () => probeHealth());
ext.registerCommand('gitea.repos', async (args) => fetchRepos(args || {}));
ext.registerCommand('gitea.orgs', async () => fetchOrgs());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchRepos, fetchOrgs,
    gtConfig,
  };
}
