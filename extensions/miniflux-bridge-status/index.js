'use strict';

/**
 * miniflux-bridge-status v0.1.0 — Miniflux RSS(只读,X-Auth-Token)
 *
 * 数据来源: Miniflux REST API(默认 127.0.0.1:8080/v1)。
 *            Miniflux 是自托管 RSS reader,UI/API 分离,REST API 文档完善。
 * 鉴权:    `X-Auth-Token: <token>` 自定义头(不是 Authorization Bearer)
 *          借鉴原则 5「Token≠密码」中档(7/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Miniflux 是 custom-auth-client.js SDK
 * **第二个用户**(前一个 Komga mode='apiKey')。Miniflux 用 mode='custom' 验证
 * SDK 三模式设计真实落地:
 *     - mode='apiKey'   →  X-API-Key: <key>     (Komga)
 *     - mode='basic'    →  Authorization: Basic  (Komga fallback)
 *     - mode='custom'   →  <header>: <token>     (Miniflux / Plex / Jellyfin)
 *
 * 命令(L0 风险, 纯只读):
 *   miniflux.health   {}  → { ok, alive, mfx_url, latency_ms, http_status, last_error, version }
 *   miniflux.feeds    {}  → { ok, alive, feeds: [{id, title, feed_url, category, ...}], last_error }
 *   miniflux.entries  {}  → { ok, alive, entries: [{id, title, url, status, feed_id, ...}], total, last_error }
 *
 * **绝不**触碰 /v1/entries/[id]/bookmark /v1/entries/[id]/unread /feeds CRUD 等 mutating 接口
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_MINIFLUX_URL    默认 http://127.0.0.1:8080
 *   PRISIR_MINIFLUX_TOKEN  X-Auth-Token 自定义鉴权 token
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/custom-auth-client');

// ── env 字段注入 + mode='custom' ─────────────────────────────
function mfxConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_MINIFLUX_URL || 'http://127.0.0.1:8080',
    mode: 'custom',                                      // Miniflux 用 X-Auth-Token 自定义头
    customHeader: 'X-Auth-Token',
    customToken: process.env.PRISIR_MINIFLUX_TOKEN || '',
    timeoutMs: 5000,
  });
}

// ── /v1/me 健康检查 ──────────────────────────────────
async function probeHealth() {
  const cfg = mfxConfig();
  const t0 = Date.now();
  const r = await httpGet({ config: cfg, path: '/v1/me' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      mfx_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /v1/me 200 + is_admin 字段 → alive
  if (r.parsed && r.parsed.id) {
    return {
      ok: true, alive: true,
      mfx_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg),
      username: String(r.parsed.username || ''),
      is_admin: Boolean(r.parsed.is_admin),
      last_error: '',
    };
  }
  return {
    ok: false, alive: false,
    mfx_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    last_error: r.error || 'unexpected response',
  };
}

// ── /v1/feeds 列出所有订阅源 ─────────────────────────────
async function fetchFeeds() {
  const cfg = mfxConfig();
  const r = await httpGet({ config: cfg, path: '/v1/feeds' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const arr = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    feeds: arr.map((f) => ({
      id: Number(f.id || 0),
      title: String(f.title || ''),
      site_url: String(f.site_url || ''),
      feed_url: String(f.feed_url || ''),
      category: f.category ? String(f.category.title || '') : '',
      disabled: Boolean(f.disabled),
      parsing_error_count: Number(f.parsing_error_count || 0),
    })),
    last_error: '',
  };
}

// ── /v1/entries?status=unread 列出未读条目(只读默认)─────────
async function fetchEntries(args = {}) {
  const cfg = mfxConfig();
  const status = String(args.status || 'unread');
  const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 100);
  let q = `status=${status}&direction=desc&limit=${limit}`;
  if (args.search) q += `&search=${encodeURIComponent(String(args.search))}`;
  const r = await httpGet({ config: cfg, path: `/v1/entries?${q}` });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const entries = Array.isArray(r.parsed?.entries) ? r.parsed.entries : [];
  return {
    ok: true, alive: true,
    entries: entries.map((e) => ({
      id: Number(e.id || 0),
      feed_id: Number(e.feed_id || 0),
      title: String(e.title || ''),
      url: String(e.url || ''),
      author: String(e.author || ''),
      status: String(e.status || ''),
      starred: Boolean(e.starred),
      published_at: String(e.published_at || ''),
      reading_time: Number(e.reading_time || 0),
    })),
    total: Number(r.parsed?.total || entries.length),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'miniflux-bridge-status',
  name: 'Miniflux RSS(只读,X-Auth-Token)',
  version: '0.1.0',
});

ext.registerCommand('miniflux.health', async () => probeHealth());
ext.registerCommand('miniflux.feeds', async () => fetchFeeds());
ext.registerCommand('miniflux.entries', async (args) => fetchEntries(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchFeeds, fetchEntries,
    mfxConfig,
  };
}