'use strict';

/**
 * funkwhale-bridge-status v0.1.0 — Funkwhale 当前播放(只读, Subsonic 协议)
 *
 * 数据来源: Funkwhale 的 Subsonic 兼容 API(默认 127.0.0.1:5000)。
 *            Funkwhale 是联邦(federation)音乐平台,Subsonic 端点直接挂 web 根路径。
 * 鉴权:    Subsonic v1.13.0+ 强制 salted token — `token = md5(password + salt)`
 *          (每次请求客户端派新 salt,服务端用本次盐算 token 比对)
 *          借鉴原则 5「Token≠密码」:截获单次 token 不能重放。
 *
 * **Phase C 统一媒体 SDK(2026-10-07)**:"三薄壳"第三次复用 subsonic-client.js
 * (前两次:Navidrome / Audiobookshelf — 但 ABS 是 Bearer 不是 Subsonic)—
 * Funkwhale 是 **第一个真正 0 修改 SDK** 的扩展。**和 Navidrome 几乎一样**,只差
 * 端口 (5000 vs 4533) + endpoint 后缀 (.view vs 无)— 端点的 .view 后缀是
 * OpenSubsonic 规范(Funkwhale 用新版),Navidrome 用旧版(无 .view)。SDK
 * 接受任意 endpoint 字符串,**扩展自己**拼 .view 后缀即可。
 *
 * 命令(L0 风险, 纯只读):
 *   funkwhale.health       {}      → { ok, alive, fw_url, latency_ms, status, last_error }
 *   funkwhale.now-playing  {}      → { ok, alive, now_playing: [...entries], last_error }
 *   funkwhale.license      {}      → { ok, alive, valid, email, licenseExpires, last_error }
 *
 * **绝不**触碰 jukeboxControl / set / download / scrobble / stream 等 mutating 接口
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_FUNKWHALE_URL    默认 http://127.0.0.1:5000
 *   PRISIR_FUNKWHALE_USER   默认空(空时 funkwhale.health 返回 alive=false,提示需配置)
 *   PRISIR_FUNKWHALE_PASS   默认空(token 计算需 password,空时返 ok=false + last_error)
 *   PRISIR_FUNKWHALE_TOKEN  可选 — 用户预派生 token 跳过明文密码
 *   PRISIR_FUNKWHALE_CLIENT 默认 'prisirai'(Subsonic c 参数)
 *   PRISIR_FUNKWHALE_API_V  默认 '1.16.1'(Funkwhale 支持 up to 1.16.0,默认 1.16.1 降级)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGetSubsonic, parseSubsonic, mapNowPlayingEntry, flattenNowPlaying } = require('../_scaffold/subsonic-client');

// ── env 字段注入(扩展自己的字段名空间) ──────────────────
function fwConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_FUNKWHALE_URL || 'http://127.0.0.1:5000',
    user: process.env.PRISIR_FUNKWHALE_USER || '',
    password: process.env.PRISIR_FUNKWHALE_PASS || '',
    preToken: process.env.PRISIR_FUNKWHALE_TOKEN || '',
    client: process.env.PRISIR_FUNKWHALE_CLIENT || 'prisirai',
    apiV: process.env.PRISIR_FUNKWHALE_API_V || '1.16.1',
  });
}

// ── /rest/ping.view 健康检查 ──────────────────────────
async function probeHealth() {
  const cfg = fwConfig();
  const t0 = Date.now();
  const r = await httpGetSubsonic({
    config: cfg,
    endpoint: 'ping.view',  // OpenSubsonic 规范加 .view 后缀
  });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      fw_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) {
    return {
      ok: false, alive: p.alive,
      fw_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      last_error: p.last_error,
    };
  }
  return {
    ok: true, alive: true,
    fw_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    last_error: '',
  };
}

// ── /rest/getNowPlaying.view 解析当前播放条目 ────────────────
async function fetchNowPlaying() {
  const cfg = fwConfig();
  const r = await httpGetSubsonic({ config: cfg, endpoint: 'getNowPlaying.view' });
  if (!r.ok) {
    return {
      ok: false, alive: false,
      last_error: `getNowPlaying HTTP ${r.status}${r.error ? ': ' + r.error : ''}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) return { ok: false, alive: p.alive, last_error: p.last_error };
  return {
    ok: true, alive: true,
    now_playing: flattenNowPlaying(p.body).map(mapNowPlayingEntry),
    last_error: '',
  };
}

// ── /rest/getLicense.view 解析 license 信息 ───────────
async function fetchLicense() {
  const cfg = fwConfig();
  const r = await httpGetSubsonic({ config: cfg, endpoint: 'getLicense.view' });
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

// ── 注册 extension ──────────────────────────────
const ext = new PrisIrExt({
  id: 'funkwhale-bridge-status',
  name: 'Funkwhale 当前播放(只读, Subsonic 协议)',
  version: '0.1.0',
});

ext.registerCommand('funkwhale.health', async () => probeHealth());
ext.registerCommand('funkwhale.now-playing', async () => fetchNowPlaying());
ext.registerCommand('funkwhale.license', async () => fetchLicense());

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchNowPlaying, fetchLicense,
    fwConfig,
  };
}