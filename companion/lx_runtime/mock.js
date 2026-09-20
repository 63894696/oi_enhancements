// mock.js — M3.28 Phase 1 PoC 兜底源
// juhe 后端实测返 "source not match"(400),ikun api 被 DNS 墙挡;
// 用 mock 验证 shim 协议层:能正确路由 request event、能返 URL 给 Python。
//
// 任意 source 都返同一段公开音频(StarWars3.wav),用于在浏览器里点 ▶ 测试按钮听到声音。
// 真实 mp3 直链不在 Phase 1 范围。
/*!
 * @name mock (M3.28 兜底)
 * @description Phase 1 PoC 用,验证 jsdom shim 协议层
 * @version 1
 */
const { EVENT_NAMES, request, on, send, version } = globalThis.lx;
const MOCK_URL = "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3";
const FAKE_SOURCES = { mock: { actions: ["musicUrl"], name: "mock", qualitys: ["320k"], type: "music" } };

// 跳过 init 网络请求,直接声明 inited
send(EVENT_NAMES.inited, { sources: FAKE_SOURCES, update: { version: version || "1", log: "", updateUrl: "" } });

on(EVENT_NAMES.request, async ({ action, source, info }) => {
    if (action !== "musicUrl") throw new Error(`mock only supports musicUrl, got: ${action}`);
    const songId = (info && info.musicInfo && (info.musicInfo.hash || info.musicInfo.songmid)) || "unknown";
    const quality = (info && info.type) || "320k";
    // 模拟一点点延迟,验真 RPC 异步性
    await new Promise(r => setTimeout(r, 30));
    return `${MOCK_URL}#mock=${source}/${songId}/${quality}`;
});
