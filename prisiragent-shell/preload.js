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
  // P3.2 视觉调档 — 透明度滑杆 + 字号缩放
  setLyricOpacity: (v) => ipcRenderer.invoke("shell:setLyricOpacity", v),
  setLyricScale: (v) => ipcRenderer.invoke("shell:setLyricScale", v),
  // P3.4 单/双行 toggle — 1 单行紧凑 / 2 双行(active + 下一行预览)
  setLyricLines: (v) => ipcRenderer.invoke("shell:setLyricLines", v),
  // P3.5(2026-10-04)music 10 段 EQ 均衡器 — 6 个 IPC 暴露 + 状态订阅
  //  - getEqState       → bootstrap 拉初始态
  //  - setEqGain(i, dB) → 单段调整(idx 0..9, dB -12..+12)
  //  - setEqPreset(name) → 应用预置(flat/vocal/bass/treble/rock/electronic)
  //  - setEqEnabled(b)  → 主开关(true=10 段 active / false=每段 gain=0 直通)
  //  - resetEq()         → 重置为 flat(主开关保留)
  //  - openEqWindow()    → 托盘同步触发(MusicView 抽屉的"独立窗"按钮也调这里)
  getEqState: () => ipcRenderer.invoke("shell:get-eq-state"),
  setEqGain: (idx, dB) => ipcRenderer.invoke("shell:set-eq-gain", idx, dB),
  setEqPreset: (name) => ipcRenderer.invoke("shell:set-eq-preset", name),
  setEqEnabled: (b) => ipcRenderer.invoke("shell:set-eq-enabled", b),
  resetEq: () => ipcRenderer.invoke("shell:reset-eq"),
  openEqWindow: () => ipcRenderer.invoke("shell:openEqWindow"),
  // 订阅主进程推过来的 EQ 状态变化(MusicView / LyricOnlyView / 独立 EQ 窗任一处改 → 其余 mirror)
  onEqStateChanged: (cb) => {
    if (typeof cb !== "function") return () => {};
    const listener = (_e, payload) => { try { cb(payload); } catch (_) {} };
    ipcRenderer.on("shell:eqStateChanged", listener);
    return () => ipcRenderer.removeListener("shell:eqStateChanged", listener);
  },
  // 订阅主进程推过来的歌词窗状态变化(toggle 后自动 push,LyricOnlyView 拿来 apply CSS class)
  // 返回 unsubscribe 函数,渲染层组件 unmount 时调用。
  onLyricStateChanged: (cb) => {
    if (typeof cb !== "function") return () => {};
    const listener = (_e, payload) => { try { cb(payload); } catch (_) {} };
    ipcRenderer.on("shell:lyricStateChanged", listener);
    return () => ipcRenderer.removeListener("shell:lyricStateChanged", listener);
  },
});
