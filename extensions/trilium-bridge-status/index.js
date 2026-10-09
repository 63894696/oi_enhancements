'use strict';

/**
 * trilium-bridge-status v0.1.0 — Trilium Notes 自托管层级笔记(只读, Bearer ETAPI)
 *
 * 数据来源: Trilium Notes ETAPI(自托管默认 http://127.0.0.1:8080/etapi)。
 *            Trilium 是 zadam/trilium 开发的自托管层级笔记应用(类似 Obsidian / Logseq),
 *            用户常把密码/API key/凭据存在里面。
 * 鉴权:    ETAPI 三种风格并存(v0.93+ 推荐 Bearer):
 *            `Authorization: Bearer <token>` (RFC 6750,我们走这条)
 *            `Authorization: <raw_token>` (老版本)
 *            `Authorization: Basic base64(user:pass)` (备用)
 *          token 在 UI → Options → ETAPI → Add Token 生成(长随机字符串,可命名/吊销)。
 *
 * **Phase C SDK 复用(2026-10-08)**:Trilium 是 bearer-client.js SDK **第十三个用户**
 * (前 12 用户跨 12 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签)。
 * **跨入笔记/层级知识域**(前 12 用户跨 12 类,加 Trilium 第 13 类)。**零 SDK 边界跨越**
 * (与 Linkwarden/Mealie/BookStack 同 pattern: `httpGet + describeAuth + makeConfig`)。
 *
 * **envelope 形状**:ETAPI 直接返对象/数组(无 `{results, count}` 信封),与 SiYuan 一致。
 *
 * 借鉴原则 5「Token≠密码」中档(6/10)— token 一旦签发可重放直到用户吊销
 *
 * 命令(L0 风险, 纯只读):
 *   trilium.health    {}  → GET /etapi/app-info 公开探活 + 取版本
 *   trilium.notes     {search?, limit?, debug?}  → GET /etapi/notes?search=&fastSearch=&limit= 列/搜索笔记
 *   trilium.note      {noteId}  → GET /etapi/notes/{noteId} 单 note 元数据
 *
 * **绝不**触碰:
 *   - POST /etapi/notes / PUT / DELETE
 *   - GET /etapi/notes/{id}/export (ZIP 含附件,高风险)
 *   - GET /etapi/import / /backup
 *   - POST /etapi/branches (创建分支)
 *
 * P0 安全约束(产品级):
 *   1. **只白名单 GET**(`/app-info`、`/notes`、`/notes/{id}`、`/attributes?noteId=`)
 *   2. **不抓 content 字段**(笔记可能含密码)— Phase A 只返 metadata(title/type/mime/dateModified)
 *   3. 用户须在 UI 自生成 token,不接受密码直传
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_TRILIUM_URL     默认 http://127.0.0.1:8080
 *   PRISIR_TRILIUM_API_KEY Bearer token(Options → ETAPI → Add Token)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function triliumConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_TRILIUM_URL || 'http://127.0.0.1:8080',
    token: process.env.PRISIR_TRILIUM_API_KEY || '',
    timeoutMs: 5000,
  });
}

// ── /etapi/app-info 公开探活(无需 Bearer,但 SDK 统一要求 token)──
async function probeHealth() {
  const cfg = triliumConfig();
  const t0 = Date.now();
  const r = await httpGet({ config: cfg, path: '/etapi/app-info' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      trilium_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  // /etapi/app-info 返 {appName, appVersion, dbVersion, syncVersion, ...}
  const data = r.parsed || {};
  return {
    ok: true, alive: true,
    trilium_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    app_name: String(data.appName || 'Trilium Notes'),
    app_version: String(data.appVersion || 'unknown'),
    db_version: String(data.dbVersion || 'unknown'),
    sync_version: Number(data.syncVersion) || 0,
    build_date: String(data.buildDate || ''),
    last_error: '',
  };
}

// ── /etapi/notes 列/搜索笔记(支持 search + fastSearch + limit)──
async function fetchNotes(args = {}) {
  const cfg = triliumConfig();
  const qs = new URLSearchParams();
  if (args.search) qs.set('search', String(args.search));
  if (args.fastSearch !== false) qs.set('fastSearch', 'true');  // 默认 fast
  if (args.limit) qs.set('limit', String(Math.max(1, Math.min(200, Number(args.limit) || 50))));
  if (args.archived === true) qs.set('archived', 'true');
  if (args.debug === true) qs.set('debug', 'true');
  const path = `/etapi/notes${qs.toString() ? '?' + qs.toString() : ''}`;
  const r = await httpGet({ config: cfg, path });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  // ETAPI 直接返 note 数组(无 envelope)
  const data = Array.isArray(r.parsed) ? r.parsed : [];
  return {
    ok: true, alive: true,
    notes: data.map((n) => ({
      note_id: String(n.noteId || ''),
      title: String(n.title || '(untitled)').slice(0, 300),
      type: String(n.type || 'text'),
      mime: String(n.mime || 'text/html'),
      date_created: String(n.dateCreated || ''),
      date_modified: String(n.dateModified || ''),
      parent_note_ids: Array.isArray(n.parentNoteIds) ? n.parentNoteIds.map(String) : [],
      is_archived: Boolean(n.isArchived),
      is_protected: Boolean(n.isProtected),           // 加密 note — Phase A 不可读
      has_children: Array.isArray(n.childNoteIds) ? n.childNoteIds.length > 0 : false,
    })),
    total: data.length,
    last_error: '',
  };
}

// ── /etapi/notes/{id} 单 note 元数据(不抓 content)─────
async function fetchNote(args = {}) {
  const cfg = triliumConfig();
  const noteId = String(args.noteId || '').trim();
  if (!noteId || noteId.length < 5) {
    return { ok: false, alive: false, http_status: 0, last_error: 'invalid noteId — must be a non-empty string' };
  }
  const r = await httpGet({ config: cfg, path: `/etapi/notes/${encodeURIComponent(noteId)}` });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const n = r.parsed || {};
  return {
    ok: true, alive: true,
    note: {
      note_id: String(n.noteId || noteId),
      title: String(n.title || '(untitled)').slice(0, 300),
      type: String(n.type || 'text'),
      mime: String(n.mime || 'text/html'),
      date_created: String(n.dateCreated || ''),
      date_modified: String(n.dateModified || ''),
      parent_note_ids: Array.isArray(n.parentNoteIds) ? n.parentNoteIds.map(String) : [],
      is_archived: Boolean(n.isArchived),
      is_protected: Boolean(n.isProtected),          // 加密 — 不读
      has_children: Array.isArray(n.childNoteIds) ? n.childNoteIds.length > 0 : false,
      child_count: Array.isArray(n.childNoteIds) ? n.childNoteIds.length : 0,
    },
    // Phase A 不抓 content(可能含密码/API key)
    content_included: false,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'trilium-bridge-status',
  name: 'Trilium Notes 自托管层级笔记(只读, Bearer ETAPI)',
  version: '0.1.0',
});

ext.registerCommand('trilium.health', async () => probeHealth());
ext.registerCommand('trilium.notes', async (args) => fetchNotes(args || {}));
ext.registerCommand('trilium.note', async (args) => fetchNote(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchNotes, fetchNote,
    triliumConfig,
  };
}