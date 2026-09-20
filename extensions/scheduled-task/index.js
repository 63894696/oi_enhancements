'use strict';

/**
 * 计划任务 v0.1.0
 *
 * 命令:
 *   sched.list  { name? }                  → { tasks: [{name, status, next_run, last_run}] }
 *   sched.create { name, exe, args?, when, dir? }
 *     when: 'onstart' | 'onlogon' | 'daily' | 'hourly' | time string e.g. '09:30'
 *   sched.delete { name }                  → { ok }
 *   sched.run    { name }                  → { ok }
 *   sched.get    { name }                  → { task }
 *
 * 实现:schtasks.exe(Windows 自带),无需 Node 额外依赖
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const ext = new PrisIrExt({ id: 'scheduled-task', name: '计划任务', version: '0.1.0' });

function runSchtasks(args, timeoutMs = 8000) {
  return new Promise((resolve) => {
    execFile('schtasks', args, { maxBuffer: 4 * 1024 * 1024, encoding: 'utf8', timeout: timeoutMs, windowsHide: true },
      (err, stdout, stderr) => resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message }));
  });
}

ext.registerCommand('sched.list', async (args) => {
  const r = await runSchtasks(['/query', '/fo', 'LIST', '/v']);
  if (!r.ok) return { error: r.err || r.stderr };
  const out = r.stdout;
  // 解析每段以 "TaskName:" 开头的块
  const tasks = [];
  const blocks = out.split(/\r?\n\r?\n/).filter(Boolean);
  for (const b of blocks) {
    const obj = {};
    for (const line of b.split(/\r?\n/)) {
      const m = line.match(/^([^:]+):\s*(.*)$/);
      if (m) obj[m[1].trim()] = m[2].trim();
    }
    if (obj.HostName && obj.TaskName) {
      tasks.push({
        name: obj.TaskName,
        status: obj.Status,
        next_run: obj['Next Run Time'],
        last_run: obj['Last Run Time'],
        author: obj['Author'],
      });
    }
  }
  const filtered = args.name ? tasks.filter(t => t.name.toLowerCase().includes(String(args.name).toLowerCase())) : tasks;
  return { tasks: filtered, total: filtered.length };
});

ext.registerCommand('sched.create', async (args) => {
  if (!args.name || !args.exe || !args.when) return { error: 'name / exe / when required' };
  const taskName = String(args.name).trim();
  if (!/^[A-Za-z0-9_\-\\$]{1,32}$/.test(taskName)) return { error: `invalid task name: ${taskName}` };

  // 拼 /sc 参数
  let sc = '';
  const when = String(args.when).toLowerCase();
  if (when === 'onstart') sc = 'ONSTART';
  else if (when === 'onlogon') sc = 'ONLOGON';
  else if (when === 'hourly') sc = 'HOURLY';
  else if (when === 'daily') sc = 'DAILY';
  else if (/^\d{2}:\d{2}$/.test(when)) {
    const [h, m] = when.split(':');
    sc = `DAILY`;
    const cargs = ['/create', '/tn', taskName, '/tr',
      `"${String(args.exe).replace(/"/g, '')}${args.args ? ' ' + String(args.args) : ''}"`,
      '/sc', sc, '/st', `${h}:${m}`];
    if (args.dir) cargs.push('/wd', String(args.dir));
    cargs.push('/f');    // 覆盖已存在
    const r = await runSchtasks(cargs);
    if (!r.ok) return { error: r.err || r.stderr };
    return { ok: true, name: taskName, when, action: 'create' };
  } else {
    return { error: `unsupported when: ${when}(use onstart/onlogon/daily/hourly/HH:MM)` };
  }
  const cargs = ['/create', '/tn', taskName, '/tr',
    `"${String(args.exe).replace(/"/g, '')}${args.args ? ' ' + String(args.args) : ''}"`,
    '/sc', sc];
  if (args.dir) cargs.push('/wd', String(args.dir));
  cargs.push('/f');
  const r = await runSchtasks(cargs);
  if (!r.ok) return { error: r.err || r.stderr };
  return { ok: true, name: taskName, when, action: 'create' };
});

ext.registerCommand('sched.delete', async (args) => {
  if (!args.name) return { error: 'name required' };
  const r = await runSchtasks(['/delete', '/tn', String(args.name), '/f']);
  return { ok: r.ok, name: args.name, raw: r.stdout.trim(), err: r.ok ? null : (r.err || r.stderr) };
});

ext.registerCommand('sched.run', async (args) => {
  if (!args.name) return { error: 'name required' };
  const r = await runSchtasks(['/run', '/tn', String(args.name)]);
  return { ok: r.ok, name: args.name, err: r.ok ? null : (r.err || r.stderr) };
});

ext.registerCommand('sched.get', async (args) => {
  if (!args.name) return { error: 'name required' };
  const list = await ext.invokeSelf('sched.list', { name: args.name });
  if (list.error) return list;
  const task = list.tasks.find(t => t.name.toLowerCase() === String(args.name).toLowerCase()) || list.tasks[0] || null;
  return { task };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });