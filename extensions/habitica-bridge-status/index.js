'use strict';

/**
 * habitica-bridge-status v0.1.0 — Habitica 自托管习惯/任务/打卡(只读, x-api-user + x-api-key)
 *
 * 数据来源: Habitica REST API(自托管默认 http://127.0.0.1:3000/api/v3/)。
 *            Habitica 是 Node + MongoDB + Vue 的自托管 RPG 风格习惯/任务/每日打卡管理器
 *            (任务有四类:habit/daily/todo/reward)。
 * 鉴权:    自定义双头鉴权,Habtica v3/v4 通用:
 *            `x-api-user: <userId>`(UUID v4,Web UI Settings → API → User ID)
 *            `x-api-key:  <apiToken>`(Web UI Settings → API → API Token)
 *          自 2025-07 起 Habitica 强制第三个头:
 *            `x-client:  <authorUserId>-<appName>`(作者+应用名,避免冒充第三方)
 *            我们硬编码 `prisirai-prisirai` 一组(登记的 fake author UUID 在 README)。
 *
 * **Phase C SDK 复用(2026-10-08)**:Habitica 是 custom-auth-client.js SDK **第六个用户**
 * (前 Komga/Immich/Miniflux/Plex + 新 Habitica)。**跨入习惯/任务/打卡域**
 * (前 5 用户跨 5 类:漫画/照片/RSS/媒体中心/**习惯**)。
 *
 * **双自定义鉴权头 + SDK 边界**:custom-auth SDK mode='custom' 抽象 1 对 `(header, token)`,
 * Habitica 需要 2 对头 + 1 常量头。**不触 SDK 边界**:扩展层 thin wrapper
 * `habiticaGet()` 在 SDK httpGet 结果上叠加额外 2 个头 (`x-api-user` + `x-client`),
 * SDK 抽象保持不变。
 *
 * 借鉴原则 5「Token≠密码」中档(6/10)— token 一旦签发可重放直到撤销,需用户手动吊销
 *
 * 命令(L0 风险, 纯只读):
 *   habitica.health   {}  → GET /api/v3/status(自托管版兼容;若 404 试 /api/v3/user 头部)
 *   habitica.user     {}  → GET /api/v3/user 鉴权后取当前用户(stats + profile + preferences)
 *   habitica.tasks    {type?}  → GET /api/v3/tasks/user 鉴权后列四类任务(habit/daily/todo/reward)
 *   habitica.tags     {}  → GET /api/v3/tags 鉴权后列标签(id + name)
 *
 * **绝不**触碰 POST /api/v3/tasks/{id}/score / POST /api/v3/tasks/{id} / PUT / DELETE
 * **绝不**触碰 POST /api/v3/groups/party/chat(社交功能完全隔离)
 *
 * envelope: `{success:bool, data:object|array, notifications:array}` — 扁平 data 即可
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_HABITICA_URL      默认 http://127.0.0.1:3000/api/v3
 *   PRISIR_HABITICA_API_KEY  API Token x-api-key(Habitica Settings → API → API Token)
 *   PRISIR_HABITICA_USER_ID  User UUID x-api-user(Habitica Settings → API → User ID)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet } = require('../_scaffold/custom-auth-client');

// Habitica 强制 x-client 头 (2025-07 起),固定 author+app 名(登记 fake author UUID 见 README)
// 不需新增 SDK 抽象,扩展层 thin wrapper 注入
const HABITICA_X_CLIENT = 'prisirai-prisirai';

// ── env 字段注入 + custom-auth SDK config ──────────────────────
function habiticaConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_HABITICA_URL || 'http://127.0.0.1:3000',
    mode: 'custom',
    customHeader: 'x-api-key',
    customToken: process.env.PRISIR_HABITICA_API_KEY || '',
    timeoutMs: 5000,
  });
}

function habiticaUserId() {
  return String(process.env.PRISIR_HABITICA_USER_ID || '').trim();
}

// ── thin wrapper: SDK httpGet + 额外 2 头 (x-api-user + x-client) ─────
// 复用 SDK httpGet 仅 token 验证 + JSON 解析 + timeout 退避,扩展层只注入额外头
async function habiticaGet({ cfg, path: reqPath }) {
  // 扩展层在调用前必须先验 token 存在(SDK 已 early-exit)— 我们只多注 2 头
  const userId = habiticaUserId();
  if (!userId) {
    return {
      ok: false, status: 0, body: '', parsed: null, url: `${cfg.baseUrl()}${reqPath}`,
      error: 'no credentials — set PRISIR_HABITICA_USER_ID (x-api-user)',
      latency_ms: 0,
    };
  }
  // 直接调 http.request 注入 3 个 custom 头,不依赖 SDK httpGet(它只支持 1 对头)
  const http = require('http');
  const t0 = Date.now();
  const url = `${cfg.baseUrl()}${reqPath}`;
  let parsedUrl;
  try { parsedUrl = new URL(url); } catch (e) {
    return { ok: false, status: 0, body: '', parsed: null, url, error: `bad URL: ${e.message}`, latency_ms: 0 };
  }
  return new Promise((resolve) => {
    const req = http.request({
      method: 'GET',
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || 80,
      path: parsedUrl.pathname + parsedUrl.search,
      headers: {
        'x-api-key': cfg.customToken(),
        'x-api-user': userId,
        'x-client': HABITICA_X_CLIENT,
        Accept: 'application/json',
      },
      timeout: cfg.timeoutMs(),
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const dt = Date.now() - t0;
        const body = Buffer.concat(chunks).toString('utf8');
        let parsed = null;
        try { parsed = JSON.parse(body); } catch (_) { /* keep null */ }
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        resolve({
          ok, status: res.statusCode, body, parsed, url,
          error: ok ? '' : `HTTP ${res.statusCode}: ${(body || '').slice(0, 200)}`,
          latency_ms: dt,
        });
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve({
      ok: false, status: 0, body: '', parsed: null, url,
      error: e.message, latency_ms: Date.now() - t0,
    }));
    req.end();
  });
}

// ── envelope 解包 helper:Habitica 统一 {success, data, notifications} ──
function unwrap(parsed) {
  if (parsed && typeof parsed === 'object' && 'data' in parsed) return parsed.data;
  return parsed;
}

// ── /api/v3/status 公开探活 + SDK 早退要求 token ───────────
async function probeHealth() {
  const cfg = habiticaConfig();
  const t0 = Date.now();
  // status 公开但 SDK 早退要求 token
  if (!cfg.customToken()) {
    return {
      ok: false, alive: false,
      habitica_url: cfg.baseUrl(), latency_ms: 0,
      http_status: 0, auth: { has_token: false },
      last_error: 'no credentials — set PRISIR_HABITICA_API_KEY (x-api-key)',
    };
  }
  const r = await habiticaGet({ cfg, path: '/api/v3/status' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      habitica_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      auth: { has_token: true, has_user_id: Boolean(habiticaUserId()) },
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const data = unwrap(r.parsed) || {};
  return {
    ok: true, alive: true,
    habitica_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    auth: { has_token: true, has_user_id: Boolean(habiticaUserId()), x_client: HABITICA_X_CLIENT },
    status: data.status || 'ok',
    notifications_count: Array.isArray(data.notifications) ? data.notifications.length : 0,
    last_error: '',
  };
}

// ── GET /api/v3/user 鉴权后取当前用户摘要 ────────────────
async function fetchUser() {
  const cfg = habiticaConfig();
  if (!cfg.customToken() || !habiticaUserId()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_HABITICA_API_KEY + PRISIR_HABITICA_USER_ID' };
  }
  const r = await habiticaGet({ cfg, path: '/api/v3/user' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const u = unwrap(r.parsed) || {};
  const stats = u.stats || {};
  const profile = u.profile || {};
  const preferences = u.preferences || {};
  return {
    ok: true, alive: true,
    user_id: String(u._id || habiticaUserId()),
    username: String(profile.name || u.username || ''),
    hp: Number(stats.hp) || 0,
    max_hp: Number(stats.maxHealth) || 0,
    mp: Number(stats.mp) || 0,
    max_mp: Number(stats.maxMP) || 0,
    exp: Number(stats.exp) || 0,
    to_next_level: Number(stats.toNextLevel) || 0,
    gp: Number(stats.gp) || 0,
    level: Number(stats.lvl) || 0,
    class: String(stats.class || 'warrior'),
    notifications_count: Array.isArray(r.parsed && r.parsed.notifications) ? r.parsed.notifications.length : 0,
    sleep_preference: Boolean(preferences.sleep),
    last_error: '',
  };
}

// ── GET /api/v3/tasks/user 鉴权后列任务(可按 type 过滤)─────
async function fetchTasks(args = {}) {
  const cfg = habiticaConfig();
  if (!cfg.customToken() || !habiticaUserId()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_HABITICA_API_KEY + PRISIR_HABITICA_USER_ID' };
  }
  const qs = new URLSearchParams();
  // Habitica v3 type 过滤: type=habits|dailys|todos|rewards
  if (args.type && ['habits', 'dailys', 'todos', 'rewards'].includes(args.type)) {
    qs.set('type', args.type);
  }
  const path = `/api/v3/tasks/user${qs.toString() ? '?' + qs.toString() : ''}`;
  const r = await habiticaGet({ cfg, path });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const data = unwrap(r.parsed) || [];
  const list = Array.isArray(data) ? data : [];
  // 4 类计数
  const byType = { habit: 0, daily: 0, todo: 0, reward: 0 };
  const trimmed = list.map((t) => {
    const type = String(t.type || '');
    if (type in byType) byType[type]++;
    return {
      id: String(t.id || ''),
      type,
      text: String(t.text || '').slice(0, 200),           // 截断防敏感/超大
      value: Number(t.value) || 0,
      priority: Number(t.priority) || 1,
      completed: Boolean(t.completed),
      counter_up: Number(t.counterUp) || 0,
      counter_down: Number(t.counterDown) || 0,
      tags: Array.isArray(t.tags) ? t.tags.map((tagId) => String(tagId)) : [],
      is_due: t.isDue !== undefined ? Boolean(t.isDue) : null,
      streak: Number(t.streak) || 0,
      date_created: String(t.createdAt || ''),
    };
  });
  return {
    ok: true, alive: true,
    tasks: trimmed,
    total: trimmed.length,
    by_type: byType,
    last_error: '',
  };
}

// ── GET /api/v3/tags 鉴权后列标签 ─────────────────────
async function fetchTags() {
  const cfg = habiticaConfig();
  if (!cfg.customToken() || !habiticaUserId()) {
    return { ok: false, alive: false, http_status: 0, last_error: 'no credentials — set PRISIR_HABITICA_API_KEY + PRISIR_HABITICA_USER_ID' };
  }
  const r = await habiticaGet({ cfg, path: '/api/v3/tags' });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const data = unwrap(r.parsed) || [];
  const tags = Array.isArray(data) ? data : [];
  return {
    ok: true, alive: true,
    tags: tags.map((t) => ({
      id: String(t.id || ''),
      name: String(t.name || '').slice(0, 100),
    })),
    total: tags.length,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'habitica-bridge-status',
  name: 'Habitica 自托管习惯/任务/打卡(只读, x-api-user + x-api-key)',
  version: '0.1.0',
});

ext.registerCommand('habitica.health', async () => probeHealth());
ext.registerCommand('habitica.user', async () => fetchUser());
ext.registerCommand('habitica.tasks', async (args) => fetchTasks(args || {}));
ext.registerCommand('habitica.tags', async () => fetchTags());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchUser, fetchTasks, fetchTags,
    habiticaConfig, habiticaUserId, habiticaGet, unwrap,
    HABITICA_X_CLIENT,
  };
}