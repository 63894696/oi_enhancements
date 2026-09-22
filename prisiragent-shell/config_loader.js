// config_loader.js — Electron 壳侧 PrisirAI 三端配置加载器(2026-09-22, P2.5+15)
//
// 镜像 Python 端 [prisIrai_config.py] + Rust 端 [prisiragent-tauri/src-tauri/src/config_loader.rs]
// 的存储契约:
//   1) 模块默认(代码内置)
//   2) YAML 文件(同级目录 / $INSTDIR / cwd 三候选)
//
// YAML 子集:key: value / 嵌套缩进 2 空格 / # 注释。零外部依赖(不用 js-yaml)。
//
// 字段:
//   ports.web / companion / music / calendar
//   brand.url / max_per_run / interval_sec / seen_cap
//   forum.url / board / hint

"use strict";

const fs = require("fs");
const path = require("path");
const { app } = require("electron");

// 模块内置默认(必须与 Python 端 prisIrai_config.py _DEFAULTS 一致)
const DEFAULTS = {
  "ports.web": 18802,
  "ports.companion": 18850,
  "ports.music": 0,
  "ports.calendar": 18803,
  "brand.url": "https://www.babelspan.com/updates.json",
  "brand.max_per_run": 3,
  "brand.interval_sec": 86400,
  "brand.seen_cap": 100,
  "forum.url": "https://bbs.babelspan.com/forum.html",
  "forum.board": "browser/shell",
  "forum.hint": "prisirai",
};

function yamlCandidates() {
  const out = [];
  // ① 跟当前脚本同级(prisiragent-shell/ + ../prisIrai_config.yaml)
  out.push(path.resolve(__dirname, "..", "prisIrai_config.yaml"));
  // ② $INSTDIR(装包后)
  if (process.env.INSTDIR) {
    out.push(path.join(process.env.INSTDIR, "prisIrai_config.yaml"));
  }
  // ③ cwd
  out.push(path.join(process.cwd(), "prisIrai_config.yaml"));
  // ④ app 装包后:同 __dirname 的兄弟(开发态 __dirname 已是 prisiragent-shell/)
  out.push(path.join(__dirname, "prisIrai_config.yaml"));
  return out;
}

function parseYaml(text) {
  const flat = {};
  let curSec = "";
  for (const line of text.split(/\r?\n/)) {
    const s = line.replace(/\s+$/, "");
    if (!s.trim() || s.trim().startsWith("#")) continue;
    const mSec = s.match(/^([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*$/);
    if (mSec) { curSec = mSec[1]; continue; }
    const m = s.match(/^  ([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*(.+?)\s*(?:#.*)?$/);
    if (m && curSec) flat[`${curSec}.${m[1]}`] = m[2];
  }
  return flat;
}

let _FLAT = null;
function loadFlat() {
  if (_FLAT) return _FLAT;
  for (const p of yamlCandidates()) {
    try {
      if (fs.existsSync(p)) {
        const parsed = parseYaml(fs.readFileSync(p, "utf8"));
        if (parsed) { _FLAT = Object.assign({}, DEFAULTS, parsed); return _FLAT; }
      }
    } catch (_) { /* continue */ }
  }
  _FLAT = Object.assign({}, DEFAULTS);
  return _FLAT;
}

function coerceInt(v, d) {
  const n = parseInt(v, 10);
  return (n >= 0 && n <= 65535) ? n : d;
}

function get(key) {
  const flat = loadFlat();
  const raw = flat[key];
  if (raw === undefined) return DEFAULTS[key];
  // 去掉引号
  const s = String(raw).trim().replace(/^["']|["']$/g, "");
  if (key.startsWith("ports.") || key === "brand.max_per_run"
      || key === "brand.interval_sec" || key === "brand.seen_cap") {
    return coerceInt(s, DEFAULTS[key]);
  }
  return s;
}

// 公开 API
module.exports = {
  get,
  // 快捷 getter
  webPortDefault: () => get("ports.web"),
  companionPortDefault: () => get("ports.companion"),
  musicPortDefault: () => get("ports.music"),
  calendarPortDefault: () => get("ports.calendar"),
  brandUrl: () => get("brand.url"),
  brandMaxPerRun: () => get("brand.max_per_run"),
  brandIntervalMs: () => get("brand.interval_sec") * 1000,
  brandSeenCap: () => get("brand.seen_cap"),
  forumUrl: () => get("forum.url"),
  forumBoard: () => get("forum.board"),
  forumHint: () => get("forum.hint"),
  forumFullUrl: () => `${get("forum.url")}#board=${get("forum.board")}&hint=${get("forum.hint")}`,
  // 测试用
  _yamlCandidates: yamlCandidates,
  _loadFlat: loadFlat,
  _DEFAULTS: DEFAULTS,
};
