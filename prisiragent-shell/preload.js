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
  // P3.10a(2026-10-04)桌面弹卡 toast — 主进程节流 + 队列 + 通知偏好过滤
  showToast: (payload) => ipcRenderer.invoke("shell:show-toast", payload),
  // 通知偏好 — all / errors / off
  getToastLevel: () => ipcRenderer.invoke("shell:get-toast-level"),
  setToastLevel: (v) => ipcRenderer.invoke("shell:set-toast-level", v),
  // 标记:在壳内运行(供 prisiragent_web 区分「壳内」vs「纯浏览器」)
  inShell: true,
});

// P2.5+25(2026-10-03)music 子窗 / 桌面歌词子窗 专用前缀 prisIragent。
// 2026-10-05:music / lyric / eq 模块已归档,IPC 暴露整段删除。
// 保留 prisIragent 命名空间(stub)+ archived 标记,渲染层若误访问 → 拿到 undefined,
// (name in window 仍是合法对象,属性访问 undefined,不会 throw)。
contextBridge.exposeInMainWorld("prisIragent", {
  _archived: true,
  _archivedAt: "2026-10-05",
  _note: "music/lyric/eq 模块已归档;所有 IPC 暴露已删除。",
});
