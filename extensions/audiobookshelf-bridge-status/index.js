'use strict';

/**
 * audiobookshelf-bridge-status v0.1.0 — Audiobookshelf 当前播放(只读, Bearer token)
 *
 * 数据来源: Audiobookshelf 自家 REST API(默认 127.0.0.1:8181)。
 * 鉴权: Bearer token(用户 API token,从主壳「Settings → Users → API Token」生成)。
 *       **不是** Subsonic md5+salt 协议(Audiobookshelf 官方主推自家 REST,Subsonic
 *       兼容是可选启用,所以本扩展走 REST 不走 _scaffold/subsonic-client)。
 *
 * 命令(L0 风险, 纯只读):
 *   audiobookshelf.health     {}      → { ok, alive, abs_url, latency_ms, http_status, last_error }
 *   audiobookshelf.libraries  {}      → { ok, alive, libraries: [{id, name, mediaType, ...}], last_error }
 *   audiobookshelf.sessions   {}      → { ok, alive, sessions: [{id, userId, mediaType, ...}], last_error }
 *
 * **绝不**触碰 /api/items/[id]/play 等任何流/下载/写控制接口
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_AUDIOBOOKSHELF_URL     默认 http://127.0.0.1:8181
 *   PRISIR_AUDIOBOOKSHELF_TOKEN   默认空(空时 audiobooks shelf.health 返 alive=false + 提示需配置)
 *
 * **借鉴 Audiobookshelf 协议设计**:「Bearer token」是「Token≠密码」原则的较弱实现
 * (token 一旦签发可重放,但 token 远端相比「明文 password」已大幅降低风险)— 借鉴原则 5
 * 提醒我们:有条件时应优先 salted token (Subsonic),Bearer 仅当 API 标准要求时用。
 */

const http = require('http');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ── env 字段注入 ────────────────────────────────
function absBaseUrl() {
  return process.env.PRISIR_AUDIOBOOKSHELF_URL || 'http://127.0.0.1:8181';
}
function absToken() {
  return process.env.PRISIR_AUDIOBOOKSHELF_TOKEN || '';
}
const ABS_TIMEOUT_MS = 1500;

// ── 通用 GET helper(JSON,带 Bearer token) ────────────────────
function absGet(path) {
  return new Promise((resolve) => {
    const token = absToken();
    if (!token) {
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url: '',
        error: 'no credentials — set PRISIR_AUDIOBOOKSHELF_TOKEN (Audiobookshelf Settings → Users → API Token)',
      });
    }
    const url = `${absBaseUrl()}${path}`;
    const headers = { Authorization: `Bearer ${token}` };
    const req = http.get(url, { timeout: ABS_TIMEOUT_MS, headers }, (res) => {
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

// ── /healthcheck 健康检查 ────────────────────────────────
async function probeHealth() {
  const t0 = Date.now();
  // healthcheck 不需要 Bearer token,但带 token 走更标准路径(走带 token 的路径便于快速发现 401)
  const r = await absGet('/healthcheck');
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      abs_url: absBaseUrl(), latency_ms: dt, http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    abs_url: absBaseUrl(), latency_ms: dt, http_status: r.status,
    last_error: '',
  };
}

// ── /api/libraries 解析 library 列表 ────────────────────
async function fetchLibraries() {
  const r = await absGet('/api/libraries');
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: `libraries HTTP ${r.status}${r.error ? ': ' + r.error : ''}`,
    };
  }
  if (!Array.isArray(r.parsed)) {
    return {
      ok: false, alive: true,
      last_error: `libraries response not an array: ${r.body.slice(0, 80)}`,
    };
  }
  return {
    ok: true, alive: true,
    libraries: r.parsed.map((l) => ({
      id: String(l.id || ''),
      name: String(l.name || ''),
      mediaType: String(l.mediaType || ''),  // 'book' | 'podcast'
      icon: String(l.icon || ''),
    })),
    last_error: '',
  };
}

// ── /api/sessions 解析当前活跃 session ────────────────────
// sessions 字段示例: {id, userId, mediaType, currentResourceId, position, ...}
async function fetchSessions() {
  const r = await absGet('/api/sessions');
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: `sessions HTTP ${r.status}${r.error ? ': ' + r.error : ''}`,
    };
  }
  if (!Array.isArray(r.parsed)) {
    return {
      ok: false, alive: true,
      last_error: `sessions response not an array: ${r.body.slice(0, 80)}`,
    };
  }
  return {
    ok: true, alive: true,
    sessions: r.parsed.map((s) => ({
      id: String(s.id || ''),
      userId: String(s.userId || ''),
      mediaType: String(s.mediaType || ''),
      currentResourceId: String(s.currentResourceId || ''),
      position: s.position != null ? Number(s.position) : null,
      duration: s.duration ? Number(s.duration) : null,
    })),
    last_error: '',
  };
}

// ── 注册 extension ────────────────────────────────
const ext = new PrisIrExt({
  id: 'audiobookshelf-bridge-status',
  name: 'Audiobookshelf 当前播放(只读, Bearer token)',
  version: '0.1.0',
});

ext.registerCommand('audiobookshelf.health', async () => probeHealth());
ext.registerCommand('audiobookshelf.libraries', async () => fetchLibraries());
ext.registerCommand('audiobookshelf.sessions', async () => fetchSessions());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchLibraries, fetchSessions,
    absBaseUrl, absToken, absGet,
  };
}