'use strict';

/**
 * lx-music-bridge-status v0.1.0 — LX Music Desktop 当前播放(只读)
 *
 * 数据来源:LX Music Desktop 的 Open API(默认 127.0.0.1:23330)。
 * 通过实测(LX Desktop 2.x 默认开启 Open API,无需鉴权)确认 /status 与 /lyric
 * 是 anonymous 开放的只读 endpoint;其余 401/403。
 *
 * 命令(L0 风险,纯只读):
 *   lx.status  {}                        → { ok, status, name, singer, album,
 *                                            duration, progress, playbackRate,
 *                                            lyricLineText, lx_alive, last_error }
 *   lx.lyric   {}                        → { ok, lrc_raw, lines: [{t_ms, text}],
 *                                            current_index, current_text, lx_alive }
 *   lx.health  {}                        → { ok, lx_alive, lx_url, latency_ms,
 *                                            last_check_at }
 *
 * 实现:Node http.get → JSON / LRC 文本解析(纯 JS,无 npm 依赖)。
 * 100% 本地,**不发起任何外网请求**,符合 P3.10b 0 上传红线。
 *
 * 已知 LX Desktop 行为(2026-10-06 实测):
 *   - /status 返 JSON 含 status / name / singer / albumName / lyricLineText /
 *     duration / progress / playbackRate
 *   - /lyric 返 LRC 文本(含 [ar:]/[ti:]/[al:]/[00:00.165]万神纪 ... 等标签)
 *   - /songList / /playList 等 403(LX Desktop 没启用或需 Origin)
 *   - status 字段取值:playing / paused / stopped / unknown
 *
 * 设计取舍:
 *   - 不缓存 status/LRC(每次 invoke 都现取,避免 LX Desktop 重启 / 切歌后状态陈旧)
 *   - LRC 解析用精简 LRC 正则(只抽 [mm:ss.fff] 行,跳过 [ar:][ti:] 等标签)
 *   - lyric.current_index = 找到 progress 所在 LRC 时间段的最后一行
 *   - 失败语义:返回 { ok: false, lx_alive: false, last_error: ... } 而不是抛异常
 *     (主对话拿 LLM 直接告诉用户「LX Desktop 没起来」)
 *
 * Phase A 范围:**严格只读**,不调 /play /pause /next /prev /seek 等 mutating 接口。
 * Phase B/C 待用户验证 Phase A 价值后再 ship 播放控制 + AI 推荐。
 */

const http = require('http');

const { PrisIrExt } = require('@prisir/extension-sdk');

// ── 默认 LX Desktop Open API 端点 ─────────────────────────────────
// 默认端口 23330,LX Desktop 设置 → 「Open API」可改。
// 端口解析顺序:env PRISIR_LX_URL > 默认 127.0.0.1:23330。
function lxBaseUrl() {
  return process.env.PRISIR_LX_URL || 'http://127.0.0.1:23330';
}

const LX_TIMEOUT_MS = 1500;       // 单 endpoint 超时,够 LX Desktop 内网返回

// ── 通用 GET helper(JSON or text) ────────────────────────────────
function httpGet(path) {
  return new Promise((resolve) => {
    const url = `${lxBaseUrl()}${path}`;
    const req = http.get(url, { timeout: LX_TIMEOUT_MS }, (res) => {
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

// ── /status 解析(LX Desktop 字段) ────────────────────────────────
async function fetchStatus() {
  const r = await httpGet('/status');
  if (!r.ok) {
    return { ok: false, lx_alive: false, last_error: `status HTTP ${r.status}${r.error ? ': ' + r.error : ''}` };
  }
  const j = r.parsed || {};
  return {
    ok: true,
    lx_alive: true,
    status: String(j.status || 'unknown'),
    name: String(j.name || ''),
    singer: String(j.singer || ''),
    album: String(j.albumName || ''),
    duration: Number(j.duration || 0),
    progress: Number(j.progress || 0),
    playbackRate: Number(j.playbackRate || 1),
    lyricLineText: String(j.lyricLineText || ''),
    last_error: '',
  };
}

// ── /lyric 解析(LRC 文本 → 行) ────────────────────────────────────
// 返回 [{t_ms, text}, ...](按时间升序,跳过 [ar:]/[ti:]/[al:] 等元数据标签)
function parseLrc(raw) {
  const out = [];
  if (!raw) return out;
  const lines = raw.split(/\r?\n/);
  const tagRe = /^\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/;
  for (const ln of lines) {
    const m = ln.match(tagRe);
    if (!m) continue;
    const min = Number(m[1]);
    const sec = Number(m[2]);
    const ms = m[3] ? Number(m[3].padEnd(3, '0').slice(0, 3)) : 0;
    const t_ms = min * 60_000 + sec * 1000 + ms;
    const text = ln.slice(m[0].length).trim();
    if (!text) continue;
    out.push({ t_ms, text });
  }
  out.sort((a, b) => a.t_ms - b.t_ms);
  return out;
}

// 当前 progress → 在 LRC 中所在行 index(最后一个 t_ms <= progress 的行)
function currentIndex(lines, progress_ms) {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].t_ms <= progress_ms) idx = i;
    else break;
  }
  return idx;
}

async function fetchLyric(progressSec) {
  const r = await httpGet('/lyric');
  if (!r.ok) {
    return { ok: false, lrc_raw: '', lines: [], current_index: -1, current_text: '', lx_alive: false, last_error: `lyric HTTP ${r.status}` };
  }
  const lines = parseLrc(r.body);
  const progress_ms = Math.max(0, Math.floor((progressSec || 0) * 1000));
  const idx = currentIndex(lines, progress_ms);
  return {
    ok: true,
    lrc_raw: r.body,
    lines,
    current_index: idx,
    current_text: idx >= 0 ? lines[idx].text : '',
    lx_alive: true,
    last_error: '',
  };
}

// ── lx.health — 探活 + 延迟 ───────────────────────────────────────
async function probeHealth() {
  const t0 = Date.now();
  const r = await httpGet('/status');
  const dt = Date.now() - t0;
  return {
    ok: r.ok,
    lx_alive: r.ok,
    lx_url: lxBaseUrl(),
    latency_ms: dt,
    last_check_at: Date.now(),
    http_status: r.status,
    last_error: r.ok ? '' : (r.error || `HTTP ${r.status}`),
  };
}

// ── 注册 extension ────────────────────────────────────────────────
const ext = new PrisIrExt({
  id: 'lx-music-bridge-status',
  name: '落雪音乐当前播放(只读)',
  version: '0.1.0',
});

ext.registerCommand('lx.status', async () => fetchStatus());
ext.registerCommand('lx.lyric', async () => {
  // lyric 需要 progress — 内部再拉一次 status 拿 progress
  const st = await fetchStatus();
  if (!st.ok) return { ok: false, lx_alive: false, last_error: st.last_error };
  return await fetchLyric(st.progress);
});
ext.registerCommand('lx.health', async () => probeHealth());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子: 暴露内部 helper 供 sandbox 单测访问 ──────────────────
// index.js 在生产路径走 PrisIrExt.start(),module.exports 仅供 __tests__/run.js
// 注入 vm sandbox 后调用 parseLrc / currentIndex / fetchStatus / fetchLyric /
// probeHealth / lxBaseUrl。这不会改变 SDK 装载路径(SDK 拿的是 ext 实例)。
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { parseLrc, currentIndex, fetchStatus, fetchLyric, probeHealth, lxBaseUrl };
}
