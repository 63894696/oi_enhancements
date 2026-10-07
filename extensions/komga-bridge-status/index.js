'use strict';

/**
 * komga-bridge-status v0.1.0 — Komga 漫画库(只读,X-API-Key + Basic Auth)
 *
 * 数据来源: Komga REST API(默认 127.0.0.1:25600/api/v1)。
 *            Komga 是漫画/BD/Manga 自托管管理器,REST API URL-based versioning。
 * 鉴权:    两种模式自动选择:
 *   1) `X-API-Key: <key>` 自 Komga 1.12.0+ — 用户在 Web UI 创建 API Key
 *   2) `Authorization: Basic base64(user:pass)` — 经典 HTTP Basic
 *
 * **Phase C SDK 抽取(2026-10-07)**:Komga 是 _scaffold/custom-auth-client.js SDK
 * **首个用户**。SDK 设计原则:
 *     - 透传鉴权模式(扩展决定 apiKey / basic / custom header)
 *     - 零 npm dep(只 Node 内置 http)
 *     - 3 层失败语义(HTTP / JSON.parse 失败 / 401/403 vs 200/404)
 *     - makeConfig factory 让扩展独立命名 env 字段
 *
 * 命令(L0 风险, 纯只读):
 *   komga.health      {}  → { ok, alive, komga_url, latency_ms, http_status, last_error }
 *   komga.libraries   {}  → { ok, alive, libraries: [{id, name, root, scan_interval}], last_error }
 *   komga.series      {}  → { ok, alive, series: [{id, name, library_id, books_count}], last_error }
 *
 * **绝不**触碰 PATCH / POST / PUT / DELETE 等 mutating 接口
 * **绝不**触碰书签 / 阅读进度(读 progress,改 progress)
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_KOMGA_URL         默认 http://127.0.0.1:25600
 *   PRISIR_KOMGA_AUTH_MODE   默认 'auto' — 'apiKey' / 'basic' / 'auto'
 *                            auto 优先 API Key(若 PRISIR_KOMGA_API_KEY 设置),回退 basic
 *   PRISIR_KOMGA_API_KEY     X-API-Key 模式 — 优先生效
 *   PRISIR_KOMGA_USER        Basic Auth 模式 — 用户名
 *   PRISIR_KOMGA_PASS        Basic Auth 模式 — 密码
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/custom-auth-client');

// ── env 字段注入 + auth_mode 解析 ──────────────────────────
function kmConfig() {
  const baseUrl = process.env.PRISIR_KOMGA_URL || 'http://127.0.0.1:25600';
  const apiKey = process.env.PRISIR_KOMGA_API_KEY || '';
  const user = process.env.PRISIR_KOMGA_USER || '';
  const pass = process.env.PRISIR_KOMGA_PASS || '';
  const explicit = (process.env.PRISIR_KOMGA_AUTH_MODE || '').toLowerCase();
  let mode;
  if (explicit === 'apikey' || explicit === 'api_key') mode = 'apiKey';
  else if (explicit === 'basic') mode = 'basic';
  else if (apiKey) mode = 'apiKey';         // auto:有 Key 就用 Key
  else if (user) mode = 'basic';             // auto:无 Key 有 user 回退 basic
  else mode = 'apiKey';                       // 默认 — 没 Key 也走 apiKey(让 SDK 返 no credentials)

  return makeConfig({
    baseUrl,
    mode,
    key: apiKey,
    user,
    pass,
    timeoutMs: 5000,
  });
}

// ── /actuator/health 健康检查 ───────────────────────
async function probeHealth() {
  const cfg = kmConfig();
  const t0 = Date.now();
  // Komga 1.9.0+ 标准健康端点(无鉴权也可访问,但带鉴权不会失败)
  const r = await httpGet({ config: cfg, path: '/actuator/health' });
  const dt = Date.now() - t0;
  if (!r.ok && r.status === 0) {
    return {
      ok: false, alive: false,
      komga_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || 'unreachable',
    };
  }
  // HTTP 200 + { status: UP } → alive
  // HTTP 401/403 → 不算 alive,但 last_error 提示鉴权问题
  if (r.ok && r.parsed && r.parsed.status === 'UP') {
    return {
      ok: true, alive: true,
      komga_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), version: r.parsed.groups || '',
      last_error: '',
    };
  }
  return {
    ok: false, alive: false,
    komga_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    last_error: r.parsed?.status ? `status=${r.parsed.status}` : (r.error || `HTTP ${r.status}`),
  };
}

// ── /api/v1/libraries 列出所有漫画库 ──────────────
async function fetchLibraries() {
  const cfg = kmConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/libraries' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.parsed?.message || r.error || `HTTP ${r.status}`,
    };
  }
  // LibraryDto array
  const arr = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    libraries: arr.map((lib) => ({
      id: String(lib.id || ''),
      name: String(lib.name || ''),
      root: String(lib.root || ''),
      scan_interval: String(lib.scanInterval || 'DISABLED'),
      unavailable: Boolean(lib.unavailable),
    })),
    last_error: '',
  };
}

// ── /api/v1/series?page=0&size=20 列出最新 20 个系列 ──
async function fetchSeries(args = {}) {
  const cfg = kmConfig();
  const size = Math.min(Math.max(Number(args.size) || 20, 1), 100);
  const page = Math.max(Number(args.page) || 0, 0);
  const search = String(args.search || '');
  let q = `page=${page}&size=${size}`;
  if (search) q += `&search=${encodeURIComponent(search)}`;
  const r = await httpGet({ config: cfg, path: `/api/v1/series?${q}` });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: r.parsed?.message || r.error || `HTTP ${r.status}`,
    };
  }
  // PageSeriesDto: { content: SeriesDto[], totalElements, totalPages, ... }
  const content = Array.isArray(r.parsed?.content) ? r.parsed.content : [];
  return {
    ok: true, alive: true,
    series: content.map((s) => ({
      id: String(s.id || ''),
      name: String(s.name || ''),
      library_id: String(s.libraryId || ''),
      books_count: Number(s.booksCount || 0),
      status: String(s.status || ''),
    })),
    total_elements: Number(r.parsed?.totalElements || 0),
    page: Number(r.parsed?.number || page),
    size: Number(r.parsed?.size || size),
    last_error: '',
  };
}

// ── 注册 extension ────────────────────────────
const ext = new PrisIrExt({
  id: 'komga-bridge-status',
  name: 'Komga 漫画库(只读,X-API-Key + Basic Auth)',
  version: '0.1.0',
});

ext.registerCommand('komga.health', async () => probeHealth());
ext.registerCommand('komga.libraries', async () => fetchLibraries());
ext.registerCommand('komga.series', async (args) => fetchSeries(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchLibraries, fetchSeries,
    kmConfig,
  };
}