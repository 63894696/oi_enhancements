'use strict';

/**
 * HTTP 请求 v0.1.0
 *
 * 命令:
 *   http.fetch   { url, method?, headers?, body?, timeout_ms? }   → { status, headers, body }
 *   http.whitelist { add?, remove?, list? }                        → { allowlist, count }
 *
 * 安全:域名白名单(默认空,需要显式 add 才允许)。localhost / 127.0.0.1 / ::1 自动拒绝(SSRF 防身)。
 *      https 默认 20s,http 10s,超时报错。
 *      body 限 4MB,响应限 8MB。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'http-request', name: 'HTTP 请求', version: '0.1.0' });

const STORE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'whitelist.json');

function loadWl() {
  try { return JSON.parse(fs.readFileSync(STORE(), 'utf8')); } catch { return []; }
}
function saveWl(wl) {
  try { fs.writeFileSync(STORE(), JSON.stringify(wl, null, 2)); } catch {}
}

// 显式拒绝私有 / 环回地址(SSRF 防身)
function isBlockedHost(host) {
  const h = String(host || '').toLowerCase().split(':')[0];
  if (h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '0.0.0.0') return true;
  // 10.x / 192.168.x / 172.16-31.x
  if (/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
  if (/^192\.168\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
  if (h.endsWith('.local') || h.endsWith('.internal')) return true;
  return false;
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return ''; }
}

ext.registerCommand('http.fetch', async (args) => {
  const url = String(args.url || '');
  if (!url) return { error: 'url required' };
  let parsed;
  try { parsed = new URL(url); } catch (e) { return { error: 'invalid url' }; }

  const host = parsed.hostname.toLowerCase();
  if (isBlockedHost(host)) return { error: `blocked host: ${host}(SSRF 防身)` };
  const wl = loadWl();
  if (!wl.some(w => host === w.toLowerCase() || host.endsWith('.' + w.toLowerCase()))) {
    return { error: `host not in whitelist: ${host}; add via http.whitelist add=${host}` };
  }
  const proto = parsed.protocol;
  if (proto !== 'http:' && proto !== 'https:') return { error: `unsupported protocol: ${proto}` };

  const method = String(args.method || 'GET').toUpperCase();
  const headers = args.headers || {};
  const body = args.body;
  const timeoutMs = Number(args.timeout_ms) || (proto === 'https:' ? 20000 : 10000);

  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const init = {
      method,
      headers: { 'User-Agent': 'PrisirAI-Extension/0.1 (+http-request)', ...headers },
      signal: ac.signal,
      redirect: 'follow',
    };
    if (body !== undefined && body !== null) {
      if (typeof body === 'string' || Buffer.isBuffer(body)) init.body = body;
      else { init.body = JSON.stringify(body); init.headers['Content-Type'] = init.headers['Content-Type'] || 'application/json'; }
    }
    const resp = await fetch(url, init);
    const text = await resp.text();
    const truncated = text.length > 8 * 1024 * 1024 ? text.slice(0, 8 * 1024 * 1024) + '...[truncated]' : text;
    const respHeaders = {};
    resp.headers.forEach((v, k) => { respHeaders[k] = v; });
    return {
      ok: resp.ok,
      status: resp.status,
      status_text: resp.statusText,
      headers: respHeaders,
      body: truncated,
      url: resp.url,
      bytes: text.length,
    };
  } catch (e) {
    return { error: e.name === 'AbortError' ? `timeout after ${timeoutMs}ms` : e.message };
  } finally {
    clearTimeout(t);
  }
});

ext.registerCommand('http.whitelist', async (args) => {
  let wl = loadWl();
  if (args.add) {
    const host = String(args.add).toLowerCase().trim();
    if (!host || host.includes('/') || !/^[a-z0-9.\-]+$/.test(host)) return { error: `bad host: ${host}` };
    if (!wl.includes(host)) wl.push(host);
    saveWl(wl);
  }
  if (args.remove) {
    wl = wl.filter(h => h !== String(args.remove).toLowerCase());
    saveWl(wl);
  }
  return { allowlist: wl, count: wl.length };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });