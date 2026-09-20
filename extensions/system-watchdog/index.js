'use strict';

/**
 * 系统调度守护 v0.1.0
 *
 * 等价 Process Lasso 核心能力:
 *   ProBalance    — 高 CPU 后台进程自动降优先级
 *   Disallowed    — 黑名单进程立即 kill
 *   LowMem        — 内存紧张时按 WorkingSet 杀非系统进程
 *   IdleSaver     — 空闲切省电电源计划
 *
 * 命令:
 *   watch.start   { interval_sec?, rules? }   → { ok, started, rules }
 *   watch.stop                                → { ok, stopped }
 *   watch.status                              → { running, rules, last_actions }
 *   watch.run_once                            → { ok, actions: [...] }
 *   watch.set_rules { disallowed?, low_mem_mb?, idle_min?, foreground_throttle?, cpu_threshold? }
 *   watch.tail_log { limit? }                 → { log: [...] }
 *
 * 状态持久化:~/.prisir/extensions/system-watchdog/state.json
 * 日志落点:~/.prisir/extensions/system-watchdog/actions.log(NDJSON)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'system-watchdog', name: '系统调度守护', version: '0.1.0' });

const HOME = () => process.env.PRISIR_EXT_HOME || '.';
const STATE = () => path.join(HOME(), 'state.json');
const LOG = () => path.join(HOME(), 'actions.log');

// 关键/安全/虚拟化/开发保护名单(P2025+5 修 — 误杀 vmmem/Everything/HipsDaemon 教训)
// 大小写不敏感比较。任何名单内进程永不 kill,也永不调优先级。
const PROTECT_NAMES = new Set([
  // Windows 系统
  'svchost.exe', 'lsass.exe', 'csrss.exe', 'winlogon.exe', 'explorer.exe',
  'dwm.exe', 'services.exe', 'smss.exe', 'wininit.exe', 'taskhostw.exe',
  'taskhost.exe', 'sihclient.exe', 'audiodg.exe', 'fontdrvhost.exe',
  'wdfmgr.exe', 'wdfmgr', 'msmpeng.exe', 'securityhealthservice.exe',
  'system', 'idle', 'registry', 'memory compression',
  // 安全软件
  'hipsdaemon.exe', 'hips.exe', '360safe.exe', '360tray.exe', 'zhudongfangyu.exe',
  'mssecsvc.exe', 'mpcmd_run.exe', 'avp.exe', 'avpui.exe', 'kav.exe', 'kavsvc.exe',
  // 虚拟化 / WSL / Docker / Hyper-V
  'vmmem', 'vmmem.exe', 'wsl.exe', 'wslhost.exe', 'wslservice.exe',
  'docker.exe', 'dockerd.exe', 'com.docker.backend', 'com.docker.service',
  'vmware-hostd.exe', 'vmware.exe', 'vmwaretray.exe', 'vmtoolsd.exe',
  'virtualboxvm.exe', 'vboxservice.exe', 'vboxtray.exe',
  // 开发工具链
  'everything.exe',        // 文件索引器,kill 后要几小时重建
  'listary.exe', 'listaryservice.exe',
  'git.exe', 'git-remote-http.exe', 'ssh.exe',
  'node.exe',              // 所有 node 子进程(含扩展子进程)
  'python.exe', 'pythonw.exe', // 我们 oiagent 全栈,误杀会断链
  'code.exe', 'cursor.exe',
  // 输入法 / 系统增强
  'lingxiime.exe', 'msctfime.exe', 'ctfmon.exe',
  // 网络栈关键
  'dns.exe', 'dnscrypt-proxy.exe',
].map(s => s.toLowerCase()));

const DEFAULT_RULES = {
  // ProBalance:后台进程 CPU 持续高 → 降优先级(只调优先级,不杀)
  foreground_throttle: true,
  cpu_threshold: 30,
  cpu_seconds: 8,
  // Disallowed:默认空 — 必须用户显式 add 才生效
  // (历史教训:默认填 msedge/chrome 会误杀正在用的浏览器导致桌面刷新)
  disallowed: [],
  // LowMem:默认 0 = 关闭。用户显式启用才生效
  // (历史教训:0 会被 falsy 兜底成 200MB,误杀 vmmem/Everything)
  low_mem_mb: 0,
  // IdleSaver
  idle_min: 0,
  // 轮询周期
  interval_sec: 5,
};

function isProtected(name) {
  return PROTECT_NAMES.has(String(name || '').toLowerCase());
}

function loadState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE(), 'utf8'));
    return {
      running: !!s.running,
      rules: Object.assign({}, DEFAULT_RULES, s.rules || {}),
      last_actions: Array.isArray(s.last_actions) ? s.last_actions.slice(-20) : [],
      last_tick_at: s.last_tick_at || 0,
    };
  } catch {
    return { running: false, rules: Object.assign({}, DEFAULT_RULES), last_actions: [], last_tick_at: 0 };
  }
}

function saveState(s) {
  try {
    fs.mkdirSync(HOME(), { recursive: true });
    fs.writeFileSync(STATE(), JSON.stringify(s, null, 2));
  } catch (e) {
    ext.log('warning', `state save failed: ${e.message}`);
  }
}

function appendLog(entry) {
  try {
    fs.mkdirSync(HOME(), { recursive: true });
    fs.appendFileSync(LOG(), JSON.stringify({ at: Date.now(), ...entry }) + '\n');
  } catch {}
}

function runPs(script, timeoutMs = 6000) {
  return new Promise((resolve) => {
    execFile('powershell', ['-NoProfile', '-Command', script],
      { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8', timeout: timeoutMs, windowsHide: true },
      (err, stdout, stderr) => resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message }));
  });
}

// ────────────────────────────── 单次巡检逻辑 ──────────────────────────────

async function probeProcs() {
  // 取所有进程(限 1000),前台窗口名(GetForegroundWindow → MainWindowTitle)
  // 输出 JSON,字段:ProcessName, Id, CPU, MemMB, IsForeground, HasWindow
  const script = `
$src = @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
"@
Add-Type -TypeDefinition $src -Language CSharp
$fgPid = 0
$h = [W]::GetForegroundWindow()
if ($h -ne 0) { [void][W]::GetWindowThreadProcessId($h, [ref]$fgPid) }
Get-Process | Where-Object { $_.Id -ne 0 -and $_.ProcessName -ne 'Idle' } |
  Select-Object Id, ProcessName,
    @{n='CPU';e={[math]::Round($_.CPU,2)}},
    @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}},
    @{n='HasWindow';e={$_.MainWindowTitle -ne ''}},
    @{n='IsForeground';e={$_.Id -eq $fgPid}},
    @{n='Path';e={$_.Path}} |
  Select-Object -First 1000 |
  ConvertTo-Json -Compress
`.trim();
  const r = await runPs(script);
  if (!r.ok) return { error: r.err || r.stderr, procs: [] };
  try {
    let arr = JSON.parse(r.stdout || '[]');
    if (!Array.isArray(arr)) arr = [arr];
    return { procs: arr };
  } catch (e) {
    return { error: 'parse: ' + e.message, procs: [] };
  }
}

async function getMemInfo() {
  const r = await runPs(`
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
  total_mb = [math]::Round($os.TotalVisibleMemorySize / 1024, 0)
  free_mb  = [math]::Round($os.FreeVisibleMemorySize / 1024, 0)
  used_pct = [math]::Round((1 - $os.FreeVisibleMemorySize / $os.TotalVisibleMemorySize) * 100, 1)
} | ConvertTo-Json -Compress
`.trim());
  if (!r.ok) return { error: r.err };
  try { return JSON.parse(r.stdout); } catch { return { error: 'parse' }; }
}

async function getIdleMin() {
  // 用 GetLastInputInfo 算空闲秒
  const r = await runPs(`
$src = @"
using System;
using System.Runtime.InteropServices;
public class I {
  [StructLayout(LayoutKind.Sequential)]
  public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(out LASTINPUTINFO plii);
  [DllImport("kernel32.dll")] public static extern uint GetTickCount();
}
"@
Add-Type -TypeDefinition $src -Language CSharp
$li = New-Object I+LASTINPUTINFO
$li.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($li)
[void][I]::GetLastInputInfo([ref]$li)
$idleSec = ([I]::GetTickCount() - $li.dwTime) / 1000
[pscustomobject]@{ idle_sec = [math]::Round($idleSec, 0) } | ConvertTo-Json -Compress
`.trim());
  if (!r.ok) return 0;
  try { return Number(JSON.parse(r.stdout).idle_sec) || 0; } catch { return 0; }
}

const POWER_SAVER = { name: '电源节能', hint: 'SCHEME_MIN / a1841308-3541-4fab-bc81-f71556f20b4a' };
const BALANCED = { name: '平衡', hint: '381b4222-f694-41f0-9685-ff5bb260df2e' };

async function setPowerPlan(plan) {
  const r = await runPs(`powercfg /setactive ${plan === 'saver' ? 'a1841308-3541-4fab-bc81-f71556f20b4a' : '381b4222-f694-41f0-9685-ff5bb260df2e'}`);
  return { ok: r.ok, stderr: r.stderr || '' };
}

async function setPriority(pid, level) {
  // BelowNormal | Normal | Idle
  const r = await runPs(`
$p = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
if ($p) {
  try {
    $p.PriorityClass = '${level}'
    'ok'
  } catch { "err: $_" }
} else { 'no-proc' }
`.trim());
  return { ok: r.ok, out: r.stdout.trim(), err: r.stderr };
}

async function killByName(name) {
  const r = await runPs(`Get-Process -Name '${name}' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; 'ok'`);
  return { ok: r.ok };
}

async function killByPid(pid, force = true) {
  const r = await runPs(`Stop-Process -Id ${pid} ${force ? '-Force' : ''} -ErrorAction SilentlyContinue; 'ok'`);
  return { ok: r.ok };
}

// ────────────────────────────── 单次巡检执行 ──────────────────────────────

async function runOnce(state) {
  const actions = [];
  const rules = state.rules;
  const { procs, error: probeErr } = await probeProcs();
  if (probeErr) {
    actions.push({ kind: 'probe_err', msg: probeErr });
    return { ok: false, actions };
  }

  // (1) Disallowed — 必须用户显式 add 才生效,默认空
  if (rules.disallowed && rules.disallowed.length) {
    const set = new Set(rules.disallowed.map(s => String(s).toLowerCase()));
    for (const p of procs) {
      const name = String(p.ProcessName || '').toLowerCase();
      if (set.has(name) && !isProtected(name)) {
        await killByPid(p.Id);
        actions.push({ kind: 'disallowed_kill', name: p.ProcessName, pid: p.Id });
        appendLog({ kind: 'disallowed_kill', name: p.ProcessName, pid: p.Id });
      }
    }
  }

  // (2) ProBalance — 后台 + CPU > threshold → BelowNormal
  if (rules.foreground_throttle) {
    const th = Number(rules.cpu_threshold) || 30;
    for (const p of procs) {
      if (p.IsForeground) continue;
      if (!p.HasWindow) continue;       // 只动有窗口的后台(避免误伤服务)
      const name = String(p.ProcessName || '').toLowerCase();
      if (isProtected(name)) continue;  // 保护名单里的进程永不动
      const cpu = Number(p.CPU) || 0;
      if (cpu >= th) {
        const r = await setPriority(p.Id, 'BelowNormal');
        if (r.out === 'ok') {
          actions.push({ kind: 'probalance', name: p.ProcessName, pid: p.Id, cpu });
          appendLog({ kind: 'probalance', name: p.ProcessName, pid: p.Id, cpu });
        }
      }
    }
  }

  // (3) LowMem — 默认 0 不启用;用户必须显式设置才生效
  // 同时要求 used_pct >= 95 才触发(原 85 太激进)
  const lowMemLimit = Number(rules.low_mem_mb) || 0;
  if (lowMemLimit > 0) {
    const mem = await getMemInfo();
    if (!mem.error && Number(mem.used_pct) >= 95) {
      const fat = procs
        .filter(p => !isProtected(String(p.ProcessName || '').toLowerCase()))
        .filter(p => !p.IsForeground)
        .filter(p => (p.MemMB || 0) >= lowMemLimit)
        .sort((a, b) => (b.MemMB || 0) - (a.MemMB || 0));
      // 一次最多杀 2 个(防连环误杀)
      let killedThisRound = 0;
      for (const p of fat) {
        if (killedThisRound >= 2) break;
        await killByPid(p.Id);
        actions.push({ kind: 'lowmem_kill', name: p.ProcessName, pid: p.Id, mem_mb: p.MemMB });
        appendLog({ kind: 'lowmem_kill', name: p.ProcessName, pid: p.Id, mem_mb: p.MemMB });
        killedThisRound++;
      }
    }
  }

  // (4) IdleSaver
  if (Number(rules.idle_min) > 0) {
    const idleSec = await getIdleMin();
    if (idleSec >= Number(rules.idle_min) * 60) {
      const r = await setPowerPlan('saver');
      if (r.ok && !state._lastIdleSwitch) {
        actions.push({ kind: 'idle_switch_saver', idle_sec: idleSec });
        appendLog({ kind: 'idle_switch_saver' });
        state._lastIdleSwitch = Date.now();
      }
    } else if (idleSec < 30 && state._lastIdleSwitch) {
      const r = await setPowerPlan('balanced');
      if (r.ok) {
        actions.push({ kind: 'idle_switch_balanced' });
        appendLog({ kind: 'idle_switch_balanced' });
        state._lastIdleSwitch = 0;
      }
    }
  }

  state.last_actions = actions;
  state.last_tick_at = Date.now();
  return { ok: true, actions };
}

// ────────────────────────────── 后台调度 ──────────────────────────────

let _loopTimer = null;

function startLoop(state) {
  if (_loopTimer) clearInterval(_loopTimer);
  const ms = Math.max(1, Number(state.rules.interval_sec) || 5) * 1000;
  _loopTimer = setInterval(async () => {
    try { await runOnce(state); saveState(state); }
    catch (e) { ext.log('error', `tick failed: ${e.message}`); }
  }, ms);
  // 不让 timer 拖住进程退出
  if (_loopTimer.unref) _loopTimer.unref();
}

function stopLoop() {
  if (_loopTimer) { clearInterval(_loopTimer); _loopTimer = null; }
}

// ────────────────────────────── 命令注册 ──────────────────────────────

ext.registerCommand('watch.start', async (args) => {
  const s = loadState();
  if (args.rules) s.rules = Object.assign({}, s.rules, args.rules);
  if (args.interval_sec) s.rules.interval_sec = Number(args.interval_sec);
  s.running = true;
  startLoop(s);
  saveState(s);
  return { ok: true, started: true, rules: s.rules };
});

ext.registerCommand('watch.stop', async () => {
  stopLoop();
  const s = loadState();
  s.running = false;
  saveState(s);
  return { ok: true, stopped: true };
});

ext.registerCommand('watch.status', async () => {
  const s = loadState();
  return { running: !!_loopTimer, rules: s.rules, last_actions: s.last_actions, last_tick_at: s.last_tick_at };
});

ext.registerCommand('watch.run_once', async () => {
  const s = loadState();
  return await runOnce(s);
});

ext.registerCommand('watch.set_rules', async (args) => {
  const s = loadState();
  s.rules = Object.assign({}, s.rules, args || {});
  saveState(s);
  if (_loopTimer) { stopLoop(); startLoop(s); }
  return { ok: true, rules: s.rules };
});

ext.registerCommand('watch.tail_log', async (args) => {
  const limit = Math.max(1, Math.min(500, Number(args.limit) || 50));
  try {
    const lines = fs.readFileSync(LOG(), 'utf8').trim().split('\n').filter(Boolean);
    return { log: lines.slice(-limit).map(l => { try { return JSON.parse(l); } catch { return { raw: l }; } }) };
  } catch { return { log: [] }; }
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
