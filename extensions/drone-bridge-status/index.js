'use strict';

/**
 * drone-bridge-status v0.1.0 — Drone CI 持续集成(只读, Bearer token)
 *
 * 数据来源: Drone CI 自家 REST API(默认 127.0.0.1:8080/api)。
 *            Drone CI 是云原生 CI/CD 平台(用 Docker 跑构建),REST API 完整。
 * 鉴权:    `Authorization: Bearer <user-token>` 标准 Bearer(RFC 6750),
 *          token 在 Drone User Profile → User Token 生成(也可 OAuth 流程)。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Drone CI 是 bearer-client.js SDK **第四个用户**
 * (前 Audiobookshelf + Kavita + Gitea)— 验证 SDK 跨域复用(从媒体 → CI/CD)。
 *
 * 命令(L0 风险, 纯只读):
 *   drone.health       {}  → { ok, alive, drone_url, latency_ms, http_status, user, last_error }
 *   drone.repos        {}  → { ok, alive, repos: [{id, slug, name, active, config}], last_error }
 *   drone.recent_builds {} → { ok, alive, builds: [{id, repo, number, status, event, branch}], last_error }
 *
 * **绝不**触碰 POST /api/repos 创建仓库 / POST /api/builds 触发构建 / DELETE 任何 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_DRONE_URL     默认 http://127.0.0.1:8080
 *   PRISIR_DRONE_TOKEN   Bearer user token(Drone User Profile → User Token)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function drConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_DRONE_URL || 'http://127.0.0.1:8080',
    token: process.env.PRISIR_DRONE_TOKEN || '',
    timeoutMs: 5000,
  });
}

// ── /api/user 健康检查(需鉴权,但能验证 token 有效)────────
async function probeHealth() {
  const cfg = drConfig();
  const t0 = Date.now();
  // /api/user 是鉴权 + 用户信息探活的合并端点
  const r = await httpGet({ config: cfg, path: '/api/user' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      drone_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/user 返 { id, login, email, active, admin }
  return {
    ok: true, alive: true,
    drone_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    user: {
      id: Number(r.parsed && r.parsed.id ? r.parsed.id : 0),
      login: String(r.parsed && r.parsed.login ? r.parsed.login : ''),
      email: String(r.parsed && r.parsed.email ? r.parsed.email : ''),
      admin: Boolean(r.parsed && r.parsed.admin),
      active: Boolean(r.parsed && r.parsed.active),
    },
    last_error: '',
  };
}

// ── /api/user/repos 当前用户仓库列表 ────────────────────
async function fetchRepos() {
  const cfg = drConfig();
  const r = await httpGet({ config: cfg, path: '/api/user/repos' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/user/repos 返 Repo[] 数组
  const list = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    repos: list.map((r) => ({
      id: Number(r.id || 0),
      slug: String(r.slug || ''),
      name: String(r.name || ''),
      full_name: String(r.full_name || ''),
      active: Boolean(r.active),
      private: Boolean(r.private),
      config: String(r.config || ''),
      last_build: {
        number: Number((r.last_build && r.last_build.number) || 0),
        status: String((r.last_build && r.last_build.status) || ''),
        event: String((r.last_build && r.last_build.event) || ''),
      },
    })),
    last_error: '',
  };
}

// ── /api/user/feed 当前用户最近 build 列表 ───────────────
async function fetchRecentBuilds(args = {}) {
  const cfg = drConfig();
  const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 50);
  const r = await httpGet({ config: cfg, path: `/api/user/feed?limit=${limit}` });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/user/feed 返 Activity[] 数组,每个含 repo + build 信息
  const list = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    builds: list.map((a) => ({
      id: Number((a.build && a.build.id) || 0),
      number: Number((a.build && a.build.number) || 0),
      status: String((a.build && a.build.status) || ''),     // 'success' | 'failure' | 'pending' | 'running' | 'killed'
      event: String((a.build && a.build.event) || ''),       // 'push' | 'pull_request' | 'tag' | ...
      branch: String((a.build && a.build.branch) || ''),
      repo_slug: String((a.repo && a.repo.slug) || ''),
      repo_name: String((a.repo && a.repo.name) || ''),
      created_at: Number((a.build && a.build.created_at) || 0),
    })),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'drone-bridge-status',
  name: 'Drone CI 持续集成(只读, Bearer token)',
  version: '0.1.0',
});

ext.registerCommand('drone.health', async () => probeHealth());
ext.registerCommand('drone.repos', async () => fetchRepos());
ext.registerCommand('drone.recent_builds', async (args) => fetchRecentBuilds(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchRepos, fetchRecentBuilds,
    drConfig,
  };
}
