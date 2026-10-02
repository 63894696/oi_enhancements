// prisiragent-shell — prisiragent 本地对话壳(Electron)主进程。
//
// 定位(prisirwork-foundation-integration-design §5.1 / F7):
//   不启浏览器也能和 prisiragent 对话。主进程负责:
//     ① spawn + 看护 prisiragent_web.py(127.0.0.1:18802,国画风聊天 UI,SQLite 持久化)
//     ② 系统托盘(最小化到托盘,不退出)
//     ③ 全局热键(默认 Ctrl+Shift+O 呼出/隐藏)
//     ④ 开机自启(可配)
//
// 红线(token/权限纪律,与 F5 同源):
//   - PrisirWork token 只在主进程读取(0600 配置文件),经 preload 以「是否存在」
//     布尔告知渲染层,绝不把 token 本体打进 renderer bundle / 暴露给页面 JS。
//   - 渲染进程 contextIsolation 开、nodeIntegration 关,只经白名单 IPC 与主进程通信。
//   - prisiragent_web 只监听 127.0.0.1;壳加载的也是回环地址,不触外网。
//
// 与已归档 securedm-shell(Tauri)不同:本壳走 Electron(用户拍板),复用 prisiragent_web.py。
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, shell, Notification } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const os = require("os");

// ---------- v2.0 日志基础设施 ----------
// 所有诊断日志落 userData/logs/,三层文件:electron-main.log(主进程自身)
// + spawn-stdout.log(Python exe stdout) + spawn-stderr.log(stderr)。
// 装包后 userData = %APPDATA%/prisiragent-shell(Win),开发态也是。
const USER_LOGS_DIR = path.join(app.getPath("userData"), "logs");
try { fs.mkdirSync(USER_LOGS_DIR, { recursive: true }); } catch (_) {}
const LOG_MAIN = path.join(USER_LOGS_DIR, "electron-main.log");
const LOG_STDOUT = path.join(USER_LOGS_DIR, "spawn-stdout.log");
const LOG_STDERR = path.join(USER_LOGS_DIR, "spawn-stderr.log");
function logTs() { return new Date().toISOString(); }
function logTo(file, level, category, msg, extra) {
  try {
    const line = `${logTs()} ${level} ${category} ${msg}${extra ? " " + extra : ""}\n`;
    fs.appendFileSync(file, line, "utf8");
  } catch (_) { /* 永不抛 */ }
}
// 滚动:每个文件超 5MB → 改名 .log.1
function rotateLog(file) {
  try {
    if (!fs.existsSync(file)) return;
    const stat = fs.statSync(file);
    if (stat.size < 5 * 1024 * 1024) return;
    for (let i = 3; i >= 1; i--) {
      const src = `${file}.${i}`;
      const dst = `${file}.${i + 1}`;
      if (fs.existsSync(src)) fs.renameSync(src, dst);
    }
    fs.renameSync(file, `${file}.1`);
  } catch (_) {}
}
function logInfo(cat, msg, extra)  { rotateLog(LOG_MAIN); logTo(LOG_MAIN, "INFO",  cat, msg, extra); }
function logWarn(cat, msg, extra)  { rotateLog(LOG_MAIN); logTo(LOG_MAIN, "WARN",  cat, msg, extra); }
function logError(cat, msg, extra) { rotateLog(LOG_MAIN); logTo(LOG_MAIN, "ERROR", cat, msg, extra); }
function logDebug(cat, msg, extra) { rotateLog(LOG_MAIN); logTo(LOG_MAIN, "DEBUG", cat, msg, extra); }
logInfo("boot", "electron main process started", `pid=${process.pid} ver=${process.versions.electron} userData=${app.getPath("userData")}`);

// 兜底:捕获未处理异常,落日志(避免窗口静默崩用户看不到)
process.on("uncaughtException", (err) => {
  logError("uncaught", err.message || String(err), `stack=${(err.stack || "").split("\n")[0]}`);
});
process.on("unhandledRejection", (reason) => {
  logError("unhandledRejection", String(reason), "");
});

// ---------- 配置 ----------
// __dirname/.. 在两种环境含义不同:
//   开发态:prisiragent-shell/ 在 oi_enhancements/ 内 → REPO_ROOT = oi_enhancements/
//   装包后:prisiragent-shell/ 在 $INSTDIR\PrisirAI\ 内 → REPO_ROOT = $INSTDIR(PrisirAI.exe 同级)
// 探测多候选路径,保证装包后能找到 PrisirAI.exe。
const PARENT_DIR = path.resolve(__dirname, "..");
const WEB_SCRIPT = path.join(PARENT_DIR, "prisiragent_web.py");
// 发布态(装包后):$INSTDIR\PrisirAI.exe;开发态:$REPO/dist/PrisirAI.exe;旧 .bak 也认。
const CORE_EXE_CANDIDATES = [
  path.join(PARENT_DIR, "PrisirAI.exe"),                    // 装包后:与 prisiragent-shell 同级
  path.join(PARENT_DIR, "dist", "PrisirAI.exe"),            // 开发态
  path.join(PARENT_DIR, "dist", "PrisirAI-core.exe"),       // 旧名回退(v0.x 阶段产物)
];
function resolveCoreExe() {
  for (const p of CORE_EXE_CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  return CORE_EXE_CANDIDATES[0];   // 默认返回装包后路径,让 spawn 报错时用户能看到准确路径
}
const REPO_ROOT = PARENT_DIR;          // 兼容旧代码(暂留,实际未用)
const WEB_HOST = "127.0.0.1";
// P2.5+11:端口解析从 env → HKCU/JSON → 默认,与 Tauri 三段优先级对齐。
// env 仍优先(dev 覆盖用),无 env 时由 port_config.readWebPort() 走 HKCU → JSON → 18802。
const _envPort = parseInt(process.env.PRISIRAGENT_WEB_PORT || process.env.OIAGENT_WEB_PORT || "", 10);
let WEB_PORT = (_envPort >= 1 && _envPort <= 65535)
  ? _envPort
  : require("./port_config").readWebPort();
let WEB_URL = `http://${WEB_HOST}:${WEB_PORT}`;
const HOTKEY = process.env.PRISIRAGENT_SHELL_HOTKEY || process.env.OIAGENT_SHELL_HOTKEY || "CommandOrControl+Shift+O";
const PYTHON = process.env.PRISIRAGENT_PYTHON || process.env.OIAGENT_PYTHON || "python";
// 输入法悬浮栏 AI 按钮的 toggle 命名事件:Prisir TSF 插件 trigger_plugin("ai") SetEvent 同名事件。
// 壳在此监听,事件触发 = 把窗口置前(等价热键的「show」半支),让用户能从输入法一键唤起对话。
const AI_TOGGLE_EVENT = process.env.PRISIR_AI_TOGGLE_EVENT || "PrisirLingXi_AiToggle_Event";

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

// ---------- prisiragent_web 子进程看护 ----------
let webProc = null;
let webReady = false;
// P2.5+21(2026-10-03):语伴 / 音乐 子进程(仅在用户托盘点击时按需 spawn,
// 不自启是因为 Tauri 壳模式才能接管这两路。Electron 壳里补全是为了 dev 体验)。
let companionProc = null;
let musicProc = null;
// 同源 window.open 去重:同一 URL 5s 内只 allow 一次,阻死循环。
const _recentlyOpenedUrls = new Map();
const _RECENT_MS = 5000;

function webUp(host, port, cb) {
  const req = http.get({ host, port, path: "/", timeout: 1500 }, (res) => {
    res.resume();
    cb(true);
  });
  req.on("error", () => cb(false));
  req.on("timeout", () => { req.destroy(); cb(false); });
}

// 端口轮询(用户点子窗后端未起时的探活)。返 Promise<boolean>。
async function waitForPort(host, port, timeoutSec) {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => webUp(host, port, resolve));
    if (ok) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

// setWindowOpenHandler 的纯函数决策:让测试能 import 该函数。
//   - 同 URL 5s 内二次 open → deny(死循环防抖)
//   - 同源但不同 URL → allow
//   - 外链 → "external"(由 Electron 侧转 shell.openExternal)
function _decideSameOriginOpen(rawUrl, webUrl, recent) {
  const isSameOrigin = rawUrl === webUrl || rawUrl.startsWith(webUrl + "/");
  if (!isSameOrigin) return { action: "external" };
  // recent 既支持 Map(主流程)又支持 plain {url: ts} 对象(测试)。
  const last = (typeof recent.get === "function")
    ? (recent.get(rawUrl) || 0)
    : (recent[rawUrl] || 0);
  if (Date.now() - last < _RECENT_MS) return { action: "deny", reason: "debounce" };
  return { action: "allow" };
}

// 简化版 pipe-to-log(用于语伴/音乐后端:无 sentinel 解析,只需落日志)。
function _pipeProcToLog(proc, label) {
  try {
    const outFd = fs.openSync(LOG_STDOUT, "a");
    const errFd = fs.openSync(LOG_STDERR, "a");
    proc.stdout.on("data", (chunk) => { try { fs.writeSync(outFd, `[${label}] ${chunk}`); } catch (_) {} });
    proc.stderr.on("data", (chunk) => { try { fs.writeSync(errFd, `[${label}] ${chunk}`); } catch (_) {} });
    proc.on("close", () => { try { fs.closeSync(outFd); fs.closeSync(errFd); } catch (_) {} });
  } catch (e) {
    logWarn(label, "pipe-to-log failed", `err=${e.message}`);
  }
}

function startCompanion() {
  if (companionProc) return;
  const port = require("./port_config").readCompanionPort();
  webUp(WEB_HOST, port, (up) => {
    if (up) { logInfo("startCompanion", "port already up, reusing", `port=${port}`); return; }
    const script = path.join(REPO_ROOT, "companion", "prisIragent-companion-web.py");
    const args = [script, "--port", String(port)];
    logInfo("startCompanion", "spawning", `cmd=python args=${JSON.stringify(args)}`);
    try {
      companionProc = spawn(PYTHON, args, {
        cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
      });
    } catch (e) {
      logError("startCompanion", "spawn failed", `err=${e.message}`);
      companionProc = null; return;
    }
    _pipeProcToLog(companionProc, "companion");
    companionProc.on("spawn", () => logInfo("companionProc", "spawned", `pid=${companionProc.pid}`));
    companionProc.on("exit", (code, signal) => {
      logWarn("companionProc", "exited", `code=${code} signal=${signal} pid=${companionProc && companionProc.pid}`);
      companionProc = null;
    });
    companionProc.on("error", (err) => logError("companionProc", "error event", `err=${err.message}`));
  });
}

function startMusic() {
  if (musicProc) return;
  const port = require("./port_config").readMusicPort();
  webUp(WEB_HOST, port, (up) => {
    if (up) { logInfo("startMusic", "port already up, reusing", `port=${port}`); return; }
    const script = path.join(REPO_ROOT, "companion", "prisIragent-music-web.py");
    const args = [script, "--port", String(port)];
    logInfo("startMusic", "spawning", `cmd=python args=${JSON.stringify(args)}`);
    try {
      musicProc = spawn(PYTHON, args, {
        cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
      });
    } catch (e) {
      logError("startMusic", "spawn failed", `err=${e.message}`);
      musicProc = null; return;
    }
    _pipeProcToLog(musicProc, "music");
    musicProc.on("spawn", () => logInfo("musicProc", "spawned", `pid=${musicProc.pid}`));
    musicProc.on("exit", (code, signal) => {
      logWarn("musicProc", "exited", `code=${code} signal=${signal} pid=${musicProc && musicProc.pid}`);
      musicProc = null;
    });
    musicProc.on("error", (err) => logError("musicProc", "error event", `err=${err.message}`));
  });
}

function startWeb() {
  if (webProc) return;
  // 已被别的进程占用端口就直接复用,不重复起。
  webUp(WEB_HOST, WEB_PORT, (up) => {
    if (up) {
      logInfo("startWeb", "port already up, reusing", `port=${WEB_PORT}`);
      webReady = true; loadWhenReady(); return;   // 关键:复用已起后端也要触发加载
    }
    // 发布态:优先 spawn 打包好的 PrisirAI.exe(用户免装 Python);
    // 开发态:exe 不存在则回退 python prisiragent_web.py。
    const coreExe = resolveCoreExe();
    const useExe = fs.existsSync(coreExe);
    const cmd = useExe ? coreExe : PYTHON;
    // --lan:遥控模式开箱即用。装包用户只能从桌面图标启动、没有「开启遥控」按钮,
    // 若不带 --lan,手机遥控页永远显示「未开启」且无开启途径=死功能(用户实测反馈)。
    // 安全由令牌门禁兜底:--lan 下非回环来源必须持持久配对令牌否则 401,配对码出示在 PC 屏
    // 由人抄进手机,公网来源连 offer 都拦。本地对话主链行为不变(回环不带令牌)。
    // 可用 PRISIRAGENT_SHELL_NO_LAN=1 显式关回默认 127.0.0.1。
    // 兼容旧名 OIAGENT_SHELL_NO_LAN。
    const wantLan = !(process.env.PRISIRAGENT_SHELL_NO_LAN || process.env.OIAGENT_SHELL_NO_LAN);
    const lanArgs = wantLan ? ["--lan"] : [];
    // calendar 端口从 port_config 读(HKCU / JSON / yaml default 18803)。
    // 修 2026-10-03 ship 漏:之前写死 DEFAULT_CALENDAR_PORT 常量但未声明,
    // 启动抛 ReferenceError,后端没起来,18802 不监听。
    const calendarPort = require("./port_config").readCalendarPort();
    const args = useExe
      ? ["--port", String(WEB_PORT), "--calendar-port", String(calendarPort), ...lanArgs]
      : [WEB_SCRIPT, "--port", String(WEB_PORT), "--calendar-port", String(calendarPort), ...lanArgs];
    // v2.0:stdout/stderr 落 spawn-{out,err}.log(原本 stdio: "ignore" 用户看不到任何错)。
    // Windows spawn 只接受文件路径 / 'pipe' / 'ignore',不接受 WriteStream 对象。
    // 用 'pipe' + 自己写文件:跨平台稳,且日志可加锁/轮转。
    logInfo("startWeb", "spawning backend", `cmd=${cmd} args=${JSON.stringify(args)} cwd=${REPO_ROOT} useExe=${useExe}`);
    try {
      webProc = spawn(cmd, args, {
        cwd: REPO_ROOT,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (e) {
      logError("startWeb", "spawn failed", `err=${e.message}`);
      return;
    }
    // 手动把 stdout/stderr 流接进 spawn-*.log(append 模式,fs.WriteStream 跨平台稳)。
    // P2.5+11:同时行级扫描 `[prisIragent_web] PRISIR_WEB_READY port=<n>` sentinel(Python 端 line 11455)
    // 拿到真实端口(端口冲突 fallback 时与请求端口不同),立即改 WEB_PORT + 触发 loadWhenReady(),
    // 避免 30s 轮询超时 + 窗口卡「正在唤醒」。
    try {
      const outFd = fs.openSync(LOG_STDOUT, "a");
      const errFd = fs.openSync(LOG_STDERR, "a");
      let _stdoutBuf = "";
      const SENTINEL_RE = /\[prisIragent_web\] PRISIR_WEB_READY port=(\d+)/;
      webProc.stdout.on("data", (chunk) => {
        try { fs.writeSync(outFd, chunk); } catch (_) {}
        _stdoutBuf += chunk.toString("utf8");
        let nl;
        while ((nl = _stdoutBuf.indexOf("\n")) >= 0) {
          const line = _stdoutBuf.slice(0, nl).trim();
          _stdoutBuf = _stdoutBuf.slice(nl + 1);
          const m = line.match(SENTINEL_RE);
          if (m) {
            const realPort = parseInt(m[1], 10);
            if (realPort >= 1 && realPort <= 65535 && realPort !== WEB_PORT) {
              logInfo("startWeb", "sentinel port changed", `expected=${WEB_PORT} real=${realPort}`);
              WEB_PORT = realPort;
              WEB_URL = `http://${WEB_HOST}:${WEB_PORT}`;
            }
            if (!webReady) {
              webReady = true;
              logInfo("startWeb", "sentinel ready", `port=${WEB_PORT}`);
              loadWhenReady();
            }
          }
        }
      });
      webProc.stderr.on("data", (chunk) => { try { fs.writeSync(errFd, chunk); } catch (_) {} });
      webProc.on("close", () => { try { fs.closeSync(outFd); fs.closeSync(errFd); } catch (_) {} });
    } catch (e) {
      logWarn("startWeb", "stdout/stderr redirect failed", `err=${e.message}`);
    }
    webProc.on("spawn", () => {
      logInfo("webProc", "spawned", `pid=${webProc.pid}`);
    });
    webProc.on("exit", (code, signal) => {
      logWarn("webProc", "exited", `code=${code} signal=${signal} pid=${webProc && webProc.pid}`);
      webProc = null; webReady = false;
      // 退出后不要立即重启,避免循环;留给用户再次触发或托盘菜单"重启对话"
    });
    webProc.on("error", (err) => {
      logError("webProc", "error event", `err=${err.message}`);
    });
    // 轮询等就绪
    const t = setInterval(() => {
      webUp(WEB_HOST, WEB_PORT, (up) => {
        if (up) {
          webReady = true;
          clearInterval(t);
          logInfo("startWeb", "backend ready", `port=${WEB_PORT}`);
          loadWhenReady();
        }
      });
    }, 400);
    setTimeout(() => {
      clearInterval(t);
      if (!webReady) {
        logError("startWeb", "backend not ready within 30s", `cmd=${cmd}`);
      }
    }, 30000);
  });
}

// ---------- 窗口 ----------
let win = null;
let tray = null;
let quitting = false;

// P2.5+16(2026-09-22):4 子窗口(语伴/音乐/📅日历/🔀工作流)独立 BrowserWindow。
// 设计:每个 label 一个 BrowserWindow 实例,close 仅 hide(常驻后台),下次同 label
//      show 时秒开(不重建);各自独立 webPreferences 沙箱红线 + 独立 IPC handler。
//      子窗口尺寸比主窗口小(语伴 920×680 / 音乐 880×620 / 日历 960×720 / 工作流 1000×720)。
const childWindows = new Map();   // label → BrowserWindow

function _commonWebPreferences() {
  return {
    preload: path.join(__dirname, "preload.js"),
    contextIsolation: true,        // 红线:渲染层拿不到 Node
    nodeIntegration: false,
    sandbox: true,
  };
}

function _createChildWindow(spec) {
  const existing = childWindows.get(spec.label);
  if (existing && !existing.isDestroyed()) {
    logInfo("existing", "reuse child window", `label=${spec.label}`);
    existing.show();
    existing.focus();
    return existing;
  }
  const w = new BrowserWindow({
    width: spec.width || 920,
    height: spec.height || 680,
    minWidth: spec.minWidth || 640,
    minHeight: spec.minHeight || 480,
    title: spec.title || "Prisir(湃睿思) AI",
    icon: path.join(__dirname, "icon.png"),
    backgroundColor: "#f6f1e7",
    show: false,                    // ready-to-show 再亮相
    autoHideMenuBar: true,
    webPreferences: _commonWebPreferences(),
  });
  // ready-to-show 触发再 show,避免白闪
  w.once("ready-to-show", () => { if (w && !w.isDestroyed()) { w.show(); w.focus(); } });
  // 兜底 3.5s 强制亮相(后端慢 / 端口冲突 fallback 场景)
  setTimeout(() => { if (w && !w.isDestroyed() && !w.isVisible()) { w.show(); w.focus(); } }, 3500);
  // 外链交系统浏览器,壳内子窗口不复用主窗口 setWindowOpenHandler(子窗独立配置)
  // P2.5+21(2026-10-03):同源 window.open 用 _decideSameOriginOpen 纯函数决策,
  // 5s 内同 URL 只 allow 1 次,避免「关于 / 隐私 / 远程」同源 open 触发多窗递归。
  w.webContents.setWindowOpenHandler(({ url }) => {
    const dec = _decideSameOriginOpen(url, WEB_URL, _recentlyOpenedUrls);
    if (dec.action === "deny") {
      logWarn("setWindowOpenHandler", "denied", `reason=${dec.reason || "same-origin"} url=${url}`);
      return { action: "deny" };
    }
    if (dec.action === "external") {
      shell.openExternal(url);
      return { action: "deny" };
    }
    // dec.action === "allow"
    _recentlyOpenedUrls.set(url, Date.now());
    return { action: "allow", overrideBrowserWindowOptions: {
      autoHideMenuBar: true,
      backgroundColor: "#f6f1e7",
      webPreferences: _commonWebPreferences(),
    }};
  });
  // close 仅 hide(常驻),quit 时才真销毁
  w.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      w.hide();
    }
  });
  w.on("closed", () => {
    childWindows.delete(spec.label);
  });
  // 首次创建 → 加载 URL(loadURL 必须发生在 close handler 注册后,否则 ready-to-show 漏)
  logInfo("childWindow", "create", `label=${spec.label} url=${spec.url}`);
  w.loadURL(spec.url);
  childWindows.set(spec.label, w);
  return w;
}

function closeAllChildWindows() {
  for (const w of childWindows.values()) {
    if (w && !w.isDestroyed()) {
      try { w.hide(); } catch (_) {}
    }
  }
}

function destroyAllChildWindows() {
  for (const w of childWindows.values()) {
    if (w && !w.isDestroyed()) {
      try { w.destroy(); } catch (_) {}
    }
  }
  childWindows.clear();
}

function createWindow() {
  win = new BrowserWindow({
    width: 1040,
    height: 760,
    minWidth: 720,
    minHeight: 520,
    title: "Prisir(湃睿思) AI",
    icon: path.join(__dirname, "icon.png"),
    backgroundColor: "#f6f1e7",   // 国画纸色,与聊天 UI 一致,避免白闪
    show: false,                   // 先藏,ready-to-show 再亮相,避免加载期白闪
    autoHideMenuBar: true,         // 隐藏顶部菜单栏(File/Edit/View…),对话壳不需要
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,      // 红线:渲染层拿不到 Node
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // 首屏 ready 才显示(防白闪);ready-to-show 只在首轮加载触发。
  win.once("ready-to-show", () => { if (win) { win.show(); win.focus(); } });
  // 兜底:远程 loadURL 若因故迟迟不 ready(后端慢/复用端口),3.5s 后强制亮相,
  // 否则窗口会卡在隐藏态只剩托盘图标,用户得手动右键才能看到(本次修的 bug)。
  setTimeout(() => { if (win && !win.isVisible()) { win.show(); win.focus(); } }, 3500);

  // 外链一律交给系统浏览器,壳内不导航出回环。
  // 同源(回环)window.open 弹出的新窗口(手机遥控/关于/隐私等)也要隐藏菜单栏 + 先藏后亮,
  // 否则这些子窗口仍带 File/Edit/View 菜单且可能白闪(用户实测反馈)。
  win.webContents.setWindowOpenHandler(({ url }) => {
    // P2.5+21(2026-10-03):同源递归去重(主窗版) — 跟子窗共用 _decideSameOriginOpen。
    const dec = _decideSameOriginOpen(url, WEB_URL, _recentlyOpenedUrls);
    if (dec.action === "deny") {
      logWarn("setWindowOpenHandler", "denied", `reason=${dec.reason || "same-origin"} url=${url}`);
      return { action: "deny" };
    }
    if (dec.action === "external") {
      shell.openExternal(url);
      return { action: "deny" };
    }
    _recentlyOpenedUrls.set(url, Date.now());
    return { action: "allow", overrideBrowserWindowOptions: {
      autoHideMenuBar: true,
      // 注意:子窗不设 show:false。window.open 的子窗不经 createWindow,拿不到句柄挂
      // ready-to-show,也没有 3.5s 兜底——设了 show:false 会永远 hidden(用户点"手机遥控"/
      // "关于"没反应的真根因)。backgroundColor 已设,白闪可忽略,直接默认 show。
      backgroundColor: "#f6f1e7",
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    }};
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
          `<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#f6f1e7;color:#5b5548;font-family:system-ui"><div>正在唤醒 Prisir(湃睿思) AI…</div></body>`
        )
    );
    // 保险:若 webReady 在我们加载过渡页之后才变 true(端口复用路径下 startWeb
    // 回调可能早于 createWindow 完成、没人再触发 load),这里兜底每 500ms 复查一次。
    const retry = setInterval(() => {
      if (!win) { clearInterval(retry); return; }
      if (webReady) { clearInterval(retry); win.loadURL(WEB_URL); }
    }, 500);
    setTimeout(() => clearInterval(retry), 30000);
  }
}

function toggleWindow() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) win.hide();
  else { win.show(); win.focus(); loadWhenReady(); }
}

// ---------- 输入法 AI 按钮唤起:监听 PrisirLingXi_AiToggle_Event ----------
// 与语音插件同构(lingxi_app 的 _voice_listener):TSF 点 AI 按钮 → OpenEvent+SetEvent 同名事件;
// 这里 WaitOne 阻塞等待,触发即把窗口置前(show+focus,不做 hide —— 用户点 AI 是想对话不是隐藏)。
// 用隐藏 PowerShell 子进程承载 WaitOne,避免引入 node 原生模块(node-addon-api 编译链)。
// 事件只在 Windows 存在;非 Windows 直接跳过。
let aiListenerProc = null;
function bringToFront() {
  if (!win) return createWindow();
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  loadWhenReady();
}
function startAiToggleListener() {
  if (process.platform !== "win32") return;
  // PowerShell:建/开命名事件 → 循环 WaitOne,每次触发打印一行标记。
  // 手动重置(ManualResetEvent $false)防一次性;stderr 静默,异常退出后由壳侧重启。
  const ps = [
    "$n='" + AI_TOGGLE_EVENT + "';",
    "$e=New-Object System.Threading.EventWaitHandle($false,[System.Threading.EventResetMode]::AutoReset,$n);",
    "while($true){ $e.WaitOne() | Out-Null; Write-Output 'AI_TOGGLE' ; [Console]::Out.Flush() }",
  ].join(" ");
  try {
    aiListenerProc = spawn("powershell.exe", ["-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], {
      windowsHide: true, stdio: ["ignore", "pipe", "ignore"],
    });
  } catch (e) { logError("aiToggle", "spawn fail", `err=${e.message}`); return; }
  let buf = "";
  aiListenerProc.stdout.on("data", (d) => {
    buf += d.toString("utf8");
    let idx;
    while ((idx = buf.indexOf("AI_TOGGLE")) >= 0) {
      buf = buf.slice(idx + "AI_TOGGLE".length);
      logInfo("aiToggle", "event -> bringToFront");
      bringToFront();
    }
  });
  // 监听进程异常死掉则 3s 后重启(保持唤起通道常开);壳退出时不再重启。
  aiListenerProc.on("exit", (code) => {
    aiListenerProc = null;
    if (!quitting) { logWarn("aiToggle", "listener exit, respawn", `code=${code}`); setTimeout(startAiToggleListener, 3000); }
  });
  logInfo("aiToggle", "listener started", `event=${AI_TOGGLE_EVENT} pid=${aiListenerProc.pid}`);
}

// ---------- v2.0 开发者模式 ----------
// 检测 $INSTDIR/dev/git-portable/ 是否存在(装包器开发者模式节产物)。
// 检测是用户已主动勾选安装 git-portable + repo.zip + DEV_README.txt。
// 红线:开发者模式菜单只在检测到 dev 资源时才显示,普通用户看不到。
// __dirname 在装包后是 $INSTDIR\prisiragent-shell\,所以 $INSTDIR = path.resolve(__dirname, "..")。
// 注意:PARENT_DIR 在不同环境含义不同(装包后 = $INSTDIR,开发态 = oi_enhancements/),
// 这里我们只关心装包后路径,直接用 __dirname 解析。
const INSTDIR = path.resolve(__dirname, "..");
const DEV_GIT_PORTABLE = path.join(INSTDIR, "dev", "git-portable");
const DEV_REPO_ZIP = path.join(INSTDIR, "dev", "repo.zip");
const DEV_README = path.join(INSTDIR, "dev", "DEV_README.txt");
function devModeAvailable() {
  // 三件都存在才算「开发者模式就绪」(否则菜单点了也是空跑)。
  return fs.existsSync(DEV_GIT_PORTABLE) && fs.existsSync(DEV_REPO_ZIP);
}
function openDeveloperTerminal() {
  // 用 git-portable.cmd shim(已在 PATH 注入),给开发者一个立即可用的 git 命令行。
  // 找不到 shim 时退到 bash.exe(用户也可手动配 PATH)。
  const shim = path.join(DEV_GIT_PORTABLE, "git-portable.cmd");
  if (!fs.existsSync(shim)) {
    logWarn("devTerminal", "shim not found", `path=${shim}`);
    return;
  }
  try {
    spawn("cmd.exe", ["/c", "start", "", shim], {
      detached: true, stdio: "ignore", windowsHide: false,
    }).unref();
    logInfo("devTerminal", "spawned", `shim=${shim}`);
  } catch (e) {
    logError("devTerminal", "spawn failed", `err=${e.message}`);
  }
}
function openDevReadme() {
  // 写完打包 + 设置默认应用关联的 PDF/RTF;最稳是用系统应用打开 .txt。
  try {
    shell.openPath(DEV_README);
    logInfo("devReadme", "opened", `path=${DEV_README}`);
  } catch (e) {
    logError("devReadme", "open failed", `err=${e.message}`);
  }
}

// ---------- 托盘 ----------
// P2.5+13(2026-09-22):语伴/音乐/📅 打开日历 3 个菜单项。
// 设计:P2.5+16(2026-09-22)起,4 子窗口(语伴/音乐/📅日历/🔀工作流)独立 BrowserWindow,
// 不再复用主窗口 loadURL。每次 tray click 各自 show/focus;close 仅 hide 不销毁,
// 下次同 label 秒开。窗口尺寸/标题按各自场景定制。
// 「主面板」标签仍走主窗口 win(不另建独立 BrowserWindow)。
function openInShell(url, label) {
  // 主面板路径(label="main"或空):复用主窗口
  if (!label || label === "main") {
    if (!win) createWindow();
    if (!win) return;
    logInfo("trayOpen", "open main in shell", `url=${url}`);
    win.show();
    win.focus();
    win.loadURL(url);
    return;
  }
  // 子窗口:走 _createChildWindow helper
  _createChildWindow({ label, url, ..._CHILD_SPEC[label] });
}
const _CHILD_SPEC = {
  companion: { width: 920, height: 680, minWidth: 640, minHeight: 480, title: "PrisirAI · 语伴" },
  music:     { width: 880, height: 620, minWidth: 640, minHeight: 480, title: "PrisirAI · 音乐" },
  calendar:  { width: 960, height: 720, minWidth: 720, minHeight: 540, title: "PrisirAI · 📅 日程" },
  workflow:  { width: 1000, height: 720, minWidth: 800, minHeight: 560, title: "PrisirAI · 🔀 工作流" },
};

function openCompanionWindow() {
  // P2.5+21(2026-10-03):Electron 壳现在自己 spawn 语伴后端。点托盘后先启后端,
  // 探活 ≤3s,起来再弹子窗;起不来兜底主 web。
  startCompanion();
  const port = require("./port_config").readCompanionPort();
  waitForPort(WEB_HOST, port, 3.0).then((ok) => {
    if (!ok) {
      logWarn("openCompanionWindow", "port not ready in 3s", `port=${port}`);
      openInShell(WEB_URL, "main");
      return;
    }
    openInShell(`http://${WEB_HOST}:${port}/`, "companion");
  });
}
function openMusicWindow() {
  // P2.5+21(2026-10-03):Electron 壳自己 spawn music 后端(端口动态分配)。
  // music web 起来后 `--port 0` 时会写 HKCU / _prisir_registry/music_port.json,
  // port_config.js 的 readMusicPort() 读动态端口;我们 spawn 时不预知,先起来
  // 等 3s 后读端口再弹子窗。
  startMusic();
  const deadline = Date.now() + 3000;
  const tick = () => {
    const port = require("./port_config").readMusicPort();
    if (port > 0) {
      // 端口有值后再探活 1 次,确保 music web 真 ready
      waitForPort(WEB_HOST, port, 1.0).then((ok) => {
        if (ok) {
          openInShell(`http://${WEB_HOST}:${port}/`, "music");
        } else if (Date.now() < deadline) {
          setTimeout(tick, 300);
        } else {
          logWarn("openMusicWindow", "music web not ready in 3s", `port=${port}`);
          openInShell(WEB_URL, "main");
        }
      });
      return;
    }
    if (Date.now() < deadline) {
      setTimeout(tick, 300);
    } else {
      logWarn("openMusicWindow", "music port 0 after 3s", `port=${port}`);
      openInShell(WEB_URL, "main");
    }
  };
  tick();
}
function openCalendarWindow() {
  // 日历 走 prisiragent_web.py 的 /prisiragent/calendar 路由。
  // P2.5+14 起日历独立端口(同进程双端口 listen),从 port_config 读。
  const port = require("./port_config").readCalendarPort();
  openInShell(`http://${WEB_HOST}:${port}/prisiragent/calendar`, "calendar");
}
// P2.5+16:工作流窗口 = 主 web 端口 + /prisiragent/#wfmodal 路由锚点(URL fragment 触发 wfmodal 全屏)。
// 工作流本身是 web 端 wfmodal 组件,不需要新后端,独立 BrowserWindow 让用户能从托盘直开。
function openWorkflowWindow() {
  openInShell(`${WEB_URL}#wfmodal`, "workflow");
}

function createTray() {
  // 用国画风 mark 若存在,否则空图标(Electron 需要有效 image)。
  // 对话壳专属图标:dialog_flame(铜环 + teal 灵机火焰),与浏览器母标圆规分开。
  const iconPath = path.join(__dirname, "icon.png");
  let img = nativeImage.createFromPath(iconPath);
  if (img.isEmpty()) img = nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip("Prisir(湃睿思) AI");
  // P2.5+17(2026-09-22):托盘子菜单 3 组 — 主控 / 多窗口 / 系统。
  // 设计原则:每组一个 submenu,submenu 内项目平铺;开发者模式(若已安装)独立顶层菜单项,
  // 不塞进任何 submenu,保持普通用户托盘菜单简洁。
  const mainCtrlSubmenu = [
    { label: "打开 PrisirAI", click: () => { if (win) { win.show(); loadWhenReady(); } else createWindow(); } },
    { label: "隐藏 PrisirAI", click: () => { if (win) { win.hide(); } } },
  ];
  const multiWindowSubmenu = [
    // P2.5+16(2026-09-22):每个子项独立 BrowserWindow,不再复用主窗口。
    { label: "语伴",      click: openCompanionWindow },
    { label: "音乐",      click: openMusicWindow },
    { label: "📅 日程",   click: openCalendarWindow },
    { label: "🔀 工作流", click: openWorkflowWindow },
    { type: "separator" },
    { label: "关闭所有子窗口", click: () => closeAllChildWindows() },
  ];
  const systemSubmenu = [
    { label: "开机自启", type: "checkbox", checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }) },
    { type: "separator" },
    { label: "退出", click: () => { quitting = true; app.quit(); } },
  ];
  const trayItems = [
    { label: "主控",   submenu: mainCtrlSubmenu },
    { label: "多窗口", submenu: multiWindowSubmenu },
    { label: "系统",   submenu: systemSubmenu },
  ];
  // 开发者模式(若安装)独立顶层菜单项,不进任何 submenu。
  if (devModeAvailable()) {
    trayItems.push({ type: "separator" });
    trayItems.push({ label: "开发者模式", submenu: [
      { label: "打开开发者终端 (git-portable)", click: openDeveloperTerminal },
      { label: "查看开发者说明", click: openDevReadme },
    ]});
  }
  tray.setContextMenu(Menu.buildFromTemplate(trayItems));
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

// v2.0 反馈卡:白名单 URL 走 shell.openExternal(系统浏览器)。
// 只允许 https:// 且 babelspan.com 子域或主页。防止渲染层被 XSS 诱导打开恶意 URL。
ipcMain.handle("shell:openExternal", (_e, url) => {
  try {
    const u = String(url || "");
    if (!/^https:\/\//i.test(u)) return { ok: false, error: "https-only" };
    const host = new URL(u).hostname.toLowerCase();
    if (host !== "bbs.babelspan.com" && host !== "babelspan.com") {
      return { ok: false, error: "host not in babelspan.com allowlist" };
    }
    shell.openExternal(u);
    logInfo("shell:openExternal", "opened", `url=${u}`);
    return { ok: true };
  } catch (e) {
    logError("shell:openExternal", "err", `e=${e.message}`);
    return { ok: false, error: e.message };
  }
});

// ---------- #50 品牌化应用通知(契约 2026-08-21 §C,壳侧) ----------
// 与扩展同逻辑:每日轮询 babelspan.com 公开更新清单 JSON,新条目弹 Electron Notification。
// 红线:L3 只读自治(只推只读更新、点击只开页);无 key(公开静态 JSON);
// 容错静默(404/非JSON/断网一律当无更新);防轰炸(去重 + 一次最多3条 + 每日一次)。
// 隐私:只向外 GET babelspan.com,不上报任何数据;seen 只存 item id(落盘于 userData)。
// P2.5+15(2026-09-22):配置走 prisIrai_config.yaml(三端对齐),找不到 yaml 用内置默认。
const _config = require("./config_loader");
const BRAND_UPDATES_URL = _config.brandUrl();
const BRAND_MAX_PER_RUN = _config.brandMaxPerRun();
const BRAND_SEEN_CAP = _config.brandSeenCap();
const BRAND_INTERVAL_MS = _config.brandIntervalMs();

function _brandSeenPath() {
  return path.join(app.getPath("userData"), "brand-notify-seen.json");
}
function _brandLoadSeen() {
  try {
    const a = JSON.parse(fs.readFileSync(_brandSeenPath(), "utf-8"));
    return Array.isArray(a) ? a : [];
  } catch { return []; }
}
function _brandSaveSeen(arr) {
  try { fs.writeFileSync(_brandSeenPath(), JSON.stringify(arr)); } catch {}
}

// 容错静默:任何失败都返回 [],绝不抛、绝不弹错误通知。
async function _brandFetchUpdates() {
  try {
    const r = await fetch(BRAND_UPDATES_URL, { cache: "no-store" });
    if (!r.ok) return [];
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    if (ct && ct.indexOf("json") < 0) return [];
    let data;
    try { data = await r.json(); } catch { return []; }
    const items = data && Array.isArray(data.items) ? data.items : [];
    return items.filter((it) => it && typeof it === "object"
      && typeof it.id === "string" && it.id.trim()
      && typeof it.title === "string" && it.title.trim());
  } catch { return []; }
}

async function checkBrandUpdates() {
  try {
    if (!Notification.isSupported()) return;
    const items = await _brandFetchUpdates();
    if (!items.length) return;
    const seen = _brandLoadSeen();
    const seenSet = new Set(seen);
    const fresh = items.filter((it) => !seenSet.has(it.id)).slice(0, BRAND_MAX_PER_RUN);
    if (!fresh.length) return;
    const iconPath = path.join(__dirname, "icon.png");
    let icon = nativeImage.createFromPath(iconPath);
    if (icon.isEmpty()) icon = undefined;
    for (const it of fresh) {
      try {
        const url = (typeof it.url === "string" && /^https:\/\//.test(it.url))
          ? it.url : "https://www.babelspan.com/";
        const n = new Notification({
          title: "Prisir · " + String(it.title).slice(0, 80), // 品牌化前缀
          body: String(it.body || "").slice(0, 200),
          icon: icon,
        });
        // 点击只开页(shell.openExternal 交给系统浏览器),不做任何写操作。
        n.on("click", () => { try { shell.openExternal(url); } catch {} });
        n.show();
        seen.push(it.id); // 只记真正弹过的
      } catch { /* 单条失败不拖垮整批 */ }
    }
    while (seen.length > BRAND_SEEN_CAP) seen.shift();
    _brandSaveSeen(seen);
  } catch { /* 顶层兜底:绝不崩主进程 */ }
}

function startBrandNotify() {
  // 用户可关(评审 minor):userData 下放一个 brand-notify-disabled 标志文件即停用,优先级高于一切。
  try {
    if (fs.existsSync(path.join(app.getPath("userData"), "brand-notify-disabled"))) return;
  } catch {}
  checkBrandUpdates(); // 启动即首查
  setInterval(checkBrandUpdates, BRAND_INTERVAL_MS); // 之后每日
}

// ---------- 生命周期 ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { win.show(); win.focus(); loadWhenReady(); } });

  app.whenReady().then(() => {
    // Windows toast 品牌化(评审 nit):不设 AppUserModelId 时通知归到通用 Electron app id。
    try { app.setAppUserModelId("com.prisir.prisiragent-shell"); } catch {}
    logInfo("app", "whenReady, starting web + window + tray");
    startWeb();
    createWindow();
    createTray();
    globalShortcut.register(HOTKEY, toggleWindow);
    startAiToggleListener(); // 输入法悬浮栏 AI 按钮唤起通道
    startBrandNotify(); // #50 品牌化应用通知(每日轮询,容错静默)
    app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });

  app.on("before-quit", () => {
    quitting = true; destroyAllChildWindows();
    // P2.5+21(2026-10-03):杀语伴 / 音乐 子进程,避免残留占用端口。
    if (companionProc) { try { companionProc.kill(); } catch (_) {} companionProc = null; }
    if (musicProc) { try { musicProc.kill(); } catch (_) {} musicProc = null; }
    logInfo("app", "before-quit");
  });
  app.on("will-quit", () => {
    logInfo("app", "will-quit");
    globalShortcut.unregisterAll();
    if (aiListenerProc) { try { aiListenerProc.kill(); } catch {} aiListenerProc = null; }
    killBackend();
  });

  // 所有窗口关上不退(托盘常驻),macOS 惯例;Windows 也一样常驻托盘。
  app.on("window-all-closed", () => { logInfo("app", "window-all-closed (stay in tray)"); });
}

// 退出时把后端清干净:webProc.kill() 只杀直接 spawn 的进程,杀不掉它再起的孙进程,
// 且「复用端口」路径下 webProc=null 根本不杀——残留后端占着 18802,下次启动误「复用」旧版。
// 故除 kill 直接子进程外,再按命令行特征兜底清残留 prisiragent_web/PrisirAI 后端进程。
function killBackend() {
  if (webProc) { try { webProc.kill(); } catch {} webProc = null; }
  try {
    // 清自己 workdir 下起的 prisiragent_web/PrisirAI 后端(不动别人的/系统 python)。
    // 用 CIM 过滤命令行含 prisiragent_web 或 PrisirAI.exe --port 的进程。
    spawn("powershell", ["-NoProfile", "-Command",
      "Get-CimInstance Win32_Process | Where-Object { " +
      "($_.Name -match '^(python|PrisirAI)\\.exe$') -and " +
      "($_.CommandLine -match 'prisiragent_web|PrisirAI\\.exe.*--port') } | " +
      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    ], { detached: true, stdio: "ignore", windowsHide: true }).unref();
    logInfo("killBackend", "sweep issued");
  } catch (e) {
    logWarn("killBackend", "sweep failed", `err=${e.message}`);
  }
}
