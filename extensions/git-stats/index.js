'use strict';

/**
 * 本地 Git 统计 v0.1.0
 *
 * 命令:
 *   git.shortlog { path, since?, until?, max? }   → { authors, total_commits }
 *   git.numstat  { path, since?, until?, max? }   → { files, total_added, total_removed }
 *   git.filemv   { path, since?, until?, max? }   → { files }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'git-stats', name: '本地 Git 统计', version: '0.1.0' });

// 从 PRISIR_EXT_HOME(扩展安装目录)往上找最近的 git repo 根
function findRepoRoot(start) {
  let cur = path.resolve(start || process.cwd());
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(cur, '.git'))) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return null;
}

function resolveRepo(args) {
  if (args.path && args.path !== '.' && path.isAbsolute(args.path)) return args.path;
  if (args.path && args.path !== '.' && fs.existsSync(args.path)) {
    // args.path 是个具体子目录,用其上级 repo
    if (fs.existsSync(path.join(args.path, '.git'))) return args.path;
  }
  // 优先级:PRISIR_WORKDIR(主进程 workdir) > ext_home 上溯 > cwd
  if (process.env.PRISIR_WORKDIR) {
    const found = findRepoRoot(process.env.PRISIR_WORKDIR);
    if (found) return found;
  }
  const home = process.env.PRISIR_EXT_HOME;
  if (home) {
    const found = findRepoRoot(home);
    if (found) return found;
  }
  // 兜底
  return process.cwd();
}

function run(args) {
  return new Promise((resolve) => {
    execFile('git', args, { maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message });
    });
  });
}

function runPipe(args1, args2) {
  // 两条 git 命令用 spawn pipe 连接(规避 Windows git shortlog -n 3 的 revision 误解析)
  return new Promise((resolve) => {
    const { spawn } = require('child_process');
    const a = spawn('git', args1, { stdio: ['ignore', 'pipe', 'pipe'] });
    const b = spawn('git', args2, { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    a.stdout.pipe(b.stdin);
    a.stderr.on('data', d => stderr += d);
    b.stdout.on('data', d => stdout += d);
    b.stderr.on('data', d => stderr += d);
    let done = false;
    const finish = (err) => { if (!done) { done = true; resolve({ ok: !err, stdout, stderr, err: err && err.message }); } };
    a.on('exit', code => { if (code !== 0) finish(new Error(`git log exit ${code}`)); });
    b.on('exit', code => { if (code !== 0) finish(new Error(`git shortlog exit ${code}`)); else finish(null); });
    b.on('error', finish);
  });
}

ext.registerCommand('git.shortlog', async (args) => {
  // Windows git 把 -n N 当成 revision,会歧义;改用 `git log | git shortlog`
  const repo = resolveRepo(args);
  if (!repo) return { error: 'no git repo found from PRISIR_EXT_HOME' };
  const logArgs = ['-C', repo, 'log', '--pretty=short'];
  if (args.since) logArgs.push(`--since=${args.since}`);
  if (args.until) logArgs.push(`--until=${args.until}`);
  if (args.max)   logArgs.push('-n', String(args.max));
  const shortArgs = ['shortlog', '-sn'];
  const r = await runPipe(logArgs, shortArgs);
  if (!r.ok) return { error: r.err || r.stderr, repo };
  const lines = r.stdout.trim().split('\n').filter(Boolean);
  const authors = lines.map(l => {
    const m = l.match(/^\s*(\d+)\s+(.+?)(?:\s+<([^>]+)>)?$/);
    return m ? { commits: Number(m[1]), name: m[2].trim(), email: m[3] || '' } : { raw: l };
  });
  const total_commits = authors.reduce((s, a) => s + (a.commits || 0), 0);
  return { authors, total_commits };
});

ext.registerCommand('git.numstat', async (args) => {
  // numstat 用 -C <path> 子命令形式
  const repo = resolveRepo(args);
  if (!repo) return { error: 'no git repo found' };
  const opts = ['-C', repo, 'log', '--pretty=tformat:', '--numstat'];
  if (args.since) opts.push(`--since=${args.since}`);
  if (args.until) opts.push(`--until=${args.until}`);
  if (args.max)   opts.push('-n', String(args.max));
  const r = await run(opts);
  if (!r.ok) return { error: r.err || r.stderr, repo };
  const files = {};
  let total_added = 0, total_removed = 0;
  for (const line of r.stdout.split('\n')) {
    const m = line.match(/^(\d+|-)\s+(\d+|-)\s+(.+)$/);
    if (!m) continue;
    const a = m[1] === '-' ? 0 : Number(m[1]);
    const d = m[2] === '-' ? 0 : Number(m[2]);
    files[m[3]] = (files[m[3]] || { added: 0, removed: 0 });
    files[m[3]].added += a;
    files[m[3]].removed += d;
    total_added += a;
    total_removed += d;
  }
  const sorted = Object.entries(files).map(([file, v]) => ({ file, ...v, total: v.added + v.removed }))
    .sort((a, b) => b.total - a.total);
  return { files: sorted, total_added, total_removed, count: sorted.length };
});

ext.registerCommand('git.filemv', async (args) => {
  const repo = resolveRepo(args);
  if (!repo) return { error: 'no git repo found' };
  const opts = ['-C', repo, 'log', '--pretty=tformat:', '--name-status'];
  if (args.since) opts.push(`--since=${args.since}`);
  if (args.until) opts.push(`--until=${args.until}`);
  if (args.max)   opts.push('-n', String(args.max));
  const r = await run(opts);
  if (!r.ok) return { error: r.err || r.stderr, repo };
  const files = {};
  for (const line of r.stdout.split('\n')) {
    const m = line.match(/^([AMDRC])\s+(.+)$/);
    if (!m) continue;
    const op = { A:'add', M:'mod', D:'del', R:'ren', C:'cpy' }[m[1]] || m[1];
    files[m[2]] = (files[m[2]] || { ops: {} });
    files[m[2]].ops[op] = (files[m[2]].ops[op] || 0) + 1;
  }
  return { files };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
