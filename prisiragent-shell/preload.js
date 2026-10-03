// preload — 渲染层与主进程之间的受控桥(红线:token 本体永不下发)。
//
// contextIsolation 开 + sandbox 开,渲染页(prisiragent_web)拿不到 Node。
// 这里只暴露一个最小白名单 API:shellInfo()。它返回的 prisirTokenPresent
// 只是「本地是否已配 PrisirWork token」的布尔,便于 UI 提示「地基已连/未连」,
// 绝不回 token 本体 —— token 0600 在主进程,不进 renderer bundle。
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("oiShell", {
  // 返回 { webUrl, webReady, prisirTokenPresent, version }
  shellInfo: () => ipcRenderer.invoke("shell:info"),
  toggle: () => ipcRenderer.invoke("shell:toggle"),
  // v2.0 反馈卡:让 prisiragent_web 在壳内用 IPC 打开系统浏览器(替代 window.open)
  // 红线:只允许 https:// 与 babelspan.com;其他 URL 拒绝,防被任意站点诱导打开。
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
  // 标记:在壳内运行(供 prisiragent_web 区分「壳内」vs「纯浏览器」)
  inShell: true,
});

// P2.5+25(2026-10-03)music 子窗 / 桌面歌词子窗 专用前缀 prisIragent。
// 区分 oiShell(主 web 用)的策略 — 主 web 不暴露歌词窗开关,
// 减少渲染层攻击面(openLyric/closeLyric 触发的是 BrowserWindow new/close)。
// P2.5+26(2026-10-03):扩 alwaysOnTop/lockDrag/bounds 4 方法 + onLyricStateChanged 订阅
// (托盘 toggle 后主进程主动 push,渲染层 bootstrap 也可主动 getLyricState 拉一次)。
contextBridge.exposeInMainWorld("prisIragent", {
  // 桌面歌词独立窗
  openLyric: () => ipcRenderer.invoke("shell:openLyric"),
  closeLyric: () => ipcRenderer.invoke("shell:closeLyric"),
  // P2.5+26 alwaysOnTop / lockDrag / bounds
  toggleLyricAlwaysOnTop: () => ipcRenderer.invoke("shell:toggleLyricAlwaysOnTop"),
  toggleLyricLockDrag: () => ipcRenderer.invoke("shell:toggleLyricLockDrag"),
  getLyricState: () => ipcRenderer.invoke("shell:getLyricState"),
  setLyricBounds: (b) => ipcRenderer.invoke("shell:setLyricBounds", b),
  // 订阅主进程推过来的歌词窗状态变化(toggle 后自动 push,LyricOnlyView 拿来 apply CSS class)
  // 返回 unsubscribe 函数,渲染层组件 unmount 时调用。
  onLyricStateChanged: (cb) => {
    if (typeof cb !== "function") return () => {};
    const listener = (_e, payload) => { try { cb(payload); } catch (_) {} };
    ipcRenderer.on("shell:lyricStateChanged", listener);
    return () => ipcRenderer.removeListener("shell:lyricStateChanged", listener);
  },
});
