/**
 * _scaffold/custom-auth-client.js — 自定义鉴权头 HTTP 客户端 SDK
 *
 * 适用于:
 *   - HTTP Basic Auth(Authorization: Basic base64(user:pass))
 *   - 自定义 API Key 头(X-API-Key: xxx)
 *   - X-Auth-Token 类(类似 Miniflux)
 *   - 任何「单 HTTP GET + 自定义鉴权头」场景
 *
 * 设计原则(2026-10-07,v2 借鉴):
 *   - 零 npm dep,只 Node 内置 http + crypto(API Key 不需要 crypto,留口备用)
 *   - 透传鉴权头:扩展自己决定 Basic vs X-API-Key vs X-Auth-Token
 *   - 3 层失败语义:HTTP / JSON.parse 失败 / HTTP 401/403 vs 200/404
 *   - 配置 factory(makeConfig)避免扩展自己写多个 env getter
 *
 * 鉴权模式(借鉴原则 5「Token≠密码」):
 *   - API Key 头:中(8/10)— token 一旦签发可重放直到撤销
 *   - HTTP Basic Auth over HTTPS:弱(5/10)— base64 编码明文,HTTPS 才安全
 *   - HTTP Basic Auth over HTTP:极弱(1/10)— **绝不**使用,本地局域网也不推荐
 *
 * 历史 ship:
 *   2026-10-07:首个使用此 SDK 的扩展 = komga-bridge-status
 */

'use strict';

const http = require('http');

/**
 * 生成鉴权头(基础 API Key header 用)
 * @param {string} key — 用户提供的 API Key / Token
 * @returns {{[k: string]: string}}
 */
function apiKeyHeader(key) {
  if (!key) return {};
  return { 'X-API-Key': key };
}

/**
 * 生成 Basic Auth 头
 * @param {string} user
 * @param {string} pass
 * @returns {{[k: string]: string}}
 */
function basicAuthHeader(user, pass) {
  if (!user) return {};
  const encoded = Buffer.from(`${user}:${pass || ''}`, 'utf8').toString('base64');
  return { Authorization: `Basic ${encoded}` };
}

/**
 * 配置 factory — 让扩展自己注入 env 字段,SDK 不绑死字段名
 * @param {{baseUrl: string, mode: 'apiKey'|'basic'|'custom', key?: string,
 *          user?: string, pass?: string, customHeader?: string, customToken?: string,
 *          timeoutMs?: number}} opts
 */
function makeConfig(opts) {
  const cfg = {
    baseUrl: opts.baseUrl,
    mode: opts.mode || 'apiKey',
    key: opts.key || '',
    user: opts.user || '',
    pass: opts.pass || '',
    customHeader: opts.customHeader || '',
    customToken: opts.customToken || '',
    timeoutMs: opts.timeoutMs || 5000,
  };
  return {
    baseUrl: () => cfg.baseUrl,
    mode: () => cfg.mode,
    key: () => cfg.key,
    user: () => cfg.user,
    pass: () => cfg.pass,
    customHeader: () => cfg.customHeader,
    customToken: () => cfg.customToken,
    timeoutMs: () => cfg.timeoutMs,
  };
}

/**
 * GET helper — 根据 config.mode 自动注入鉴权头
 * @param {{config: ReturnType<typeof makeConfig>, path: string}} args
 * @returns {Promise<{ok: boolean, status: number, body: string, parsed: any,
 *                  url: string, error: string, latency_ms: number}>}
 */
function httpGet(args) {
  return new Promise((resolve) => {
    const { config, path: reqPath } = args;
    const t0 = Date.now();
    const baseUrl = config.baseUrl();
    const url = `${baseUrl}${reqPath}`;

    // 构造鉴权头
    let authHeaders = {};
    const mode = config.mode();
    if (mode === 'apiKey') {
      authHeaders = apiKeyHeader(config.key());
    } else if (mode === 'basic') {
      authHeaders = basicAuthHeader(config.user(), config.pass());
    } else if (mode === 'custom') {
      const h = config.customHeader();
      const t = config.customToken();
      if (h && t) authHeaders = { [h]: t };
    }

    if (Object.keys(authHeaders).length === 0) {
      const dt = Date.now() - t0;
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url,
        error: 'no credentials — set API Key / user+pass / custom token',
        latency_ms: dt,
      });
    }

    const headers = { ...authHeaders, Accept: 'application/json' };
    let parsedUrl;
    try { parsedUrl = new URL(url); } catch (e) {
      const dt = Date.now() - t0;
      return resolve({
        ok: false, status: 0, body: '', parsed: null, url,
        error: `bad URL: ${e.message}`, latency_ms: dt,
      });
    }

    const req = http.get({
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || 80,
      path: parsedUrl.pathname + parsedUrl.search,
      headers,
      timeout: config.timeoutMs(),
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const dt = Date.now() - t0;
        const body = Buffer.concat(chunks).toString('utf8');
        let parsed = null;
        try { parsed = JSON.parse(body); } catch (_) { /* keep null */ }
        // 401/403/404 等错误不在 HTTP 层算 ok=false — 留给扩展自己判定
        const isHttpErr = res.statusCode >= 400;
        resolve({
          ok: !isHttpErr, status: res.statusCode, body, parsed, url,
          error: isHttpErr ? `HTTP ${res.statusCode}` : '',
          latency_ms: dt,
        });
      });
    });

    req.on('timeout', () => {
      req.destroy(new Error('timeout'));
    });
    req.on('error', (e) => {
      const dt = Date.now() - t0;
      resolve({
        ok: false, status: 0, body: '', parsed: null, url,
        error: e.message || String(e), latency_ms: dt,
      });
    });
  });
}

/**
 * 取鉴权头用于日志或展示(给用户调试)
 * @param {ReturnType<typeof makeConfig>} config
 */
function describeAuth(config) {
  const mode = config.mode();
  if (mode === 'apiKey') return config.key() ? `X-API-Key (len=${config.key().length})` : 'X-API-Key (empty)';
  if (mode === 'basic') return `Basic ${config.user() ? '(set)' : '(empty user)'}`;
  if (mode === 'custom') return `${config.customHeader() || '(empty)'} ${config.customToken() ? '(set)' : '(empty)'}`;
  return `(unknown mode: ${mode})`;
}

module.exports = {
  apiKeyHeader,
  basicAuthHeader,
  makeConfig,
  httpGet,
  describeAuth,
};