'use strict';

/**
 * navidrome-bridge-status v0.1.0 — Navidrome 当前播放(只读, Subsonic 协议)
 *
 * 数据来源: Navidrome 的 Subsonic 兼容 API(默认 127.0.0.1:4533)。
 * 鉴权: Subsonic v1.13.0+ 强制 salted token — `token = md5(password + salt)`,
 *       每次请求客户端派新 salt + 服务端用本次盐算 token 比对。**这是 v2 对比
 * 研究借鉴点 5「Token≠密码」的优秀范例**: 截获一个 token 无法跨端点重放
 * (因为下次 salt 是新的,服务端不存 hash)。
 *
 * **Phase C 统一媒体 SDK(2026-10-07)**:Subsonic 协议已沉淀到
 * extensions/_scaffold/subsonic-client.js,本扩展只做「薄壳」— 注入 env 字段、
 * 拼装命令注册、把 nowPlaying entry 映射成 PrisirAI 友好字段。
 *
 * 命令(L0 风险, 纯只读):
 *   navidrome.health       {}      → { ok, alive, nd_url, latency_ms, status, last_error }
 *   navidrome.now-playing  {}      → { ok, alive, now_playing: [...entries], last_error }
 *   navidrome.license      {}      → { ok, alive, valid, email, licenseExpires, last_error }
 *
 * **绝不**触碰 jukeboxControl/set/clear/play/stop/skip / setGain / getDownload / stream
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_NAVIDROME_URL     默认 http://127.0.0.1:4533
 *   PRISIR_NAVIDROME_USER    默认空(空时 navidrome.health 返回 alive=false,提示需配置)
 *   PRISIR_NAVIDROME_PASS    默认空(token 计算需 password,空时返 ok=false + last_error)
 *   PRISIR_NAVIDROME_TOKEN   可选 — 用户也可直接预派生 token,跳过明文密码传入
 *   PRISIR_NAVIDROME_CLIENT  默认 'prisirai'(Subsonic c 参数)
 *   PRISIR_NAVIDROME_API_V   默认 '1.16.1'(Subsonic v 参数,服务端校验 major+minor)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGetSubsonic, parseSubsonic, mapNowPlayingEntry, flattenNowPlaying } = require('../_scaffold/subsonic-client');

// ── env 字段注入(扩展自己的字段名空间) ──
function ndConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_NAVIDROME_URL || 'http://127.0.0.1:4533',
    user: process.env.PRISIR_NAVIDROME_USER || '',
    password: process.env.PRISIR_NAVIDROME_PASS || '',
    preToken: process.env.PRISIR_NAVIDROME_TOKEN || '',
    client: process.env.PRISIR_NAVIDROME_CLIENT || 'prisirai',
    apiV: process.env.PRISIR_NAVIDROME_API_V || '1.16.1',
  });
}

// ── /ping 健康检查 ──────────────────────────────────────────────
async function probeHealth() {
  const cfg = ndConfig();
  const t0 = Date.now();
  const r = await httpGetSubsonic({
    config: cfg,
    endpoint: 'ping',
  });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      nd_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      last_error: r.error || `HTTP ${r.status}`,
    };
  }
  const p = parseSubsonic(r);
  if (!p.ok) {
    return {
      ok: false, alive: p.alive,
      nd_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
      last_error: p.last_error,
    };
  }
  return {
    ok: true, alive: true,
    nd_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status,
    last_error: '',
  };
}

// ── /getNowPlaying 解析当前播放条目 ────────────────────────────
async function fetchNowPlaying() {
  const cfg = ndConfig();
  const r = await httpGetSubsonic({ config: cfg, endpoint: 'getNowPlaying' });
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

// ── /getLicense 解析 license 信息(纯探活 + server 信息) ─────────
async function fetchLicense() {
  const cfg = ndConfig();
  const r = await httpGetSubsonic({ config: cfg, endpoint: 'getLicense' });
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
    ndConfig,
  };
}