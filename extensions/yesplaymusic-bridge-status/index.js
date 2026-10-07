'use strict';

/**
 * yesplaymusic-bridge-status v0.1.0 — YesPlayMusic 当前播放(只读)
 *
 * 数据来源:YesPlayMusic 的 Open API(默认 127.0.0.1:27232)。
 * **必须用户在「Settings → Experimental Features → Enable local API」手动启用**(YesPlayMusic
 * 默认 Open API 是关闭的,这是借鉴原则 4「本地服务默认关闭」的好范例)。
 *
 * 命令(L0 风险,纯只读):
 *   yesplaymusic.health           {}              → { ok, alive, ypm_url, latency_ms, http_status, last_error }
 *   yesplaymusic.status           {}              → { ok, alive, playing, currentTime, volume, loop, shuffle, track, last_error }
 *   yesplaymusic.current-track    {}              → { ok, alive, track: {id, name, album, artist, duration, picUrl}, last_error }
 *
 * 实现:Node http.get → JSON 解析。YesPlayMusic 的 JSON 用 { data: {...} } 包裹(非 flat),
 * 跟 LX 不一样,**不**像 LX 直接返 status / name / singer 字段。
 *
 * 设计取舍(沿用 LX 5 步法):
 *   - 失败语义优先:不抛异常,返 { ok: false, alive: false, last_error: ... }
 *   - 不缓存(每次 invoke 现取,YesPlayMusic 切歌立即生效)
 *   - Phase A 范围:**严格只读**,**绝不**触碰 /player/play /pause /next /prev /seek /setVolume /mode
 *
 * 已知 YesPlayMusic /status 响应格式(实测/社区文档):
 *   { "data": { "playing": true, "currentTime": 42, "duration": 215, "volume": 80,
 *               "loop": false, "shuffle": false, "player": {...} } }
 * YesPlayMusic /current-track:
 *   { "data": { "id": 123456, "name": "...", "album": "...", "artist": "...",
 *               "picUrl": "https://...", "duration": 215000 } }
 */

const http = require('http');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ── 默认 YesPlayMusic Open API 端点 ────────────────────────────
// 默认端口 27232,YesPlayMusic 设置 → 实验性功能可改。
function ypmBaseUrl() {
  return process.env.PRISIR_YESPLAYMUSIC_URL || 'http://127.0.0.1:27232';
}

const YPM_TIMEOUT_MS = 1500;

// ── 通用 GET helper(JSON) ────────────────────────────────────
function httpGet(path) {
  return new Promise((resolve) => {
    const url = `${ypmBaseUrl()}${path}`;
    const req = http.get(url, { timeout: YPM_TIMEOUT_MS }, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        const ct = String(res.headers['content-type'] || '');
        let body = buf;
        let parsed = null;
        if (ct.includes('application/json') || buf.trim().startsWith('{')) {
          try { parsed = JSON.parse(buf); } catch {}
        }
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode,
          body,
          parsed,
          url,
        });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('timeout')); });
    req.on('error', (e) => resolve({ ok: false, status: 0, body: '', parsed: null, url, error: e.message }));
  });
}

// ── /status 解析(YesPlayMusic data 包裹) ──────────────────────
async function fetchStatus() {
  const r = await httpGet('/status');
  if (!r.ok) {
    return { ok: false, alive: false, last_error: `status HTTP ${r.status}${r.error ? ': ' + r.error : ''}` };
  }
  const d = r.parsed && r.parsed.data ? r.parsed.data : {};
  return {
    ok: true,
    alive: true,
    playing: Boolean(d.playing),
    currentTime: Number(d.currentTime || 0),
    duration: Number(d.duration || 0),
    volume: Number(d.volume || 0),
    loop: Boolean(d.loop),
    shuffle: Boolean(d.shuffle),
    track: d.player ? {
      id: Number(d.player.id || 0),
      name: String(d.player.name || ''),
      artist: String(d.player.artist || ''),
      album: String(d.player.album || ''),
    } : null,
    last_error: '',
  };
}

// ── /current-track 解析(YesPlayMusic data 包裹) ────────────────
async function fetchCurrentTrack() {
  const r = await httpGet('/current-track');
  if (!r.ok) {
    return { ok: false, alive: false, last_error: `current-track HTTP ${r.status}` };
  }
  const d = r.parsed && r.parsed.data ? r.parsed.data : {};
  return {
    ok: true,
    alive: true,
    track: {
      id: Number(d.id || 0),
      name: String(d.name || ''),
      artist: String(d.artist || ''),
      album: String(d.album || ''),
      picUrl: String(d.picUrl || ''),
      duration: Number(d.duration || 0),
    },
    last_error: '',
  };
}

// ── /health 探活 + 延迟 ────────────────────────────────────────
async function probeHealth() {
  const t0 = Date.now();
  const r = await httpGet('/status');
  const dt = Date.now() - t0;
  return {
    ok: r.ok,
    alive: r.ok,
    ypm_url: ypmBaseUrl(),
    latency_ms: dt,
    http_status: r.status,
    last_error: r.ok ? '' : (r.error || `HTTP ${r.status}`),
  };
}

// ── 注册 extension ───────────────────────────────────────────────
const ext = new PrisIrExt({
  id: 'yesplaymusic-bridge-status',
  name: 'YesPlayMusic 当前播放(只读)',
  version: '0.1.0',
});

ext.registerCommand('yesplaymusic.health', async () => probeHealth());
ext.registerCommand('yesplaymusic.status', async () => fetchStatus());
ext.registerCommand('yesplaymusic.current-track', async () => fetchCurrentTrack());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ─────────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchStatus, fetchCurrentTrack, ypmBaseUrl, httpGet,
  };
}