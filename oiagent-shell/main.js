// oiagent-shell — oiagent 本地对话壳(Electron)主进程。
//
// 定位(prisirwork-foundation-integration-design §5.1 / F7):
//   不启浏览器也能和 oiagent 对话。主进程负责:
//     ① spawn + 看护 oiagent_web.py(127.0.0.1:18802,国画风聊天 UI,SQLite 持久化)
//     ② 系统托盘(最小化到托盘,不退出)
//     ③ 全局热键(默认 Ctrl+Shift+O 呼出/隐藏)
//     ④ 开机自启(可配)
//
// 红线(token/权限纪律,与 F5 同源):
//   - PrisirWork token 只在主进程读取(0600 配置文件),经 preload 以「是否存在」
//     布尔告知渲染层,绝不把 token 本体打进 renderer bundle / 暴露给页面 JS。
//   - 渲染进程 contextIsolation 开、nodeIntegration 关,只经白名单 IPC 与主进程通信。
//   - oiagent_web 只监听 127.0.0.1;壳加载的也是回环地址,不触外网。
//
// 与已归档 securedm-shell(Tauri)不同:本壳走 Electron(用户拍板),复用 oiagent_web.py。
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const os = require("os");

// ---------- 配置 ----------
const REPO_ROOT = path.resolve(__dirname, "..");          // oi_enhancements 根
const WEB_SCRIPT = path.join(REPO_ROOT, "oiagent_web.py");
const WEB_HOST = "127.0.0.1";
const WEB_PORT = parseInt(process.env.OIAGENT_WEB_PORT || "18802", 10);
const WEB_URL = `http://${WEB_HOST}:${WEB_PORT}`;
const HOTKEY = process.env.OIAGENT_SHELL_HOTKEY || "CommandOrControl+Shift+O";
const PYTHON = process.env.OIAGENT_PYTHON || "python";

// ---------- token 纪律:主进程读 0600 配置,只把「是否存在」告知渲染层 ----------
function prisirTokenPath() {
  return process.env.PRISIR_WORK_CONFIG || path.join(os.homedir(), ".prisir", "work.json");
}
function prisirTokenPresent() {
  try {
    const data = JSON.parse(fs.readFileSync(prisirTokenPath(), "utf-8"));
    return !!(data.token && String(data.token).trim());
  } catch {
    return false;
  }
}
// 注意:绝不把 token 本体暴露给渲染层。下面的 IPC 只回布尔。

// ---------- oiagent_web 子进程看护 ----------
let webProc = null;
let webReady = false;

function webUp(cb) {
  const req = http.get({ host: WEB_HOST, port: WEB_PORT, path: "/", timeout: 1500 }, (res) => {
    res.resume();
    cb(true);
  });
  req.on("error", () => cb(false));
  req.on("timeout", () => { req.destroy(); cb(false); });
}

function startWeb() {
  if (webProc) return;
  // 已被别的进程占用端口就直接复用,不重复起。
  webUp((up) => {
    if (up) { webReady = true; return; }
    webProc = spawn(PYTHON, [WEB_SCRIPT, "--port", String(WEB_PORT)], {
      cwd: REPO_ROOT,
      stdio: "ignore",          // 不把对话日志引到壳 stdout
      windowsHide: true,
    });
    webProc.on("exit", () => { webProc = null; webReady = false; });
    // 轮询等就绪
    const t = setInterval(() => {
      webUp((up) => { if (up) { webReady = true; clearInterval(t); loadWhenReady(); } });
    }, 400);
    setTimeout(() => clearInterval(t), 20000);
  });
}

// ---------- 窗口 ----------
let win = null;
let tray = null;
let quitting = false;

function createWindow() {
  win = new BrowserWindow({
    width: 1040,
    height: 760,
    minWidth: 720,
    minHeight: 520,
    title: "oiagent 对话",
    icon: path.join(REPO_ROOT, "assets", "prisir-mark-256.png"),
    backgroundColor: "#f6f1e7",   // 国画纸色,与聊天 UI 一致,避免白闪
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,      // 红线:渲染层拿不到 Node
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // 外链一律交给系统浏览器,壳内不导航出回环。
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(WEB_URL)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });

  loadWhenReady();

  // 最小化到托盘而不是退出(常驻对话壳)。
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => { win = null; });
}

function loadWhenReady() {
  if (!win) return;
  if (webReady) {
    win.loadURL(WEB_URL);
  } else {
    // 起服务过渡页(本地 data URL,不触网)。
    win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          `<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#f6f1e7;color:#5b5548;font-family:system-ui"><div>正在唤醒 oiagent…</div></body>`
        )
    );
  }
}

function toggleWindow() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) win.hide();
  else { win.show(); win.focus(); loadWhenReady(); }
}

// ---------- 托盘 ----------
function createTray() {
  // 用国画风 mark 若存在,否则空图标(Electron 需要有效 image)。
  const iconPath = path.join(REPO_ROOT, "assets", "prisir-mark-256.png");
  let img = nativeImage.createFromPath(iconPath);
  if (img.isEmpty()) img = nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip("oiagent 对话");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "打开 oiagent", click: () => { if (win) { win.show(); loadWhenReady(); } else createWindow(); } },
    { label: "开机自启", type: "checkbox", checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }) },
    { type: "separator" },
    { label: "退出", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", toggleWindow);
}

// ---------- IPC(白名单;渲染层只能问这些) ----------
ipcMain.handle("shell:info", () => ({
  webUrl: WEB_URL,
  webReady,
  prisirTokenPresent: prisirTokenPresent(),  // 布尔,不回 token 本体
  version: app.getVersion(),
}));
ipcMain.handle("shell:toggle", () => toggleWindow());

// ---------- 生命周期 ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { win.show(); win.focus(); loadWhenReady(); } });

  app.whenReady().then(() => {
    startWeb();
    createWindow();
    createTray();
    globalShortcut.register(HOTKEY, toggleWindow);
    app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });

  app.on("before-quit", () => { quitting = true; });
  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    if (webProc) { try { webProc.kill(); } catch {} webProc = null; }
  });

  // 所有窗口关上不退(托盘常驻),macOS 惯例;Windows 也一样常驻托盘。
  app.on("window-all-closed", () => { /* 常驻托盘,不 app.quit() */ });
}
