// preload — 渲染层与主进程之间的受控桥(红线:token 本体永不下发)。
//
// contextIsolation 开 + sandbox 开,渲染页(oiagent_web)拿不到 Node。
// 这里只暴露一个最小白名单 API:shellInfo()。它返回的 prisirTokenPresent
// 只是「本地是否已配 PrisirWork token」的布尔,便于 UI 提示「地基已连/未连」,
// 绝不回 token 本体 —— token 0600 在主进程,不进 renderer bundle。
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("oiShell", {
  // 返回 { webUrl, webReady, prisirTokenPresent, version }
  shellInfo: () => ipcRenderer.invoke("shell:info"),
  toggle: () => ipcRenderer.invoke("shell:toggle"),
  // 标记:在壳内运行(供 oiagent_web 区分「壳内」vs「纯浏览器」)
  inShell: true,
});
