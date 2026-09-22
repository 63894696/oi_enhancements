// port_config.js — Electron 壳侧的端口配置(2026-09-19)
//
// 镜像 Python 端 [`companion/music/port_config.py`] + Rust 端
// [`prisIragent-tauri/src-tauri/src/port_config.rs`] 的存储契约:
//   1) HKCU\Software\PrisirAI\<name>_port  (DWORD,主通道,Win)
//   2) <userData>/_prisir_registry/ports.json  (跨平台 fallback)
//   3) 模块默认(代码内置)
//
// Rust/Python/Electron 三端字段名严格对齐:
//   web_port / companion_port / music_port  (HKCU 值名 + JSON key)
//   ports.json  (JSON 文件名)
//   _prisir_registry  (目录名)
//
// Electron 端只读(写入由 Python 端 notify_port_changed 负责)。
// 与既有 music web 流程一致:
//   - Python 端 prisiragent-music-web.py 启动后写 HKCU music_port
//   - Electron 壳 port_config.readMusicPort() 读取

"use strict";

const fs = require("fs");
const path = require("path");
const { app } = require("electron");

// 模块默认(必须与 Python 端 port_config.py DEFAULT_* 一致)
// P2.5+15(2026-09-22):优先读 prisIrai_config.yaml(若存在),字段对齐三端。
let DEFAULT_WEB_PORT = 18802;       // 主面板 prisiragent_web.py
let DEFAULT_COMPANION_PORT = 18850; // 语伴
let DEFAULT_MUSIC_PORT = 0;         // music web 动态分配
// 日历 走 prisiragent_web.py 的 /prisIragent/calendar 路由,故 calendar 端口 = web 端口。
// 留独立常量便于后续若 calendar 拆独立服务时切换。
// P2.5+14(2026-09-22):实际生产 calendar 端口独立 — 同进程双端口 listen,
// 但 JS 端 default 仍跟随 web(若 Python 端未启 --calendar-port 则 fallback)。
// P2.5+15(2026-09-22):默认 18803 来自 yaml ports.calendar(允许用户改 yaml 重定义)。
let DEFAULT_CALENDAR_PORT = 18803;
try {
  // 延迟 require:这个文件被 main.js 和测试都加载,config_loader.js 用 electron.app,
  // 若 app 还没 ready 时(测试场景)会抛,所以 try 包住,失败保持内置默认。
  const _cfg = require("./config_loader");
  if (typeof _cfg.webPortDefault === "function") {
    DEFAULT_WEB_PORT = _cfg.webPortDefault();
    DEFAULT_COMPANION_PORT = _cfg.companionPortDefault();
    DEFAULT_MUSIC_PORT = _cfg.musicPortDefault();
    DEFAULT_CALENDAR_PORT = _cfg.calendarPortDefault();
  }
} catch (_) { /* 装包后无 yaml / 测试态无 electron / 都走内置默认 */ }

// Windows 注册表路径
const REG_KEY_PATH = "Software\\PrisirAI";
const REG_VAL_SUFFIX = "_port";

// 跨平台 fallback JSON 文件路径候选
const _JSON_FILENAME = "ports.json";
const _JSON_DIRNAME = "_prisir_registry";

function jsonPathCandidates() {
  const out = [];
  try {
    const userData = app.getPath("userData");
    out.push(path.join(userData, _JSON_DIRNAME, _JSON_FILENAME));
  } catch (_) {
    // app 还没 ready 时(主进程模块加载阶段)会抛,降级到 cwd
  }
  out.push(path.join(process.cwd(), _JSON_DIRNAME, _JSON_FILENAME));
  return out;
}

function readJson(name) {
  for (const p of jsonPathCandidates()) {
    try {
      const s = fs.readFileSync(p, "utf8");
      const v = JSON.parse(s);
      if (v && typeof v === "object" && typeof v[name] === "number") {
        const n = v[name];
        if (n >= 1 && n <= 65535) return n;
      }
    } catch (_) { /* 文件不存在或解析失败,试下一个候选 */ }
  }
  return null;
}

function writeJson(name, port) {
  for (const p of jsonPathCandidates()) {
    try {
      const dir = path.dirname(p);
      fs.mkdirSync(dir, { recursive: true });
      let data = {};
      try { data = JSON.parse(fs.readFileSync(p, "utf8") || "{}"); } catch (_) {}
      if (typeof data !== "object" || data === null) data = {};
      data[name] = port;
      data[`${name}_updated_at_ms`] = Date.now();
      fs.writeFileSync(p, JSON.stringify(data, null, 2));
      return true;
    } catch (_) { /* 试下一个候选 */ }
  }
  return false;
}

/**
 * 读 HKCU 注册表。
 * 用 PowerShell 调 Reg Query(避免引入 native registry 模块,跨 Electron 版本稳)。
 */
function readWinreg(name) {
  if (process.platform !== "win32") return null;
  const valName = `${name.toLowerCase()}${REG_VAL_SUFFIX}`;
  try {
    const { execSync } = require("child_process");
    const out = execSync(
      `reg query "HKCU\\${REG_KEY_PATH}" /v "${valName}" /reg:64`,
      { encoding: "utf8", timeout: 1500, windowsHide: true, stdio: ["ignore", "pipe", "ignore"] }
    );
    // 输出形如:
    // HKEY_CURRENT_USER\Software\PrisirAI
    //     web_port    REG_DWORD    0x499b
    const m = out.match(/REG_DWORD\s+(0x[0-9a-fA-F]+|\d+)/);
    if (!m) return null;
    const n = parseInt(m[1], m[1].startsWith("0x") ? 16 : 10);
    if (n >= 1 && n <= 65535) return n;
    return null;
  } catch (_) {
    return null; // 注册表项不存在 / 权限不够 / 不在 Win
  }
}

/**
 * 旧 music_port 迁移(从 HKCU\Software\PrisirAI\music_port 一次性搬到 JSON)。
 * 与 Python 端 _migrate_legacy_music_port 同款行为,避免两端各自迁移竞态。
 */
function migrateLegacyMusicPort() {
  if (process.platform !== "win32") return;
  // 若 JSON 已标记迁移过,跳过
  for (const p of jsonPathCandidates()) {
    try {
      const v = JSON.parse(fs.readFileSync(p, "utf8"));
      if (v && v.__music_legacy_migrated__) return;
    } catch (_) { /* 继续试下一个候选 */ }
  }
  // 读旧 HKCU music_port
  const legacy = readWinregRaw("music_port");
  if (legacy !== null) {
    // 写到 JSON
    for (const p of jsonPathCandidates()) {
      try {
        const dir = path.dirname(p);
        fs.mkdirSync(dir, { recursive: true });
        let data = {};
        try { data = JSON.parse(fs.readFileSync(p, "utf8") || "{}"); } catch (_) {}
        if (typeof data !== "object" || data === null) data = {};
        if (!("music" in data)) data.music = legacy;
        data.__music_legacy_migrated__ = true;
        fs.writeFileSync(p, JSON.stringify(data, null, 2));
        break;
      } catch (_) {}
    }
  }
  // 标记已迁移(即便 legacy 没找到,也写标记免得每次都查注册表)
  writeJson("__music_legacy_migrated__", true);
}

/** 读 HKCU 任意 value name(给旧 music_port 迁移用) */
function readWinregRaw(valName) {
  if (process.platform !== "win32") return null;
  try {
    const { execSync } = require("child_process");
    const out = execSync(
      `reg query "HKCU\\${REG_KEY_PATH}" /v "${valName}" /reg:64`,
      { encoding: "utf8", timeout: 1500, windowsHide: true, stdio: ["ignore", "pipe", "ignore"] }
    );
    const m = out.match(/REG_DWORD\s+(0x[0-9a-fA-F]+|\d+)/);
    if (!m) return null;
    return parseInt(m[1], m[1].startsWith("0x") ? 16 : 10);
  } catch (_) { return null; }
}

/**
 * 读用户配置的端口。
 * 优先级:HKCU 注册表 → JSON fallback → 默认。
 *
 * @param {string} name "web" / "companion" / "music"
 * @param {number} defaultPort 模块默认
 * @returns {number}
 */
function readPort(name, defaultPort) {
  if (name === "music") {
    try { migrateLegacyMusicPort(); } catch (_) {}
  }
  const v = readWinreg(name);
  if (v !== null) return v;
  const j = readJson(name);
  if (j !== null) return j;
  return defaultPort;
}

const readWebPort = () => readPort("web", DEFAULT_WEB_PORT);
const readCompanionPort = () => readPort("companion", DEFAULT_COMPANION_PORT);
const readMusicPort = () => readPort("music", DEFAULT_MUSIC_PORT);
// 日历 = 复用 web 端口(HKCU/JSON 与 web 同源);若 JSON 显式存了 calendar 端口也允许覆盖。
const readCalendarPort = () => {
  const v = readWinreg("calendar");
  if (v !== null) return v;
  const j = readJson("calendar");
  if (j !== null) return j;
  return DEFAULT_CALENDAR_PORT;
};

module.exports = {
  DEFAULT_WEB_PORT,
  DEFAULT_COMPANION_PORT,
  DEFAULT_MUSIC_PORT,
  DEFAULT_CALENDAR_PORT,
  readPort,
  readWebPort,
  readCompanionPort,
  readMusicPort,
  readCalendarPort,
  writeJson,
  // 给测试用
  _readWinreg: readWinreg,
  _readJson: readJson,
  _jsonPathCandidates: jsonPathCandidates,
};
