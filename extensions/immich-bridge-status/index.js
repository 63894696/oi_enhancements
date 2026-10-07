'use strict';

/**
 * immich-bridge-status v0.1.0 — Immich 照片库(只读,x-api-key)
 *
 * 数据来源: Immich REST API(默认 127.0.0.1:2283/api)。
 *            Immich 是自托管照片/视频管理(Google Photos 替代品),REST API + URL-based path。
 * 鉴权:    `x-api-key: <key>` 自定义头(不是 Authorization Bearer)
 *          借鉴原则 5「Token≠密码」中档(7/10)— token 可重放但本项目 100% 本地
 *          Immich 提供细粒度权限(album.read / asset.read / ...),用户创建 API Key 时分配
 *
 * **Phase C SDK 复用(2026-10-07)**:Immich 是 custom-auth-client.js SDK
 * **mode='apiKey' 第二个用户**(前一个:Komga 漫画)——验证 SDK 接受"任意 API Key header"
 * 真跨域(漫画版 × 照片版)。
 *
 * 命令(L0 风险, 纯只读):
 *   immich.health      {}  → { ok, alive, immich_url, latency_ms, http_status, version, last_error }
 *   immich.albums     {}  → { ok, alive, albums: [{id, name, assetCount, createdAt}], last_error }
 *   immich.assets     {}  → { ok, alive, assets: [{id, type, originalFileName, duration, ...}], total, last_error }
 *
 * **绝不**触碰 POST /api/assets(上传)/ POST /api/albums(创建)/ DELETE 等 mutating 接口
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_IMMICH_URL      默认 http://127.0.0.1:2283
 *   PRISIR_IMMICH_API_KEY  x-api-key 自定义鉴权 token
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/custom-auth-client');

// ── env 字段注入 + mode='apiKey' ─────────────────────────────
function imxConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_IMMICH_URL || 'http://127.0.0.1:2283',
    mode: 'apiKey',                                       // Immich 用 x-api-key 自定义头(API Key 模式)
    key: process.env.PRISIR_IMMICH_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── /api/server/ping 健康检查(无鉴权也可访问)─────────
async function probeHealth() {
  const cfg = imxConfig();
  const t0 = Date.now();
  // Immich /ping 是 public,但 /server/version 需要鉴权 — 验 version 才是真活
  const r = await httpGet({ config: cfg, path: '/api/server/version' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      immich_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/server/version 返 {version: 'v1.x.y'} → alive
  if (r.parsed && r.parsed.version) {
    return {
      ok: true, alive: true,
      immich_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg),
      version: String(r.parsed.version || ''),
      last_error: '',
    };
  }
  return {
    ok: false, alive: false,
    immich_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg), last_error: r.error || 'unexpected response',
  };
}

// ── /api/albums 列出所有相册 ────────────────────────
async function fetchAlbums() {
  const cfg = imxConfig();
  const r = await httpGet({ config: cfg, path: '/api/albums' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // Immich /albums 返 AlbumResponse { albums: Album[] } 包装(从内部结构提取)
  const albums = (() => {
    const top = r.parsed;
    if (Array.isArray(top?.albums?.albums)) return top.albums.albums;
    if (Array.isArray(top?.albums)) return top.albums;
    return Array.isArray(top) ? top : [];
  })();
  return {
    ok: true, alive: true,
    albums: albums.map((a) => ({
      id: String(a.id || ''),
      name: String(a.albumName || a.name || ''),
      asset_count: Number(a.assetCount || 0),
      created_at: String(a.createdAt || ''),
      is_shared: Boolean(a.shared),
    })),
    last_error: '',
  };
}

// ── /api/assets?take=20 列出最近资产 ────────────────
async function fetchAssets(args = {}) {
  const cfg = imxConfig();
  const take = Math.min(Math.max(Number(args.take) || 20, 1), 100);
  const r = await httpGet({ config: cfg, path: `/api/assets?take=${take}` });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // Immich /assets 返 {assets: AssetResponse[], total} 或直接 array
  const top = r.parsed;
  const assets = Array.isArray(top?.assets) ? top.assets : (Array.isArray(top) ? top : []);
  return {
    ok: true, alive: true,
    assets: assets.map((a) => ({
      id: String(a.id || ''),
      type: String(a.type || ''),
      original_file_name: String(a.originalFileName || ''),
      duration: String(a.duration || ''),
      is_favorite: Boolean(a.isFavorite),
      file_created_at: String(a.fileCreatedAt || ''),
    })),
    total: Number(top?.total || assets.length),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'immich-bridge-status',
  name: 'Immich 照片库(只读,x-api-key)',
  version: '0.1.0',
});

ext.registerCommand('immich.health', async () => probeHealth());
ext.registerCommand('immich.albums', async () => fetchAlbums());
ext.registerCommand('immich.assets', async (args) => fetchAssets(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchAlbums, fetchAssets,
    imxConfig,
  };
}