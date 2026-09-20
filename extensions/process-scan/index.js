'use strict';

/**
 * 进程扫描 v0.1.0
 *
 * 命令:
 *   ps.list { name?, limit? }        → { processes: [{pid, name, cpu, mem_mb, path, has_window}], total }
 *   ps.find { name, exact? }         → { matches: [...] }
 *   ps.kill { pid | name, force? }   → { killed, failed }
 *
 * kill 高风险:默认按 name 杀需要传 force:true 才会真杀,单 PID 默认走 CloseMainWindow 优雅退出。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const ext = new PrisIrExt({ id: 'process-scan', name: '进程扫描', version: '0.1.0' });

function runPs(script, timeoutMs = 8000) {
  return new Promise((resolve) => {
    execFile('powershell', ['-NoProfile', '-Command', script],
      { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8', timeout: timeoutMs, windowsHide: true },
      (err, stdout, stderr) => resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message }));
  });
}

const PS_LIST = `
Get-Process | Select-Object Id,ProcessName,@{n='CPU';e={[math]::Round($_.CPU,2)}},@{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,1)}},@{n='HasWindow';e={$_.MainWindowTitle -ne ''}},Path | ConvertTo-Json -Compress
`.trim();

ext.registerCommand('ps.list', async (args) => {
  const r = await runPs(PS_LIST);
  if (!r.ok) return { error: r.err || r.stderr };
  let arr = [];
  try { arr = JSON.parse(r.stdout || '[]'); } catch (e) { return { error: 'parse: ' + e.message }; }
  if (!Array.isArray(arr)) arr = [arr];
  const name = String(args.name || '').toLowerCase();
  if (name) arr = arr.filter(p => String(p.ProcessName || '').toLowerCase().includes(name));
  const total = arr.length;
  const limit = Number(args.limit) || 200;
  arr = arr.slice(0, limit);
  const processes = arr.map(p => ({
    pid: p.Id,
    name: p.ProcessName,
    cpu: p.CPU || 0,
    mem_mb: p.MemMB || 0,
    has_window: !!p.HasWindow,
    path: p.Path || '',
  }));
  return { processes, total };
});

ext.registerCommand('ps.find', async (args) => {
  if (!args.name) return { error: 'name required' };
  // 子进程内手动调 ps.list(SDK 未暴露 invokeSelf)
  const list = await ext.commands.get('ps.list')({ name: args.name }, { sessionId: '' });
  if (list.error) return list;
  const exact = args.exact === true;
  const matches = exact
    ? list.processes.filter(p => String(p.name).toLowerCase() === String(args.name).toLowerCase())
    : list.processes;
  return { matches, total: matches.length };
});

ext.registerCommand('ps.kill', async (args) => {
  if (!args.pid && !args.name) return { error: 'pid or name required' };
  let pids = [];
  if (args.pid) pids = [Number(args.pid)];
  else {
    const list = await ext.commands.get('ps.list')({ name: args.name }, { sessionId: '' });
    if (list.error) return list;
    pids = list.processes.map(p => p.pid);
  }
  if (!pids.length) return { killed: 0, failed: 0, error: 'no matching process' };

  // 默认优雅:CloseMainWindow → 3s 后强制 Stop-Process
  const force = args.force === true;
  const script = `
$pids = @(${pids.join(',')})
$killed = 0; $failed = 0
foreach ($pid in $pids) {
  $p = Get-Process -Id $pid -ErrorAction SilentlyContinue
  if (-not $p) { $failed++; continue }
  try {
    if ('${force}' -eq 'true') {
      Stop-Process -Id $pid -Force -ErrorAction Stop
    } else {
      if ($p.MainWindowTitle -ne '') { $p.CloseMainWindow() | Out-Null } else { Stop-Process -Id $pid -ErrorAction Stop }
      $p.WaitForExit(3000) | Out-Null
      if (-not $p.HasExited) { Stop-Process -Id $pid -Force -ErrorAction Stop }
    }
    $killed++
  } catch {
    $failed++
  }
}
[pscustomobject]@{ killed=$killed; failed=$failed } | ConvertTo-Json -Compress
`.trim();
  const r = await runPs(script);
  if (!r.ok) return { error: r.err || r.stderr };
  try {
    const out = JSON.parse(r.stdout);
    return { killed: out.killed, failed: out.failed, force };
  } catch (e) { return { error: 'parse: ' + e.message + ' raw: ' + r.stdout }; }
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });