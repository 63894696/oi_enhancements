'use strict';

/**
 * nextcloud-bridge-status v0.1.0 — Nextcloud 云盘(只读, OAuth2 Bearer)
 *
 * 数据来源: Nextcloud OCS API v1 + Files Sharing API v1(默认 127.0.0.1:80)。
 *            Nextcloud 是自托管云盘/协作平台(Owncloud fork),OCS (Open Collaboration Services) 是 REST API。
 * 鉴权:    `Authorization: Bearer <oauth2-access-token>` 标准 Bearer(RFC 6750),
 *          token 在 Nextcloud 通过 OAuth2 流程获取(用户先注册 app + 走 auth code flow)。
 *          Phase A 简化:用户手填已签发的 access token(env `PRISIR_NEXTCLOUD_TOKEN`),
 *          不在 Phase A 范围跑 OAuth dance(OAuth 流程是交互式,Phase A 只读 + 100% 本地)。
 *          借鉴原则 5「Token≠密码」中档(6/10)— OAuth token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Nextcloud 是 bearer-client.js SDK **第六个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible)。
 *
 * **SDK 边界决策**:Nextcloud OCS API 需要额外 header `OCS-APIRequest: true`
 * (SDK 当前 httpGet 透传 Bearer 不支持自定义 extraHeaders)。
 * **Nextcloud 扩展 inline httpGetOcS helper** 30 行(类似 Plausible httpPostJson 模式),
 * 复用 SDK bearerHeader / makeConfig / 失败语义。这是 SDK 边界:
 *  - httpGet: 标准 Bearer,SDK 复用
 *  - httpGetOcS: OCS + extraHeaders,inline 避免 SDK 膨胀
 *
 * 命令(L0 风险, 纯只读):
 *   nextcloud.health   {}  → { ok, alive, nextcloud_url, latency_ms, http_status, last_error }
 *   nextcloud.users    {}  → { ok, alive, users: [{id, enabled}] }
 *   nextcloud.shares   {path?} → { ok, alive, shares: [{id, share_type, permissions}] }
 *
 * **绝不**触碰 POST /shares 创建分享 / DELETE /shares/{id} 任何 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_NEXTCLOUD_URL    默认 http://127.0.0.1:80
 *   PRISIR_NEXTCLOUD_TOKEN  Bearer OAuth2 access token(用户走 OAuth flow 后填)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, bearerHeader, describeAuth } = require('../_scaffold/bearer-client');
const http = require('http');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function ncConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_NEXTCLOUD_URL || 'http://127.0.0.1:80',
    token: process.env.PRISIR_NEXTCLOUD_TOKEN || '',
    timeoutMs: 5000,
  });
}

// ── inline httpGetOcS helper(OCS + extraHeaders + JSON accept,SDK 不膨胀)──
function httpGetOcS(args) {
  const cfg = args && args.config;
  const path = String((args && args.path) || '/');
  return new Promise((resolve) => {
    const token = cfg ? cfg.token_() : '';
    if (!token) {
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url: '',
        error: `no credentials — set Bearer OAuth2 token`,
      });
    }
    const url = `${cfg.baseUrl_()}${path}`;
    const headers = {
      ...bearerHeader(token),
      'OCS-APIRequest': 'true',
      'Accept': 'application/json',
    };
    const req = http.get(url, { timeout: cfg.timeoutMs_(), headers }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        // OCS 返 JSON: { ocs: { meta: {...}, data: ... } }
        let parsed = null;
        try { parsed = JSON.parse(buf); } catch {}
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode,
          body: buf,
          parsed,
          url,
          ...(res.statusCode >= 400 ? { error: `HTTP ${res.statusCode}` } : {}),
        });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', (e) => resolve({
      ok: false, status: 0, body: '', parsed: null, url, error: e.message,
    }));
  });
}

// ── /ocs/v1.php/cloud/users 探活(鉴权 + 用户列表探活合并)──
async function probeHealth() {
  const cfg = ncConfig();
  const t0 = Date.now();
  const r = await httpGetOcS({ config: cfg, path: '/ocs/v1.php/cloud/users' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      nextcloud_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    nextcloud_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    last_error: '',
  };
}

// ── /ocs/v1.php/cloud/users 详细用户列表(只读字段)──────────
async function fetchUsers() {
  const cfg = ncConfig();
  const r = await httpGetOcS({ config: cfg, path: '/ocs/v1.php/cloud/users' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // OCS /cloud/users 返 { ocs: { meta, data: { users: ["alice", "bob"] } } }
  const data = r.parsed && r.parsed.ocs && r.parsed.ocs.data;
  const list = (data && Array.isArray(data.users)) ? data.users : [];
  return {
    ok: true, alive: true,
    users: list.map((id) => ({
      id: String(id),
      enabled: true,                 // OCS list 不返 enabled 字段,Phase A 假设 enabled
    })),
    last_error: '',
  };
}

// ── /ocs/v2.php/apps/files_sharing/api/v1/shares 分享列表 ──────
async function fetchShares(args = {}) {
  const cfg = ncConfig();
  let path = '/ocs/v2.php/apps/files_sharing/api/v1/shares';
  if (args.path) path += `?path=${encodeURIComponent(String(args.path))}`;
  const r = await httpGetOcS({ config: cfg, path });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // OCS /shares 返 { ocs: { meta, data: [{id, share_type, permissions, ...}] } }
  const data = r.parsed && r.parsed.ocs && r.parsed.ocs.data;
  const list = Array.isArray(data) ? data : [];
  return {
    ok: true, alive: true,
    shares: list.map((s) => ({
      id: Number(s.id || 0),
      share_type: Number(s.share_type || 0),     // 0=user, 1=group, 3=public link
      uid_owner: String(s.uid_owner || ''),
      displayname_owner: String(s.displayname_owner || ''),
      path: String(s.path || ''),
      permissions: Number(s.permissions || 0),
      expiration: String(s.expiration || ''),
    })),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'nextcloud-bridge-status',
  name: 'Nextcloud 云盘(只读, OAuth2 Bearer)',
  version: '0.1.0',
});

ext.registerCommand('nextcloud.health', async () => probeHealth());
ext.registerCommand('nextcloud.users', async () => fetchUsers());
ext.registerCommand('nextcloud.shares', async (args) => fetchShares(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchUsers, fetchShares,
    ncConfig, httpGetOcS,
  };
}
