'use strict';

/**
 * 剪贴板 v0.1.0
 *
 * 命令:
 *   clip.read   {}              → { text, length }
 *   clip.write  { text }        → { ok, length }
 *   clip.history { limit? }     → { items, total }
 *
 * 实现:Windows PowerShell Get-Clipboard / Set-Clipboard(UTF-8 via Out-File)。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'clipboard', name: '剪贴板历史', version: '0.1.0' });

const HIST_FILE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'history.json');

function load() {
  try {
    if (fs.existsSync(HIST_FILE())) return JSON.parse(fs.readFileSync(HIST_FILE(), 'utf8'));
  } catch {}
  return [];
}

function save(items) {
  try { fs.writeFileSync(HIST_FILE(), JSON.stringify(items, null, 2)); return true; } catch { return false; }
}

function pushHist(text) {
  if (!text) return;
  const all = load();
  if (all[0] && all[0].text === text) return;
  all.unshift({ text: text.slice(0, 10000), at: Date.now() });
  while (all.length > 50) all.pop();
  save(all);
}

function runPs(script) {
  return new Promise((resolve) => {
    execFile('powershell', ['-NoProfile', '-Command', script],
      { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8' },
      (err, stdout, stderr) => {
        resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message });
      });
  });
}

ext.registerCommand('clip.read', async () => {
  const r = await runPs('Get-Clipboard -Raw');
  if (!r.ok) return { error: r.err || r.stderr };
  const text = r.stdout.replace(/\r?\n$/, '');
  pushHist(text);
  return { text, length: text.length };
});

ext.registerCommand('clip.write', async (args) => {
  const text = String(args.text || '');
  // 写到临时文件再读回,保留 UTF-8 中文
  const tmp = path.join(require('os').tmpdir(), `__clip_${Date.now()}.txt`);
  fs.writeFileSync(tmp, text, 'utf8');
  // 用 Get-Content + Set-Clipboard
  const r = await runPs(`Get-Content -LiteralPath "${tmp}" -Raw | Set-Clipboard`);
  try { fs.unlinkSync(tmp); } catch {}
  if (!r.ok) return { error: r.err || r.stderr };
  pushHist(text);
  return { ok: true, length: text.length };
});

ext.registerCommand('clip.history', async (args) => {
  const items = load().slice(0, Number(args.limit) || 20);
  return { items, total: items.length };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
