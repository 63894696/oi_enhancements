'use strict';

/**
 * 应用启动器 v0.1.0
 *
 * 命令:
 *   app.run    { exe, args?, cwd?, window? }   → { ok, pid, hint }
 *     window: normal | hidden | minimized | maximized(默认 normal)
 *   app.open_url { url }                       → { ok }
 *   app.open_path { path }                     → { ok }  (文件/目录/explorer / select 父)
 *   app.recent  { limit? }                     → { items: [{path, at}] }  最近启动 exe
 *
 * 安全:白名单 exe 名(避免注入);运行后写 recent 列表(最近 20 条)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'app-launcher', name: '应用启动器', version: '0.1.0' });

const RECENT_FILE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'recent.json');

// 简单白名单:不强制(只是防护意外传错);运行时仍按 exe 名/路径解析
const ALLOW_BUILTINS = new Set([
  'notepad.exe', 'mspaint.exe', 'calc.exe', 'wordpad.exe', 'msconfig.exe',
  'taskmgr.exe', 'explorer.exe', 'cmd.exe', 'powershell.exe', 'wt.exe',
  'chrome.exe', 'msedge.exe', 'firefox.exe',
  'code.exe', 'cursor.exe',
]);

function loadRecent() {
  try { return JSON.parse(fs.readFileSync(RECENT_FILE(), 'utf8')); } catch { return []; }
}
function saveRecent(items) {
  try { fs.writeFileSync(RECENT_FILE(), JSON.stringify(items.slice(0, 20), null, 2)); } catch {}
}
function pushRecent(p) {
  if (!p) return;
  const items = loadRecent().filter(i => i.path !== p);
  items.unshift({ path: p, at: Date.now() });
  saveRecent(items);
}

function runPs(script, timeoutMs = 5000) {
  return new Promise((resolve) => {
    execFile('powershell', ['-NoProfile', '-Command', script],
      { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8', timeout: timeoutMs, windowsHide: true },
      (err, stdout, stderr) => resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message }));
  });
}

ext.registerCommand('app.run', async (args) => {
  const exe = String(args.exe || '').trim();
  if (!exe) return { error: 'exe required' };
  const baseName = path.basename(exe).toLowerCase();
  const builtin = ALLOW_BUILTINS.has(baseName);
  if (args.builtin_only === true && !builtin) return { error: `not in builtin allowlist: ${baseName}` };

  const argList = Array.isArray(args.args) ? args.args : (args.args ? String(args.args).split(/\s+/) : []);
  const cwd = args.cwd || process.env.PRISIR_WORKDIR || process.cwd();
  const winMap = { normal: 1, hidden: 0, minimized: 7, maximized: 3 };
  const wflag = winMap[String(args.window || 'normal')] !== undefined ? winMap[String(args.window || 'normal')] : 1;

  // 用 PowerShell Start-Process 传 ProcessStartInfo;detached 不阻塞
  const psArgs = argList.map(a => `'${String(a).replace(/'/g, "''")}'`).join(',');
  const script = `
$p = Start-Process -FilePath '${exe.replace(/'/g, "''")}' ${argList.length ? `-ArgumentList @(${psArgs})` : ''} -WorkingDirectory '${cwd.replace(/'/g, "''")}' -WindowStyle @(${wflag}) -PassThru -ErrorAction Stop
[pscustomobject]@{ pid=$p.Id } | ConvertTo-Json -Compress
`.trim();
  const r = await runPs(script);
  if (!r.ok) return { error: r.err || r.stderr, hint: '检查 exe 路径是否正确', builtin };
  try {
    const out = JSON.parse(r.stdout);
    pushRecent(path.resolve(exe));
    return { ok: true, pid: out.pid, builtin };
  } catch (e) {
    return { ok: r.ok, error: 'parse: ' + e.message, raw: r.stdout };
  }
});

ext.registerCommand('app.open_url', async (args) => {
  const url = String(args.url || '').trim();
  if (!url) return { error: 'url required' };
  if (!/^(https?:\/\/|file:\/\/|mailto:|ms-windows-store:)/i.test(url)) {
    return { error: 'protocol not allowed (need http/https/file/mailto/ms-windows-store)' };
  }
  const r = await runPs(`Start-Process '${url.replace(/'/g, "''")}'`);
  return { ok: r.ok, error: r.ok ? null : (r.err || r.stderr) };
});

ext.registerCommand('app.open_path', async (args) => {
  const p = String(args.path || '').trim();
  if (!p) return { error: 'path required' };
  const abs = path.resolve(p);
  if (!fs.existsSync(abs)) return { error: 'path not exist', path: abs };
  if (fs.statSync(abs).isDirectory()) {
    const r = await runPs(`Start-Process explorer.exe '${abs.replace(/'/g, "''")}'`);
    return { ok: r.ok, mode: 'directory', path: abs };
  }
  const r = await runPs(`Start-Process '${abs.replace(/'/g, "''")}'`);
  return { ok: r.ok, mode: 'file', path: abs };
});

ext.registerCommand('app.recent', async (args) => {
  const items = loadRecent().slice(0, Number(args.limit) || 10);
  return { items, total: items.length };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });