'use strict';

/**
 * plex-bridge-status v0.1.0 — Plex 媒体服务器(只读,X-Plex-Token)
 *
 * 数据来源: Plex Media Server REST API(默认 127.0.0.1:32400)。
 *            Plex 是自托管影视媒体服务器(电影/剧集/音乐/照片),REST API + URL-based path。
 * 鉴权:    `X-Plex-Token: <token>` 自定义头(不是 Authorization Bearer)
 *          借鉴原则 5「Token≠密码」中档(7/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Plex 是 custom-auth-client.js SDK
 * **第三个用户**(前两个:Komga mode='apiKey'/'basic',Miniflux mode='custom')
 * Plex 用 mode='custom' + X-Plex-Token,**与 Miniflux 几乎一致**,扩展家族扩到 "影视"。
 *
 * 命令(L0 风险, 纯只读):
 *   plex.health      {}  → { ok, alive, plex_url, latency_ms, http_status, version, last_error }
 *   plex.libraries   {}  → { ok, alive, libraries: [{key, title, type, agent}], last_error }
 *   plex.recent      {}  → { ok, alive, items: [{title, type, year, library, rating}], last_error }
 *
 * **绝不**触碰 /:/playback/* /status/sessions (会话)/library/sections/refresh /scrobble 等 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_PLEX_URL    默认 http://127.0.0.1:32400
 *   PRISIR_PLEX_TOKEN  X-Plex-Token 自定义鉴权 token
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/custom-auth-client');

// ── env 字段注入 + mode='custom' ─────────────────────────────
function pxConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_PLEX_URL || 'http://127.0.0.1:32400',
    mode: 'custom',                                       // Plex 用 X-Plex-Token 自定义头
    customHeader: 'X-Plex-Token',
    customToken: process.env.PRISIR_PLEX_TOKEN || '',
    timeoutMs: 5000,
  });
}

// ── /identity 健康检查(返 Plex 服务器元信息)─────────────
async function probeHealth() {
  const cfg = pxConfig();
  const t0 = Date.now();
  const r = await httpGet({ config: cfg, path: '/identity' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      plex_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /identity 返 MediaContainer with size>0 → alive
  const mc = r.parsed?.MediaContainer || r.parsed;
  if (mc && Number(mc.size || 0) > 0) {
    return {
      ok: true, alive: true,
      plex_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg),
      version: String(r.parsed?.MediaContainer?.version || mc.version || ''),
      machine_identifier: String(r.parsed?.MediaContainer?.machineIdentifier || mc.machineIdentifier || ''),
      last_error: '',
    };
  }
  return {
    ok: false, alive: false,
    plex_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg), last_error: r.error || 'unexpected response',
  };
}

// ── /library/sections 列出所有媒体库 ──────────────────
async function fetchLibraries() {
  const cfg = pxConfig();
  const r = await httpGet({ config: cfg, path: '/library/sections' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // MediaContainer.Directory[] (每个 library)
  const directories = Array.isArray(r.parsed?.MediaContainer?.Directory) ? r.parsed.MediaContainer.Directory : [];
  return {
    ok: true, alive: true,
    libraries: directories.map((d) => ({
      key: String(d.key || ''),
      title: String(d.title || ''),
      type: String(d.type || ''),
      agent: String(d.agent || ''),
      scanner: String(d.scanner || ''),
      language: String(d.language || ''),
    })),
    last_error: '',
  };
}

// ── /library/recentlyAdded 最近添加媒体 ────────────────
async function fetchRecent(args = {}) {
  const cfg = pxConfig();
  const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 100);
  const r = await httpGet({ config: cfg, path: `/library/recentlyAdded?X-Plex-Container-Size=${limit}` });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // MediaContainer.Metadata[]
  const metadata = Array.isArray(r.parsed?.MediaContainer?.Metadata) ? r.parsed.MediaContainer.Metadata : [];
  return {
    ok: true, alive: true,
    items: metadata.slice(0, limit).map((m) => ({
      title: String(m.title || ''),
      type: String(m.type || ''),
      year: Number(m.year || 0),
      library: String(m.librarySectionTitle || ''),
      rating: Number(m.rating || 0),
      added_at: String(m.addedAt || ''),
    })),
    total: metadata.length,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'plex-bridge-status',
  name: 'Plex 媒体服务器(只读,X-Plex-Token)',
  version: '0.1.0',
});

ext.registerCommand('plex.health', async () => probeHealth());
ext.registerCommand('plex.libraries', async () => fetchLibraries());
ext.registerCommand('plex.recent', async (args) => fetchRecent(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchLibraries, fetchRecent,
    pxConfig,
  };
}