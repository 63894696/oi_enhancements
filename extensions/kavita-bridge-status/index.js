'use strict';

/**
 * kavita-bridge-status v0.1.0 — Kavita 漫画/书库(只读, Bearer API key)
 *
 * 数据来源: Kavita 自家 REST API(默认 127.0.0.1:5000/api)。
 *            Kavita 是自托管漫画/电子书/PDF 库(类似 Komga 但功能更广)。
 * 鉴权:    `Authorization: Bearer <api-key>` 标准 Bearer(token 实际是 JWT,
 *          由 Kavita 用户在「User Settings → API Keys」生成)。
 *          借鉴原则 5「Token≠密码」中档(6/10)— token 可重放但本项目 100% 本地
 *
 * **Bearer 模式复用**:与 Audiobookshelf 同模式(`Authorization: Bearer ${token}`),
 * 但**未抽 SDK**(Audiobookshelf 单案例不够 SDK 化阈值,inline 重复一次更轻)。
 *
 * 命令(L0 风险, 纯只读):
 *   kavita.health      {}  → { ok, alive, kavita_url, latency_ms, http_status, version, last_error }
 *   kavita.libraries   {}  → { ok, alive, libraries: [{id, name, type}], last_error }
 *   kavita.series      {search?, limit?} → { ok, alive, series: [{id, name, libraryId, pageCount}], total, last_error }
 *
 * **绝不**触碰 /api/Reader/progress /mark-read /mark-unread /series DELETE /Library scan
 * 等 mutating 接口。
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_KAVITA_URL       默认 http://127.0.0.1:5000
 *   PRISIR_KAVITA_API_KEY   Bearer token(JWT),在 Kavita User Settings → API Keys 生成
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const http = require('http');

// ── env 字段注入 + Bearer helper ──────────────────────────
function kvBaseUrl() {
  return process.env.PRISIR_KAVITA_URL || 'http://127.0.0.1:5000';
}
function kvToken() {
  return process.env.PRISIR_KAVITA_API_KEY || '';
}
const KV_TIMEOUT_MS = 5000;

function kvGet(path) {
  return new Promise((resolve) => {
    const token = kvToken();
    if (!token) {
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url: '',
        error: 'no credentials — set PRISIR_KAVITA_API_KEY (Kavita User Settings → API Keys)',
      });
    }
    const url = `${kvBaseUrl()}${path}`;
    const headers = { Authorization: `Bearer ${token}` };
    const req = http.get(url, { timeout: KV_TIMEOUT_MS, headers }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        const ct = String(res.headers['content-type'] || '');
        let parsed = null;
        if (ct.includes('application/json') || buf.trim().startsWith('{') || buf.trim().startsWith('[')) {
          try { parsed = JSON.parse(buf); } catch {}
        }
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode,
          body: buf,
          parsed,
          url,
        });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', (e) => resolve({
      ok: false, status: 0, body: '', parsed: null, url, error: e.message,
    }));
  });
}

// ── /api/Server/ping 健康检查(无需鉴权,用于探活)────────────
async function probeHealth() {
  const t0 = Date.now();
  // Kavita /api/Server/ping 返 { value: "pong" } 无需 Bearer
  const r = await kvGet('/api/Server/ping');
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      kavita_url: kvBaseUrl(), latency_ms: dt, http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/Server/ping 返 { value: 'pong', apiVersion: '0.0.x', ... }
  const version = r.parsed && r.parsed.apiVersion ? String(r.parsed.apiVersion) : '';
  return {
    ok: true, alive: true,
    kavita_url: kvBaseUrl(), latency_ms: dt, http_status: r.status,
    version,
    last_error: '',
  };
}

// ── /api/Library/libraries 列出所有 library ──────────────────
async function fetchLibraries() {
  const r = await kvGet('/api/Library/libraries');
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/Library/libraries 返 array of {id, name, type, ...}
  const list = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    libraries: list.map((l) => ({
      id: Number(l.id || 0),
      name: String(l.name || ''),
      type: String(l.type || ''),         // 'Manga' | 'Comic' | 'Book' | ...
      coverImage: String(l.coverImage || ''),
    })),
    last_error: '',
  };
}

// ── /api/Series/v2 列出/搜索 series(分页 + search) ─────────
async function fetchSeries(args = {}) {
  const cfg = kvBaseUrl();
  const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 100);
  const search = String(args.search || '');
  // /api/Series/v2 用 POST,但 Phase A 用 GET /api/Series/all-types 列出全系列
  // 简化:走 GET /api/Series?PageNumber=1&PageSize=limit (REST 风格)
  let path = `/api/Series?PageNumber=1&PageSize=${limit}`;
  if (search) path += `&SearchTerm=${encodeURIComponent(search)}`;
  const r = await kvGet(path);
  if (!r.ok) {
    return {
      ok: false, alive: false,
      http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /api/Series 返 { result: [...], totalPages, totalCount, pageNumber }
  const top = r.parsed || {};
  const list = Array.isArray(top.result) ? top.result : (Array.isArray(top) ? top : []);
  return {
    ok: true, alive: true,
    series: list.map((s) => ({
      id: Number(s.id || 0),
      name: String(s.name || s.localizedName || ''),
      library_id: Number(s.libraryId || 0),
      page_count: Number(s.pages || 0),
      formatted_name: String(s.formattedName || ''),
    })),
    total: Number(top.totalCount || list.length),
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'kavita-bridge-status',
  name: 'Kavita 漫画/书库(只读, Bearer API key)',
  version: '0.1.0',
});

ext.registerCommand('kavita.health', async () => probeHealth());
ext.registerCommand('kavita.libraries', async () => fetchLibraries());
ext.registerCommand('kavita.series', async (args) => fetchSeries(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchLibraries, fetchSeries,
    kvBaseUrl, kvToken, kvGet,
  };
}
