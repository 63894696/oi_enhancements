// prisiragent-shell — prisiragent 本地对话壳(Electron)主进程。
//
// 定位(prisirwork-foundation-integration-design §5.1 / F7):
//   不启浏览器也能和 prisiragent 对话。主进程负责:
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
const WEB_SCRIPT = path.join(PARENT_DIR, "oiagent_web.py");
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
const WEB_PORT = parseInt(process.env.PRISIRAGENT_WEB_PORT || process.env.OIAGENT_WEB_PORT || "18802", 10);
const WEB_URL = `http://${WEB_HOST}:${WEB_PORT}`;
const HOTKEY = process.env.OIAGENT_SHELL_HOTKEY || "CommandOrControl+Shift+O";
const PYTHON = process.env.OIAGENT_PYTHON || "python";
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
    if (up) {
      logInfo("startWeb", "port already up, reusing", `port=${WEB_PORT}`);
      webReady = true; loadWhenReady(); return;   // 关键:复用已起后端也要触发加载
    }
    // 发布态:优先 spawn 打包好的 PrisirAI.exe(用户免装 Python);
    // 开发态:exe 不存在则回退 python oiagent_web.py。
    const coreExe = resolveCoreExe();
    const useExe = fs.existsSync(coreExe);
    const cmd = useExe ? coreExe : PYTHON;
    // --lan:遥控模式开箱即用。装包用户只能从桌面图标启动、没有「开启遥控」按钮,
    // 若不带 --lan,手机遥控页永远显示「未开启」且无开启途径=死功能(用户实测反馈)。
    // 安全由令牌门禁兜底:--lan 下非回环来源必须持持久配对令牌否则 401,配对码出示在 PC 屏
    // 由人抄进手机,公网来源连 offer 都拦。本地对话主链行为不变(回环不带令牌)。
    // 可用 OIAGENT_SHELL_NO_LAN=1 显式关回默认 127.0.0.1。
    const wantLan = !process.env.OIAGENT_SHELL_NO_LAN;
    const lanArgs = wantLan ? ["--lan"] : [];
    const args = useExe
      ? ["--port", String(WEB_PORT), ...lanArgs]
      : [WEB_SCRIPT, "--port", String(WEB_PORT), ...lanArgs];
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
    try {
      const outFd = fs.openSync(LOG_STDOUT, "a");
      const errFd = fs.openSync(LOG_STDERR, "a");
      webProc.stdout.on("data", (chunk) => { try { fs.writeSync(outFd, chunk); } catch (_) {} });
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
      webUp((up) => {
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
    if (url.startsWith(WEB_URL)) return { action: "allow", overrideBrowserWindowOptions: {
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
function createTray() {
  // 用国画风 mark 若存在,否则空图标(Electron 需要有效 image)。
  // 对话壳专属图标:dialog_flame(铜环 + teal 灵机火焰),与浏览器母标圆规分开。
  const iconPath = path.join(__dirname, "icon.png");
  let img = nativeImage.createFromPath(iconPath);
  if (img.isEmpty()) img = nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip("Prisir(湃睿思) AI");
  // 托盘菜单:开发者模式只在该模式安装后才出现(普通用户托盘菜单保持简洁)。
  const trayItems = [
    { label: "打开 PrisirAI", click: () => { if (win) { win.show(); loadWhenReady(); } else createWindow(); } },
    { label: "开机自启", type: "checkbox", checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }) },
  ];
  if (devModeAvailable()) {
    trayItems.push({ type: "separator" });
    trayItems.push({ label: "开发者模式", submenu: [
      { label: "打开开发者终端 (git-portable)", click: openDeveloperTerminal },
      { label: "查看开发者说明", click: openDevReadme },
    ]});
  }
  trayItems.push({ type: "separator" });
  trayItems.push({ label: "退出", click: () => { quitting = true; app.quit(); } });
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
const BRAND_UPDATES_URL = "https://www.babelspan.com/updates.json";
const BRAND_MAX_PER_RUN = 3;   // 一次最多 3 条,防轰炸
const BRAND_SEEN_CAP = 100;    // seen 只留最近 100 个 id
const BRAND_INTERVAL_MS = 24 * 60 * 60 * 1000; // 每日

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

  app.on("before-quit", () => { quitting = true; logInfo("app", "before-quit"); });
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
// 故除 kill 直接子进程外,再按命令行特征兜底清残留 oiagent_web/PrisirAI 后端进程。
function killBackend() {
  if (webProc) { try { webProc.kill(); } catch {} webProc = null; }
  try {
    // 清自己 workdir 下起的 oiagent_web/PrisirAI 后端(不动别人的/系统 python)。
    // 用 CIM 过滤命令行含 oiagent_web 或 PrisirAI.exe --port 的进程。
    spawn("powershell", ["-NoProfile", "-Command",
      "Get-CimInstance Win32_Process | Where-Object { " +
      "($_.Name -match '^(python|PrisirAI)\\.exe$') -and " +
      "($_.CommandLine -match 'oiagent_web|PrisirAI\\.exe.*--port') } | " +
      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    ], { detached: true, stdio: "ignore", windowsHide: true }).unref();
    logInfo("killBackend", "sweep issued");
  } catch (e) {
    logWarn("killBackend", "sweep failed", `err=${e.message}`);
  }
}
