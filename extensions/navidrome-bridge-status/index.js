'use strict';

/**
 * navidrome-bridge-status v0.1.0 — Navidrome 当前播放(只读, Subsonic 协议)
 *
 * 数据来源: Navidrome 的 Subsonic 兼容 API(默认 127.0.0.1:4533)。
 * 鉴权: Subsonic v1.13.0+ 强制 salted token — `token = md5(password + salt)`,
 *       每次请求服务端派新 salt + 客户端用 password+盐新算 token。**这是 v2 对比
 * 研究借鉴点 5「Token≠密码」的优秀范例**: 截获一个 token 无法跨端点重放
 * (因为 salt 是新的, 而且服务端验证不存 hash), 远比「一次鉴权 + 长 token」安全。
 *
 * 命令(L0 风险, 纯只读):
 *   navidrome.health       {}      → { ok, alive, nd_url, latency_ms, status, last_error }
 *   navidrome.now-playing  {}      → { ok, alive, now_playing: [...entries], last_error }
 *   navidrome.license      {}      → { ok, alive, valid, email, licenseExpires, last_error }
 *
 * 实现: Node crypto.md5 + http.get + JSON 解析。Subsonic 响应包在
 * { 'subsonic-response': { status, ... } }, 与 LX 的 flat / YesPlayMusic 的 data 都不同。
 *
 * 设计取舍(沿用 LX 5 步法):
 *   - 失败语义优先: 不抛异常, 返 { ok: false, alive: false, last_error: ... }
 *   - 失败语义双层: HTTP 失败 vs Subsonic.status='failed' vs Subsonic.error.message
 *   - 不缓存(每次 invoke 现取, Navidrome 切歌立即生效)
 *   - Phase A 范围: **严格只读**, **绝不**触碰 jukeboxControl/set/clear/play/stop/skip
 *     / setGain / getDownload / stream 等任何写/下载/流接口
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_NAVIDROME_URL     默认 http://127.0.0.1:4533
 *   PRISIR_NAVIDROME_USER    默认空(空时 navidrome.health 返回 alive=false,提示需配置)
 *   PRISIR_NAVIDROME_PASS    默认空(token 计算需 password,空时返 ok=false + last_error)
 *   PRISIR_NAVIDROME_TOKEN   可选 — 用户也可直接预派生 token,跳过明文密码传入
 *   PRISIR_NAVIDROME_CLIENT  默认 'prisirai'(Subsonic c 参数)
 *   PRISIR_NAVIDROME_API_V   默认 '1.16.1'(Subsonic v 参数,服务端校验 major+minor)
 */

const http = require('http');
const crypto = require('crypto');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ── 配置(env 优先) ────────────────────────────────────────────
function ndBaseUrl() {
  return process.env.PRISIR_NAVIDROME_URL || 'http://127.0.0.1:4533';
}
function ndUser() {
  return process.env.PRISIR_NAVIDROME_USER || '';
}
function ndPass() {
  return process.env.PRISIR_NAVIDROME_PASS || '';
}
function ndPrecomputedToken() {
  return process.env.PRISIR_NAVIDROME_TOKEN || '';
}
function ndClient() {
  return process.env.PRISIR_NAVIDROME_CLIENT || 'prisirai';
}
function ndApiV() {
  return process.env.PRISIR_NAVIDROME_API_V || '1.16.1';
}

const ND_TIMEOUT_MS = 1500;

// ── Subsonic 鉴权:token = md5(password + salt) ─────────────────
// **借鉴原则 5「Token≠密码」**:每次请求服务端派新 salt,客户端用 password+新盐算新 token;
// 截获单次 token 不能跨端点重放。
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

// ── 通用 GET helper(JSON,带 Subsonic 鉴权) ────────────────────
// opts:
//   - preToken / preSalt: 已派好的 token + salt(若用户 PRISIR_NAVIDROME_TOKEN 直接提供)
//   - password:           用户的明文密码(本次切空才需要,且不传 e2e mock 测试)
//   - endpoint:           '/ping' '/getNowPlaying' '/getLicense' 等
function httpGetSubsonic({ password, preToken, preSalt, endpoint }) {
  return new Promise((resolve) => {
    let salt = preSalt;
    let token = preToken;
    if (!token) {
      if (!password) {
        return resolve({
          ok: false, status: 0, body: '', parsed: null, url: '',
          error: 'no credentials — set PRISIR_NAVIDROME_PASS or PRISIR_NAVIDROME_TOKEN',
        });
      }
      const t = makeToken(password);
      salt = t.salt;
      token = t.token;
    }
    const qs = [
      `u=${encodeURIComponent(ndUser())}`,
      `t=${encodeURIComponent(token)}`,
      `s=${encodeURIComponent(salt)}`,
      `v=${encodeURIComponent(ndApiV())}`,
      `c=${encodeURIComponent(ndClient())}`,
      'f=json',
    ].join('&');
    const url = `${ndBaseUrl()}/rest/${endpoint}?${qs}`;
    const req = http.get(url, { timeout: ND_TIMEOUT_MS }, (res) => {
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

// ── Subsonic 失败语义检测(HTTP OK 但 status='failed') ─────────
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
      ok: false, alive: true,  // sibling: server alive but logical error
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

// ── /ping 健康检查 ──────────────────────────────────────────────
// Subsonic ping 返回空 subsonic-response(只 status='ok'),用于纯探活
async function probeHealth() {
  const t0 = Date.now();
  // ping 不需要 auth(Navidrome 默认允许 unauthenticated /ping),
  // 但带 token 走更标准路径
  const r = await httpGetSubsonic({
    password: ndPass() || undefined,
    preToken: ndPrecomputedToken() || undefined,
    endpoint: 'ping',
  });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      nd_url: ndBaseUrl(), latency_ms: dt, http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) {
    return {
      ok: false, alive: p.alive,
      nd_url: ndBaseUrl(), latency_ms: dt, http_status: r.status,
      last_error: p.last_error,
    };
  }
  return {
    ok: true, alive: true,
    nd_url: ndBaseUrl(), latency_ms: dt, http_status: r.status,
    last_error: '',
  };
}

// ── /getNowPlaying 解析当前播放条目 ────────────────────────────
// Subsonic 返回 nowPlaying entry: { id, title, album, artist, genre, year,
//   track, minutes, seconds, bitRate, suffix, contentType, isDir, coverArt
//   , playerId, username, minutesAgo, ... }
async function fetchNowPlaying() {
  const r = await httpGetSubsonic({
    password: ndPass() || undefined,
    preToken: ndPrecomputedToken() || undefined,
    endpoint: 'getNowPlaying',
  });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: `getNowPlaying HTTP ${r.status}${r.error ? ': ' + r.error : ''}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) return { ok: false, alive: p.alive, last_error: p.last_error };
  const entries = Array.isArray(p.body.nowPlaying && p.body.nowPlaying.entry)
    ? p.body.nowPlaying.entry
    : (p.body.nowPlaying && p.body.nowPlaying.entry
        ? [p.body.nowPlaying.entry]
        : []);
  return {
    ok: true, alive: true,
    now_playing: entries.map((e) => ({
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
    })),
    last_error: '',
  };
}

// ── /getLicense 解析 license 信息(纯探活 + server 信息) ─────────
async function fetchLicense() {
  const r = await httpGetSubsonic({
    password: ndPass() || undefined,
    preToken: ndPrecomputedToken() || undefined,
    endpoint: 'getLicense',
  });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: `getLicense HTTP ${r.status}${r.error ? ': ' + r.error : ''}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) return { ok: false, alive: p.alive, last_error: p.last_error };
  const lic = p.body.license || {};
  return {
    ok: true, alive: true,
    valid: Boolean(lic.valid),
    email: String(lic.email || ''),
    licenseExpires: String(lic.licenseExpires || ''),
    serverVersion: String((p.body.serverVersion) || ''),
    last_error: '',
  };
}

// ── 注册 extension ──────────────────────────────────────────────
const ext = new PrisIrExt({
  id: 'navidrome-bridge-status',
  name: 'Navidrome 当前播放(只读, Subsonic 协议)',
  version: '0.1.0',
});

ext.registerCommand('navidrome.health', async () => probeHealth());
ext.registerCommand('navidrome.now-playing', async () => fetchNowPlaying());
ext.registerCommand('navidrome.license', async () => fetchLicense());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ────────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchNowPlaying, fetchLicense,
    ndBaseUrl, ndUser, ndPass, ndPrecomputedToken, ndClient, ndApiV,
    makeToken, randomSalt, md5Hex,
    httpGetSubsonic, parseSubsonic,
  };
}