'use strict';

/**
 * subsonic-client.js — Phase C 统一 Subsonic 协议 SDK 子集
 *
 * 协议:Subsonic v1.13.0+
 *   - 鉴权:token = md5(password + salt)(每次请求客户端派新 salt)
 *   - URL:{base}/rest/{endpoint}?u=user&t=token&s=salt&v=1.16.1&c=client&f=json
 *   - 响应:{'subsonic-response': {status: 'ok'/'failed', ...data, error: {code, message}}}
 *
 * 适用软件:
 * - Navidrome     : 默认 127.0.0.1:4533
 * - Audiobookshelf: 默认 127.0.0.1:8181(可选开启 Subsonic 兼容,推荐走 Bearer SDK 不走本 SDK)
 * - LMS           : 默认 127.0.0.1:9000
 * - Funkwhale     : 默认 127.0.0.1:5000
 * - Ampache       : 默认 127.0.0.1:80
 *
 * **使用方式**(Navidrome 已 ship,Audiobookshelf 走 Bearer SDK):
 *   const { httpGetSubsonic, parseSubsonic, makeToken, md5Hex, randomSalt } = require('../_scaffold/subsonic-client');
 *
 * 设计取舍:
 *   - **零依赖**,只 require Node built-in crypto + http
 *   - **失败语义三层`:HTTP / envelope 缺失 / subsonic-response.status=failed + error.code
 *   - **可注入 transport**(以便测试用 mock server 或写 E2E 替代)
 *   - **env 覆盖**:复用扩展自己的 env 字段,不强制 PRISIR_SUBSONIC_* 命名
 *
 * **不做什么**:
 *   - 不写任何 mutating endpoint(jukeboxControl / set / create / update / delete)
 *   - 不读密码(password 仅用于派生 token,内存一次性使用)
 *   - 不缓存(每次 invoke 现取)
 */

const http = require('http');
const crypto = require('crypto');

// ── 鉴权原语 ──
function randomSalt(n = 12) {
  return crypto.randomBytes(n).toString('hex');
}
function md5Hex(s) {
  return crypto.createHash('md5').update(s).digest('hex');
}
function makeToken(password) {
  const salt = randomSalt();
  const token = md5Hex(password + salt);
  return { salt, token };
}

// ── 配置(provider 函数形式,让扩展自己注入 env 字段) ──
function makeConfig({ baseUrl, user, password, preToken, client, apiV }) {
  return {
    baseUrl: () => baseUrl,
    user: () => user,
    password: () => password,
    preToken: () => preToken,
    client: () => client || 'prisirai',
    apiV: () => apiV || '1.16.1',
  };
}

// ── 通用 GET helper(带 Subsonic 鉴权) ──
/**
 * opts:
 *   - config      : makeConfig() 返回
 *   - password    : 用户的明文密码(优先级低于 config.preToken)
 *   - preToken    : 预派生 token(用户已算好的,跳过明文密码)
 *   - preSalt     : 预派生 salt(配 preToken 用)
 *   - endpoint    : 'ping' / 'getNowPlaying' / 'getLicense' 等
 *   - timeoutMs   : 默认 1500
 */
function httpGetSubsonic({ config, password, preToken, preSalt, endpoint, timeoutMs = 1500 }) {
  return new Promise((resolve) => {
    let salt = preSalt;
    let token = preToken;
    if (!token) {
      const pw = password || config.password();
      if (!pw) {
        return resolve({
          ok: false, status: 0, body: '', parsed: null, url: '',
          error: 'no credentials — set password or preToken',
        });
      }
      const t = makeToken(pw);
      salt = t.salt;
      token = t.token;
    }
    const qs = [
      `u=${encodeURIComponent(config.user())}`,
      `t=${encodeURIComponent(token)}`,
      `s=${encodeURIComponent(salt)}`,
      `v=${encodeURIComponent(config.apiV())}`,
      `c=${encodeURIComponent(config.client())}`,
      'f=json',
    ].join('&');
    const url = `${config.baseUrl()}/rest/${endpoint}?${qs}`;
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        const ct = String(res.headers['content-type'] || '');
        let parsed = null;
        if (ct.includes('application/json') || buf.trim().startsWith('{')) {
          try { parsed = JSON.parse(buf); } catch {}
        }
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode,
          body: buf,
          parsed,
          url,
          endpoint,
        });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', (e) => resolve({
      ok: false, status: 0, body: '', parsed: null, url, endpoint, error: e.message,
    }));
  });
}

// ── Subsonic 失败语义检测(HTTP OK 但 status='failed') ──
function parseSubsonic(r) {
  if (!r.parsed) {
    return {
      ok: false, alive: false,
      last_error: `Subsonic ${r.endpoint} HTTP ${r.status} non-JSON: ${r.body.slice(0, 80)}`,
    };
  }
  const root = r.parsed['subsonic-response'];
  if (!root) {
    return {
      ok: false, alive: false,
      last_error: `Subsonic ${r.endpoint} missing root envelope`,
    };
  }
  if (root.status === 'failed') {
    const err = root.error || {};
    return {
      ok: false, alive: true,
      last_error: `Subsonic ${r.endpoint} status=failed code=${err.code || '?'} msg=${err.message || '?'}`,
    };
  }
  if (root.status !== 'ok') {
    return {
      ok: false, alive: true,
      last_error: `Subsonic ${r.endpoint} unknown status: ${root.status || '(empty)'}`,
    };
  }
  return { ok: true, alive: true, body: root, last_error: '' };
}

// ── Subsonic 通用「现在在听」字段映射 ──
// Subsonic 的 nowPlaying entry: { id, title, album, artist, genre, year, track,
//   minutes, seconds, bitRate, suffix, contentType, isDir, coverArt,
//   playerId, username, minutesAgo, ... }
function mapNowPlayingEntry(e) {
  return {
    username: String(e.username || ''),
    title: String(e.title || ''),
    artist: String(e.artist || ''),
    album: String(e.album || ''),
    genre: String(e.genre || ''),
    year: e.year ? Number(e.year) : null,
    minutesAgo: e.minutesAgo != null ? Number(e.minutesAgo) : null,
    playerId: Number(e.playerId || 0),
    id: String(e.id || ''),
    contentType: String(e.contentType || ''),
    bitRate: e.bitRate ? Number(e.bitRate) : null,
  };
}
function flattenNowPlaying(body) {
  const np = body.nowPlaying;
  if (!np) return [];
  if (Array.isArray(np.entry)) return np.entry;
  if (np.entry) return [np.entry];
  return [];
}

module.exports = {
  randomSalt,
  md5Hex,
  makeToken,
  makeConfig,
  httpGetSubsonic,
  parseSubsonic,
  mapNowPlayingEntry,
  flattenNowPlaying,
};