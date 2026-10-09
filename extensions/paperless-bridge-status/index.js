'use strict';

/**
 * paperless-bridge-status v0.1.0 — Paperless-ngx 自托管文档管理(只读, Token auth)
 *
 * 数据来源: Paperless-ngx REST API(自托管默认 http://127.0.0.1:8000/api/)。
 *            Paperless-ngx 是 Django REST Framework + Tesseract OCR + Daphne ASGI 的自托管文档管理
 *            (类似 Devonthink / Mendeley / Google Drive + OCR)。
 * 鉴权:    `Authorization: Token <api_token>`(Django REST Framework TokenAuthentication),
 *          token 在 Paperless-ngx Web UI → My Profile → API Token 页面生成。
 *
 * **Phase C SDK 复用(2026-10-08)**:Paperless-ngx 是 custom-auth-client.js SDK **第七个用户**
 * (前 Komga/Immich/Miniflux/Plex/Habitica + 新 Paperless)。**跨入文档管理域**(全新域)。
 *
 * **DRF envelope**:`{count: int, next: url|null, previous: url|null, all: int[], results: array}`。
 * 分页通过 `?page=N` 循环拉,跳过 `all` 数组(全量 ID 列表,避免 OOM 大库)。
 * `?page_size=25` 控制每页。
 *
 * 借鉴原则 5「Token≠密码」中档(6/10)— token 一旦签发可重放直到用户撤销
 *
 * 命令(L0 风险, 纯只读):
 *   paperless.health      {}  → GET /api/ 探活
 *   paperless.documents   {page?, page_size?, query?}  → GET /api/documents/ 列文档(DRF 分页)
 *   paperless.tags        {page?, page_size?}  → GET /api/tags/ 标签列表(DRF 分页)
 *   paperless.thumb       {documentId}  → GET /api/documents/{id}/thumb/ 取 PNG 缩略图(base64)
 *
 * **绝不**触碰 POST /api/documents/ / PUT / PATCH / DELETE 任何 mutating 接口
 * **绝不**触碰 POST /api/documents/post_document/(消费邮件/上传入口)
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_PAPERLESS_URL      默认 http://127.0.0.1:8000
 *   PRISIR_PAPERLESS_API_KEY  Token 后面的字符串(My Profile → API Token)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet } = require('../_scaffold/custom-auth-client');
const http = require('http');

// ── env 字段注入 + custom-auth SDK config ──────────────────────
function paperlessConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_PAPERLESS_URL || 'http://127.0.0.1:8000',
    mode: 'custom',
    customHeader: 'Authorization',
    // DRF TokenAuthentication 头值是 "Token <raw_key>"(自带前缀)
    customToken: process.env.PRISIR_PAPERLESS_API_KEY ? `Token ${process.env.PRISIR_PAPERLESS_API_KEY}` : '',
    timeoutMs: 8000,            // DRF + OCR 后端稍慢,延 timeout
  });
}

// ── DRF envelope 解析(只读 count + next + results, 跳 all)──
function parseDrfEnvelope(parsed) {
  if (parsed && typeof parsed === 'object' && 'results' in parsed) {
    return {
      count: Number(parsed.count) || 0,
      next: parsed.next || null,
      previous: parsed.previous || null,
      results: Array.isArray(parsed.results) ? parsed.results : [],
    };
  }
  return { count: 0, next: null, previous: null, results: [] };
}

// ── /api/ 探活(DRF 根 + 鉴权后返 schema-style list)─────────
async function probeHealth() {
  const cfg = paperlessConfig();
  const t0 = Date.now();
  if (!cfg.customToken()) {
    return {
      ok: false, alive: false,
      paperless_url: cfg.baseUrl(), latency_ms: 0,
      http_status: 0, auth: { has_token: false },
      last_error: 'no credentials — set PRISIR_PAPERLESS_API_KEY',
    };
  }
  // /api/ 端点鉴权后返所有 endpoint schema 列表(DRF 通用)
  const r = await httpGet({ config: cfg, path: '/api/' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      paperless_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: { has_token: true },
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    paperless_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: { has_token: true },
    endpoints_count: typeof r.parsed === 'object' ? Object.keys(r.parsed).length : 0,
    last_error: '',
  };
}

// ── /api/documents/ 列文档(DRF 分页 + 跳过 all)─────────────
async function fetchDocuments(args = {}) {
  const cfg = paperlessConfig();
  if (!cfg.customToken()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_PAPERLESS_API_KEY' };
  }
  const pageSize = Math.max(1, Math.min(100, Number(args.page_size) || 25));
  const maxPages = Math.max(1, Math.min(20, Number(args.max_pages) || 5));  // 防止大库 OOM
  const qs = new URLSearchParams();
  qs.set('page_size', String(pageSize));
  if (args.query) qs.set('query', String(args.query));
  if (args.tags__id) qs.set('tags__id__all', String(args.tags__id));
  if (args.correspondent__id) qs.set('correspondent__id__all', String(args.correspondent__id));

  // DRF 分页循环:跟随 next URL 直到 null 或 max_pages
  const docs = [];
  let totalCount = 0;
  let pages = 0;
  let lastPath = `/api/documents/?${qs.toString()}`;
  let lastStatus = 0;
  while (lastPath && pages < maxPages) {
    const r = await httpGet({ config: cfg, path: lastPath });
    lastStatus = r.status;
    if (!r.ok) {
      return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
    }
    const env = parseDrfEnvelope(r.parsed);
    if (pages === 0) totalCount = env.count;
    // 提取 paperless document 字段
    for (const d of env.results) {
      docs.push({
        id: Number(d.id) || 0,
        title: String(d.title || '(untitled)').slice(0, 300),
        document_type: d.document_type ? Number(d.document_type) : null,
        correspondent: d.correspondent ? Number(d.correspondent) : null,
        tags: Array.isArray(d.tags) ? d.tags.map((t) => Number(t) || 0) : [],
        created: String(d.created || ''),
        modified: String(d.modified || ''),
        added: String(d.added || ''),
        archive_serial_number: Number(d.archive_serial_number) || null,
        original_filename: String(d.original_filename || '').slice(0, 200),
        mime_type: String(d.mime_type || ''),
        page_count: Number(d.page_count) || 0,
      });
    }
    pages++;
    // next 是绝对 URL(从 DRF),需要抽出 pathname + search
    lastPath = env.next ? pathToRelativePath(env.next, cfg.baseUrl()) : null;
  }
  return {
    ok: true, alive: true,
    documents: docs,
    total_in_query: totalCount,
    pages_fetched: pages,
    max_pages_hit: pages >= maxPages && lastPath !== null,
    last_http_status: lastStatus,
    last_error: '',
  };
}

// ── /api/tags/ 标签列表(DRF 分页)───────────────────────
async function fetchTags(args = {}) {
  const cfg = paperlessConfig();
  if (!cfg.customToken()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_PAPERLESS_API_KEY' };
  }
  const pageSize = Math.max(1, Math.min(200, Number(args.page_size) || 100));
  const r = await httpGet({ config: cfg, path: `/api/tags/?page_size=${pageSize}` });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const env = parseDrfEnvelope(r.parsed);
  return {
    ok: true, alive: true,
    tags: env.results.map((t) => ({
      id: Number(t.id) || 0,
      name: String(t.name || '').slice(0, 100),
      slug: String(t.slug || ''),
      color: String(t.color || ''),
      match: String(t.match || ''),
      is_inbox_tag: Boolean(t.is_inbox_tag),
      document_count: Number(t.document_count) || 0,
    })),
    total: env.count,
    last_error: '',
  };
}

// ── /api/documents/{id}/thumb/ 取缩略图 PNG base64 ─────────
// SDK httpGet 走 utf8 字符串解码,binary PNG 失真 → inline http.request 保留 Buffer
async function fetchThumb(args = {}) {
  const cfg = paperlessConfig();
  if (!cfg.customToken()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_PAPERLESS_API_KEY' };
  }
  const docId = Number(args.documentId);
  if (!docId || docId <= 0) {
    return { ok: false, alive: false, http_status: 0, last_error: 'invalid documentId — must be a positive integer' };
  }
  // 走原始 token(剥 "Token " 前缀)重新拼,SDK cfg 内是 "Token xxx"
  const rawToken = String(cfg.customToken()).replace(/^Token\s+/i, '');
  const t0 = Date.now();
  const url = `${cfg.baseUrl()}/api/documents/${docId}/thumb/`;
  let parsedUrl;
  try { parsedUrl = new URL(url); } catch (e) {
    return { ok: false, alive: false, http_status: 0, last_error: `bad URL: ${e.message}` };
  }
  return new Promise((resolve) => {
    const req = http.request({
      method: 'GET',
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || 80,
      path: parsedUrl.pathname,
      headers: {
        Authorization: `Token ${rawToken}`,
        Accept: 'image/png',
      },
      timeout: cfg.timeoutMs(),
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return resolve({
            ok: false, alive: false, http_status: res.statusCode,
            last_error: `HTTP ${res.statusCode}: ${buf.toString('utf8', 0, 200)}`,
          });
        }
        resolve({
          ok: true, alive: true,
          document_id: docId,
          png_base64: buf.toString('base64'),         // raw Buffer → base64
          size_bytes: buf.length,
          latency_ms: Date.now() - t0,
          last_error: '',
        });
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve({
      ok: false, alive: false, http_status: 0, last_error: e.message,
    }));
    req.end();
  });
}

// ── DRF next URL → 相对 path 抽取 ──────────────────────
function pathToRelativePath(absUrl, baseUrl) {
  try {
    const a = new URL(absUrl);
    const b = new URL(baseUrl);
    if (a.hostname === b.hostname && a.port === b.port) {
      return a.pathname + a.search;
    }
    // host 不匹配(反代场景)— 把 baseUrl 的 origin 替换为 next 的 origin
    return a.pathname + a.search;
  } catch (_) {
    return null;
  }
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'paperless-bridge-status',
  name: 'Paperless-ngx 自托管文档管理(只读, Token auth)',
  version: '0.1.0',
});

ext.registerCommand('paperless.health', async () => probeHealth());
ext.registerCommand('paperless.documents', async (args) => fetchDocuments(args || {}));
ext.registerCommand('paperless.tags', async (args) => fetchTags(args || {}));
ext.registerCommand('paperless.thumb', async (args) => fetchThumb(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchDocuments, fetchTags, fetchThumb,
    paperlessConfig, parseDrfEnvelope, pathToRelativePath,
  };
}