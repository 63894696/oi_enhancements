'use strict';

/**
 * bearer-client.js — Pure Bearer SDK (RFC 6750: Authorization: Bearer <token>)
 *
 * 抽自 Audiobookshelf + Kavita,2026-10-07 加 Gitea 后达 3 用户,正式抽取 SDK。
 *
 * 与 custom-auth-client.js 区别:
 *   - custom-auth SDK: 自定义 header 名(X-API-Key / X-Auth-Token / X-Plex-Token / x-api-key 等)
 *   - bearer SDK:      标准 Bearer scheme(RFC 6750),header 名固定为 Authorization
 *
 * 提供 API:
 *   - bearerHeader(token)            → { Authorization: 'Bearer <token>' } 或 {}
 *   - makeConfig({baseUrl, token, timeoutMs?}) → 不可变 config 对象
 *   - httpGet({config, path})        → Promise<{ok, status, body, parsed, url, error}>
 *   - describeAuth(config)           → { mode: 'bearer', has_token: bool, base_url: string }
 *
 * 三层失败语义:
 *   - 缺 token    → { ok:false, status:0, error:'no credentials — ...' }
 *   - 网络错      → { ok:false, status:0, error:'ECONNREFUSED|...' }
 *   - HTTP 4xx/5xx → { ok:false, status:<code>, parsed:<body>, error:'HTTP <code>' }
 *
 * 零 npm 依赖,纯 Node 24+ 内置 http。
 */

const http = require('http');
const { URL } = require('url');

// ── API 1:构造 Bearer header ────────────────────────────
function bearerHeader(token) {
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

// ── API 2:makeConfig(不可变 config,失败语义用)────────────
function makeConfig(opts) {
  const baseUrl = String((opts && opts.baseUrl) || 'http://127.0.0.1').replace(/\/+$/, '');
  const token = String((opts && opts.token) || '');
  const timeoutMs = Number((opts && opts.timeoutMs) || 5000);
  const cfg = Object.freeze({
    baseUrl,
    token,
    timeoutMs,
    baseUrl_() { return baseUrl; },
    token_() { return token; },
    timeoutMs_() { return timeoutMs; },
  });
  return cfg;
}

// ── API 3:httpGet(同 custom-auth SDK 失败语义)────────────
function httpGet(args) {
  const cfg = args && args.config;
  const path = String((args && args.path) || '/');
  return new Promise((resolve) => {
    if (!cfg || typeof cfg.baseUrl_ !== 'function') {
      return resolve({ ok: false, status: 0, body: '', parsed: null, url: '', error: 'no config' });
    }
    const token = cfg.token_();
    if (!token) {
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url: '',
        error: `no credentials — set Bearer token`,
      });
    }
    const url = `${cfg.baseUrl_()}${path}`;
    let parsed_url;
    try { parsed_url = new URL(url); } catch (e) {
      return resolve({ ok: false, status: 0, body: '', parsed: null, url, error: `bad URL: ${e.message}` });
    }
    const headers = bearerHeader(token);
    const req = http.get(url, { timeout: cfg.timeoutMs_(), headers }, (res) => {
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
          ...(res.statusCode >= 400 ? { error: `HTTP ${res.statusCode}` } : {}),
        });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', (e) => resolve({
      ok: false, status: 0, body: '', parsed: null, url, error: e.message,
    }));
  });
}

// ── API 4:describeAuth(供 L0 命令输出 auth 描述)─────────
function describeAuth(cfg) {
  if (!cfg || typeof cfg.baseUrl_ !== 'function') return { mode: 'bearer', has_token: false, base_url: '' };
  return {
    mode: 'bearer',
    has_token: Boolean(cfg.token_()),
    base_url: cfg.baseUrl_(),
  };
}

module.exports = {
  bearerHeader,
  makeConfig,
  httpGet,
  describeAuth,
};
