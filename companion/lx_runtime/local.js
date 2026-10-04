// local.js — 2026-10-04 bug fix:local-only LX 源
//
// mock.js 永远返 googleapis URL(国内 DNS 不可达),
// juhe.js 用第三方公共服务 api.music.lerd.dpdns.org(不稳)。
// 从开发至今所有点歌都掉 seed.mp3 兜底,所有歌共享同一首 White Christmas 音频。
//
// 修复:加 local.js 源,musicUrl 直接返 ok=True 但 url="local://",
//   Python 端 seed_from_url 识别 "local://" → 走 LocalLibrary 真 mp3 匹配,
//   不调任何外网。完全 local-only,沿用 P3.10b 0 上传红线。
//
// 即使用户显式 sources=["local.js", "mock.js", "juhe.js"] 启用多源,
//   local.js 永远排第一,优先返 local://(即时成功)。
/*!
 * @name local (2026-10-04 local-only)
 * @description 完全本地源,不调任何外网 LX;Python 端 seed_from_url 识别 local:// URL
 * @version 1
 */
const { EVENT_NAMES, request, on, send, version } = globalThis.lx;
const FAKE_SOURCES = { local: { actions: ["musicUrl"], name: "local", qualitys: ["320k"], type: "music" } };

send(EVENT_NAMES.inited, { sources: FAKE_SOURCES, update: { version: version || "1", log: "", updateUrl: "" } });

on(EVENT_NAMES.request, async ({ action, source, info }) => {
    if (action !== "musicUrl") throw new Error(`local only supports musicUrl, got: ${action}`);
    // 不调外网,直接返 local:// 占位,Python 端 seed_from_url 接到后
    // 会查 LocalLibrary 真 mp3;若没真 mp3 → seed.mp3 兜底。
    const songId = (info && info.musicInfo && (info.musicInfo.hash || info.musicInfo.songmid)) || "unknown";
    const quality = (info && info.type) || "320k";
    return `local://prisir/${songId}/${quality}`;
});
