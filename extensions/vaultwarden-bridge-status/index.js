'use strict';

/**
 * vaultwarden-bridge-status v0.1.0 — Vaultwarden 自托管密码管理器(只读, OAuth2 client_credentials)
 *
 * 数据来源: Vaultwarden REST API(自托管默认 http://127.0.0.1:80,容器内 8080→80)。
 *            Vaultwarden 是 Bitwarden 兼容密码管理器的 Rust 自托管实现。
 *            Bitwarden 客户端走 OAuth2 client_credentials 颁发 JWT,数据端零知识加密,
 *            服务端仅返 EncString(2.<iv>|<ct>|<mac>)——Phase A 我们只读 metadata,
 *            不解密(password/notes 永远是 EncString,我们只取元数据 name/type 等)。
 * 鉴权:    **两步**(与已 ship 10 用户不同):
 *            Step 1: POST /identity/connect/token  (form-urlencoded,client_credentials grant)
 *                    → 返 { access_token, expires_in:3600, token_type:'Bearer', scope:'api' }
 *            Step 2: 用 access_token 走 Authorization: Bearer 调 /api/...
 *            借鉴原则 5「Token≠密码」中档(6/10)— JWT 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Vaultwarden 是 bearer-client.js SDK **第十一个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud/Outline/BookStack/Firefly III/Mealie)。
 * **跨入凭据/安全域**(前 10 用户跨 9 类)——bearer SDK 跨域累计 10 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/**凭据**。
 *
 * **OAuth2 client_credentials inline factory(30 行)**:
 * - 不改 Bearer SDK(SDK 抽象不变),只在扩展内 inline 一个 `fetchAccessToken()` 工厂
 * - token 缓存 3600s(与 Vaultwarden expires_in 对齐,Phase A 不需 refresh)
 * - 与 SDK 关系 =「在 SDK 上方薄薄一层」,不触 SDK 边界
 * - 后续遇类似服务(Confluence Cloud、GitLab OAuth2)直接复用工厂
 *
 * 命令(L0 风险, 纯只读):
 *   vault.health     {}  → GET /alive 公开探活(无需 token,RFC3339 UTC)
 *   vault.profile    {}  → GET /api/accounts/profile 鉴权后当前用户(Id + Email + TwoFactorEnabled)
 *   vault.folders    {}  → GET /api/folders 鉴权后列文件夹(Name 是 EncString,我们只返 Id + RevisionDate + Name 截断)
 *   vault.ciphers    {}  → GET /api/ciphers 鉴权后列密码项(Type 1=Login/2=SecureNote/3=Card/4=Identity + Favorite + FolderId)
 *
 * **绝不**触碰 POST /api/accounts/register/password/keys/kdf 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_VAULT_URL          默认 http://127.0.0.1
 *   PRISIR_VAULT_CLIENT_ID    user.<uuid>(Vaultwarden 用户 Profile → API Key 复制,形式 user.<uuid>)
 *   PRISIR_VAULT_CLIENT_SECRET 该 API key 的 secret
 *   PRISIR_VAULT_SCOPE        默认 api
 */

const http = require('http');
const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config + 内部 token 缓存 ─
let _tokenCache = { token: '', expires_at: 0 };

function vaultConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_VAULT_URL || 'http://127.0.0.1',
    token: process.env.PRISIR_VAULT_CLIENT_ID || '',
    timeoutMs: 5000,
  });
}

function vaultCredentials() {
  return {
    client_id: process.env.PRISIR_VAULT_CLIENT_ID || '',
    client_secret: process.env.PRISIR_VAULT_CLIENT_SECRET || '',
    scope: process.env.PRISIR_VAULT_SCOPE || 'api',
    base_url: process.env.PRISIR_VAULT_URL || 'http://127.0.0.1',
  };
}

// ── OAuth2 client_credentials inline factory(30 行)──
// POST /identity/connect/token  form-urlencoded → { access_token, expires_in, ... }
function fetchAccessToken({ baseUrl, client_id, client_secret, scope, timeoutMs = 5000 } = {}) {
  return new Promise((resolve) => {
    if (!client_id || !client_secret) {
      return resolve({
        ok: false, access_token: '', expires_in: 0, error: 'no credentials — set VAULT_CLIENT_ID and VAULT_CLIENT_SECRET',
      });
    }
    const body = `grant_type=client_credentials&scope=${encodeURIComponent(scope)}&client_id=${encodeURIComponent(client_id)}&client_secret=${encodeURIComponent(client_secret)}`;
    const url = `${baseUrl.replace(/\/+$/, '')}/identity/connect/token`;
    const req = http.request(url, {
      method: 'POST', timeout: timeoutMs,
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body),
      },
    }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let parsed = null;
        try { parsed = JSON.parse(buf); } catch {}
        if (res.statusCode >= 200 && res.statusCode < 300 && parsed && parsed.access_token) {
          resolve({
            ok: true,
            access_token: String(parsed.access_token),
            expires_in: Number(parsed.expires_in) || 3600,
            url,
          });
        } else {
          resolve({
            ok: false, access_token: '', expires_in: 0, url,
            status: res.statusCode,
            error: parsed && parsed.error_description
              ? `${parsed.error}: ${parsed.error_description}`
              : `HTTP ${res.statusCode}`,
          });
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve({
      ok: false, access_token: '', expires_in: 0, url, error: e.message,
    }));
    req.write(body);
    req.end();
  });
}

// ── token 取 cache 或 fetch + 续约 ─
async function getAccessToken() {
  const now = Math.floor(Date.now() / 1000);
  if (_tokenCache.token && _tokenCache.expires_at > now + 30) {
    return { ok: true, access_token: _tokenCache.token, expires_in: _tokenCache.expires_at - now, cached: true };
  }
  const creds = vaultCredentials();
  const r = await fetchAccessToken({
    baseUrl: creds.base_url,
    client_id: creds.client_id,
    client_secret: creds.client_secret,
    scope: creds.scope,
  });
  if (r.ok) {
    _tokenCache = {
      token: r.access_token,
      expires_at: now + (r.expires_in || 3600),
    };
  }
  return { ...r, cached: false };
}

// ── 鉴权后 httpGet 包装(临时拼 token 配置)─────
async function authedGet({ path }) {
  const creds = vaultCredentials();
  const t = await getAccessToken();
  if (!t.ok) {
    return { ok: false, status: 0, body: '', parsed: null, url: '', error: t.error };
  }
  const cfg = makeConfig({
    baseUrl: creds.base_url,
    token: t.access_token,
    timeoutMs: 5000,
  });
  return httpGet({ config: cfg, path });
}

// ── GET /alive 公开探活(无需 token,RFC3339 UTC 时间戳)──────
async function probeHealth() {
  const creds = vaultCredentials();
  const t0 = Date.now();
  // /alive 是公开端点(无 token),SDK 临时拼 placeholder 让 SDK 通过
  const probeCfg = makeConfig({
    baseUrl: creds.base_url, token: 'public-probe-no-token-required', timeoutMs: 5000,
  });
  const r = await httpGet({ config: probeCfg, path: '/alive' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      vault_url: creds.base_url, latency_ms: dt, http_status: r.status,
      auth: { mode: 'oauth2-client_credentials', has_credentials: Boolean(creds.client_id && creds.client_secret) },
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    vault_url: creds.base_url, latency_ms: dt, http_status: r.status,
    auth: { mode: 'oauth2-client_credentials', has_credentials: Boolean(creds.client_id && creds.client_secret) },
    server_time: String(r.body || '').trim(),
    last_error: '',
  };
}

// ── GET /api/accounts/profile 当前用户(Id + Email + 2FA)──
async function fetchProfile() {
  const r = await authedGet({ path: '/api/accounts/profile' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // Vaultwarden PascalCase: { Id, Name, Email, EmailVerified, Premium, TwoFactorEnabled, Organizations: [...] }
  const p = r.parsed || {};
  return {
    ok: true, alive: true,
    profile: {
      id: String(p.Id || ''),
      name: String(p.Name || ''),
      email: String(p.Email || ''),
      email_verified: Boolean(p.EmailVerified),
      premium: Boolean(p.Premium),
      two_factor_enabled: Boolean(p.TwoFactorEnabled),
      organizations_count: Array.isArray(p.Organizations) ? p.Organizations.length : 0,
    },
    last_error: '',
  };
}

// ── GET /api/folders 列文件夹(Name 是 EncString,我们只返 Id + RevisionDate)──
async function fetchFolders() {
  const r = await authedGet({ path: '/api/folders' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // { Data: [{Id, Name(Encrypted), RevisionDate, Object:'folder'}] }
  const data = Array.isArray(r.parsed && r.parsed.Data) ? r.parsed.Data : [];
  return {
    ok: true, alive: true,
    folders: data.map((f) => ({
      id: String(f.Id || ''),
      name_encrypted: Boolean(String(f.Name || '').startsWith('2.')),       // EncString 以 '2.' 开头
      revision_date: String(f.RevisionDate || ''),
    })),
    total: data.length,
    last_error: '',
  };
}

// ── GET /api/ciphers 列密码项(Type + Favorite + FolderId)──
async function fetchCiphers() {
  const r = await authedGet({ path: '/api/ciphers' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // { Data: [{Id, Type:1/2/3/4, Name(Encrypted), FolderId, Favorite, RevisionDate, Login:{Uris:[...]} }] }
  const data = Array.isArray(r.parsed && r.parsed.Data) ? r.parsed.Data : [];
  const typeNames = { 1: 'login', 2: 'secure_note', 3: 'card', 4: 'identity' };
  return {
    ok: true, alive: true,
    ciphers: data.map((c) => ({
      id: String(c.Id || ''),
      type: Number(c.Type) || 0,
      type_name: typeNames[Number(c.Type) || 0] || 'unknown',
      favorite: Boolean(c.Favorite),
      folder_id: String(c.FolderId || ''),
      name_encrypted: Boolean(String(c.Name || '').startsWith('2.')),
      has_login_uri: Boolean(c.Login && Array.isArray(c.Login.Uris) && c.Login.Uris.length > 0),
      login_uri_count: (c.Login && Array.isArray(c.Login.Uris)) ? c.Login.Uris.length : 0,
      revision_date: String(c.RevisionDate || ''),
    })),
    total: data.length,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'vaultwarden-bridge-status',
  name: 'Vaultwarden 自托管密码管理器(只读, OAuth2 client_credentials)',
  version: '0.1.0',
});

ext.registerCommand('vault.health', async () => probeHealth());
ext.registerCommand('vault.profile', async () => fetchProfile());
ext.registerCommand('vault.folders', async () => fetchFolders());
ext.registerCommand('vault.ciphers', async () => fetchCiphers());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchProfile, fetchFolders, fetchCiphers,
    vaultConfig, vaultCredentials,
    fetchAccessToken, getAccessToken, authedGet,
    _tokenCache,                                                   // 测试时手动重置
    _resetTokenCache: () => { _tokenCache = { token: '', expires_at: 0 }; },
  };
}