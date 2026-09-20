'use strict';

/**
 * 按键模拟 v0.1.0
 *
 * 命令:
 *   key.type   { text, interval_ms? }      → { ok, count }
 *   key.hotkey { combo }                    → { ok }  例如 "Ctrl+C" / "Alt+Tab" / "Ctrl+Shift+S"
 *   key.click  { x, y, button? }           → { ok }  mouse_event 点击
 *   key.press  { key, mods? }              → { ok }  单键 + mods
 *
 * ⚠ 高权限 — SendInput 走 user32,可能被 UIPI / 焦点限制拒
 * ⚠ 默认每次调用都打 [ext:keystroke-emit] 日志(便于审计)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'keystroke-emit', name: '按键模拟', version: '0.1.0' });

const { execFile } = require('child_process');
function runPs(script, timeoutMs = 4000) {
  return new Promise((resolve) => {
    execFile('powershell', ['-NoProfile', '-Command', script],
      { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8', timeout: timeoutMs, windowsHide: true },
      (err, stdout, stderr) => resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message }));
  });
}

// 已知 SendInput 包装:扫键→下→上→移,文本逐字注入
const PS_TYPE_BODY = `
$src = @"
using System;
using System.Runtime.InteropServices;
using System.Threading;
public class K {
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, int dwExtraInfo);
  public const uint KEYEVENTF_KEYUP = 0x0002;
  public const uint KEYEVENTF_UNICODE = 0x0004;
  public static void SendChar(char c) {
    keybd_event(0, 0, KEYEVENTF_UNICODE, 0);
    keybd_event(0, (byte)c, KEYEVENTF_UNICODE, 0);
    keybd_event(0, (byte)c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0);
  }
}
"@
Add-Type -TypeDefinition $src -Language CSharp
$text = 'TEXT_PLACEHOLDER'
$interval = INTERVAL_PLACEHOLDER
foreach ($c in $text.ToCharArray()) {
  [K]::SendChar($c)
  if ($interval -gt 0) { Start-Sleep -Milliseconds $interval }
}
'ok-count=' + $text.Length
`.trim();

function buildTypeScript(text, interval) {
  // 单引号 + 嵌入 escape(PS 单引号串内要 double-single 表达单引号)
  const escaped = text.replace(/'/g, "''");
  return PS_TYPE_BODY
    .replace('TEXT_PLACEHOLDER', `'${escaped}'`)
    .replace('INTERVAL_PLACEHOLDER', String(interval));
}

ext.registerCommand('key.type', async (args) => {
  const text = String(args.text || '');
  if (!text) return { error: 'text required' };
  const interval = Number(args.interval_ms) || 5;
  const script = buildTypeScript(text, interval);
  const r = await runPs(script);
  ext.log('info', `type: "${text.slice(0,40)}${text.length > 40 ? '...' : ''}" (${text.length} chars)`);
  const m = r.stdout.match(/ok-count=(\d+)/);
  return { ok: r.ok, count: m ? Number(m[1]) : 0, raw: r.stdout.trim() };
});

// 组合键映射
const VK_MAP = {
  ctrl: 0x11, control: 0x11, alt: 0x12, shift: 0x10, win: 0x5B, meta: 0x5B,
  tab: 0x09, enter: 0x0D, return: 0x0D, esc: 0x1B, escape: 0x1B,
  space: 0x20, backspace: 0x08, delete: 0x2E, del: 0x2E,
  home: 0x24, end: 0x23, pgup: 0x21, pgdn: 0x22,
  left: 0x25, right: 0x27, up: 0x26, down: 0x28,
  f1: 0x70, f2: 0x71, f3: 0x72, f4: 0x73, f5: 0x74, f6: 0x75,
  f7: 0x76, f8: 0x77, f9: 0x78, f10: 0x79, f11: 0x7A, f12: 0x7B,
  a: 0x41, b: 0x42, c: 0x43, d: 0x44, e: 0x45, f: 0x46, g: 0x47,
  h: 0x48, i: 0x49, j: 0x4A, k: 0x4B, l: 0x4C, m: 0x4D, n: 0x4E,
  o: 0x4F, p: 0x50, q: 0x51, r: 0x52, s: 0x53, t: 0x54, u: 0x55,
  v: 0x56, w: 0x57, x: 0x58, y: 0x59, z: 0x5A,
  '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
  '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
};

function parseCombo(combo) {
  const parts = String(combo).split('+').map(s => s.trim().toLowerCase()).filter(Boolean);
  const mods = [];
  let main = null;
  for (const p of parts) {
    if (['ctrl', 'control', 'alt', 'shift', 'win', 'meta'].includes(p)) mods.push(p);
    else main = p;
  }
  return { mods, main };
}

const PS_HOTKEY_BODY = `
$src = @"
using System;
using System.Runtime.InteropServices;
public class K {
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, int dwExtraInfo);
  public const uint KEYEVENTF_KEYUP = 0x0002;
}
"@
Add-Type -TypeDefinition $src -Language CSharp
$mods = @(MODS_CSV -split ',' | Where-Object { $_ -ne '' })
$main = [byte]MAIN_VK
foreach ($m in $mods) { [K]::keybd_event([byte]$m, 0, 0, 0) }
[K]::keybd_event($main, 0, 0, 0)
Start-Sleep -Milliseconds 30
[K]::keybd_event($main, 0, 0x0002, 0)
foreach ($m in ($mods | Sort-Object -Descending)) { [K]::keybd_event([byte]$m, 0, 0x0002, 0) }
'ok'
`.trim();

function buildHotkeyScript(modVks, mainVk) {
  // JS 端已知 modVks + mainVk,直接做模板替换(避 $args 索引坑)
  return PS_HOTKEY_BODY
    .replace('MODS_CSV', modVks.join(','))
    .replace('MAIN_VK', String(mainVk));
}

ext.registerCommand('key.hotkey', async (args) => {
  const { mods, main } = parseCombo(args.combo);
  if (!main) return { error: 'combo empty' };
  const mainVk = VK_MAP[main];
  if (mainVk === undefined) return { error: `unknown key: ${main}` };
  const modVks = mods.map(m => VK_MAP[m]).filter(v => v !== undefined);
  if (mods.length && !modVks.length) return { error: `unknown modifier: ${mods.join(',')}` };
  const script = buildHotkeyScript(modVks, mainVk);
  const r = await runPs(script);
  ext.log('info', `hotkey: ${args.combo}`);
  const ok = r.ok && r.stdout.trim() === 'ok';
  return { ok, combo: args.combo, raw: r.stdout.trim(), err: ok ? null : (r.err || r.stderr) };
});

ext.registerCommand('key.press', async (args) => {
  const vk = VK_MAP[String(args.key || '').toLowerCase()];
  if (vk === undefined) return { error: `unknown key: ${args.key}` };
  const mods = Array.isArray(args.mods) ? args.mods : [];
  const modVks = mods.map(m => VK_MAP[String(m).toLowerCase()]).filter(v => v !== undefined);
  const script = buildHotkeyScript(modVks, vk);
  const r = await runPs(script);
  const ok = r.ok && r.stdout.trim() === 'ok';
  return { ok, key: args.key, raw: r.stdout.trim() };
});

const PS_CLICK_BODY = `
$src = @"
using System;
using System.Runtime.InteropServices;
public class M {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, int dwData, int dwExtraInfo);
  public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
  public const uint MOUSEEVENTF_LEFTUP   = 0x0004;
  public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
  public const uint MOUSEEVENTF_RIGHTUP   = 0x0010;
}
"@
Add-Type -TypeDefinition $src -Language CSharp
[M]::SetCursorPos([int]X_PLACEHOLDER, [int]Y_PLACEHOLDER) | Out-Null
Start-Sleep -Milliseconds 30
$btn = 'BTN_PLACEHOLDER'
if ($btn -eq 'right') {
  [M]::mouse_event([M]::MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
  [M]::mouse_event([M]::MOUSEEVENTF_RIGHTUP,   0, 0, 0, 0)
} else {
  [M]::mouse_event([M]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
  [M]::mouse_event([M]::MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)
}
'ok'
`.trim();

function buildClickScript(x, y, btn) {
  return PS_CLICK_BODY
    .replace('X_PLACEHOLDER', String(x))
    .replace('Y_PLACEHOLDER', String(y))
    .replace('BTN_PLACEHOLDER', btn);
}

ext.registerCommand('key.click', async (args) => {
  if (args.x === undefined || args.y === undefined) return { error: 'x,y required' };
  const x = Number(args.x), y = Number(args.y);
  const btn = String(args.button || 'left');
  const script = buildClickScript(x, y, btn);
  const r = await runPs(script);
  ext.log('info', `click: ${btn}@(${x},${y})`);
  return { ok: r.ok, x, y, button: btn, raw: r.stdout.trim() };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });