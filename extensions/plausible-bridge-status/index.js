'use strict';

/**
 * plausible-bridge-status v0.1.0 — Plausible Analytics 自托管(只读, Bearer API key)
 *
 * 数据来源: Plausible 自家 Stats API v2 + Sites API v1(默认 127.0.0.1:8000/api)。
 *            Plausible 是隐私友好的 Google Analytics 替代品(自托管,无 cookie 无追踪)。
 * 鉴权:    `Authorization: Bearer <api-key>` 标准 Bearer(RFC 6750),
 *          token 在 Plausible User → API Keys 生成(可选 Stats API / Sites API 类型)。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Phase C SDK 复用(2026-10-07)**:Plausible 是 bearer-client.js SDK **第五个用户**
 * (前 Audiobookshelf/Kavita/Gitea/Drone CI)— 累计 5 用户 SDK 抽取持续价值。
 *
 * **SDK 边界决策**:Plausible Stats API v2 用 POST JSON body(非 GET),
 * SDK 当前只 httpGet。**Plausible 扩展 inline httpPostJson helper** 30 行,
 * sites/health 等 GET 端点仍走 SDK(httpGet)。这是有意的 SDK 边界:
 *  - httpGet: 通用,SDK 复用
 *  - httpPostJson: 罕见(POST + body),inline 避免 SDK 膨胀
 *
 * 命令(L0 风险, 纯只读):
 *   plausible.health       {}  → { ok, alive, plausible_url, latency_ms, http_status, last_error }
 *   plausible.sites        {}  → { ok, alive, sites: [{domain, timezone}] }
 *   plausible.summary      {site_id, metrics?, date_range?}  → POST /api/v2/query
 *
 * **绝不**触碰 POST /api/v1/sites 创建站点 / DELETE /api/v1/sites/{domain} 任何 mutating
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_PLAUSIBLE_URL     默认 http://127.0.0.1:8000
 *   PRISIR_PLAUSIBLE_API_KEY Bearer API key(Plausible User → API Keys)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');
const http = require('http');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function plConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_PLAUSIBLE_URL || 'http://127.0.0.1:8000',
    token: process.env.PRISIR_PLAUSIBLE_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── inline httpPostJson helper(POST + JSON body 罕见,SDK 不膨胀)──
function httpPostJson(args) {
  const cfg = args && args.config;
  const path = String((args && args.path) || '/');
  const body = args && args.body ? JSON.stringify(args.body) : '{}';
  return new Promise((resolve) => {
    const token = cfg ? cfg.token_() : '';
    if (!token) {
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url: '',
        error: `no credentials — set Bearer token`,
      });
    }
    const url = `${cfg.baseUrl_()}${path}`;
    const headers = {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
    };
    const req = http.request(url, { method: 'POST', timeout: cfg.timeoutMs_(), headers }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
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
    req.write(body);
    req.end();
  });
}

// ── /api/v1/sites 探活(GET,SDK 复用 + 不消耗 stats quota)────
async function probeHealth() {
  const cfg = plConfig();
  const t0 = Date.now();
  // /api/v1/sites 是 GET,鉴权 + 列出全部站点(探活 + 数据双用途)
  const r = await httpGet({ config: cfg, path: '/api/v1/sites' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      plausible_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    plausible_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    last_error: '',
  };
}

// ── /api/v1/sites 列出全部站点(GET,SDK 复用)────────────
async function fetchSites() {
  const cfg = plConfig();
  const r = await httpGet({ config: cfg, path: '/api/v1/sites' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v1/sites 返 array of {domain, timezone}
  const list = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    sites: list.map((s) => ({
      domain: String(s.domain || ''),
      timezone: String(s.timezone || 'UTC'),
    })),
    last_error: '',
  };
}

// ── POST /api/v2/query 聚合 stats ───────────────────────
async function fetchSummary(args = {}) {
  const cfg = plConfig();
  if (!args.site_id) {
    return {
      ok: false, alive: false,
      last_error: 'missing required arg: site_id (Plausible registered domain)',
    };
  }
  const metrics = Array.isArray(args.metrics) && args.metrics.length > 0
    ? args.metrics
    : ['visitors', 'pageviews', 'bounce_rate', 'visit_duration'];
  const date_range = String(args.date_range || '7d');
  const r = await httpPostJson({
    config: cfg,
    path: '/api/v2/query',
    body: {
      site_id: args.site_id,
      metrics,
      date_range,
    },
  });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/v2/query 返 { results: [number | {dimensions: [...], metrics: [number]}], query: {...} }
  const results = Array.isArray(r.parsed && r.parsed.results) ? r.parsed.results : [];
  const out = { ok: true, alive: true, site_id: args.site_id, metrics, date_range, summary: {}, last_error: '' };
  // 聚合型(无 dimensions)返 number 数组 → 映射回 metrics
  if (results.every((v) => typeof v === 'number')) {
    metrics.forEach((m, i) => { out.summary[m] = results[i]; });
  } else {
    // 带 dimensions → 原样保留前 N 项
    out.results = results.slice(0, 20);
  }
  return out;
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'plausible-bridge-status',
  name: 'Plausible Analytics 自托管(只读, Bearer API key)',
  version: '0.1.0',
});

ext.registerCommand('plausible.health', async () => probeHealth());
ext.registerCommand('plausible.sites', async () => fetchSites());
ext.registerCommand('plausible.summary', async (args) => fetchSummary(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchSites, fetchSummary,
    plConfig, httpPostJson,
  };
}
