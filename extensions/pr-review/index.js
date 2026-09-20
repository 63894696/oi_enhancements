'use strict';

/**
 * PR Diff 评论辅助 v0.1.0
 *
 * 命令:
 *   pr.diff      { path, range? }            → { raw, files }
 *   pr.hunks     { path, file, range? }      → { hunks: [{ header, lines }] }
 *   pr.checklist { path, range? }            → { items: [{ category, hint }] }
 *
 * 审查维度(简化版,可被 settings.checklist 覆盖):
 *   - 调试日志残留(console.log / fmt.Println / println! / System.out)
 *   - TODO/FIXME 注释
 *   - 大文件改动(> 400 行)
 *   - 缺少测试覆盖(touched test 文件比例 < 30%)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { execFile } = require('child_process');
const ext = new PrisIrExt({ id: 'pr-review', name: 'PR Diff 评论辅助', version: '0.1.0' });

function git(args) {
  return new Promise((resolve) => {
    execFile('git', args, { maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', err: err && err.message });
    });
  });
}

ext.registerCommand('pr.diff', async (args) => {
  const a = ['-C', String(args.path || '.'), 'diff', '--no-color'];
  if (args.range) a.push(args.range);
  const r = await git(a);
  if (!r.ok) return { error: r.err || r.stderr };
  // 拆成文件块
  const files = [];
  const parts = r.stdout.split(/^diff --git /m).filter(Boolean);
  for (const p of parts) {
    const header = p.split('\n')[0];
    const file = (header.match(/ b\/(.+)$/) || [])[1] || header;
    files.push({ file, body: 'diff --git ' + p });
  }
  return { raw: r.stdout, files, bytes: r.stdout.length };
});

ext.registerCommand('pr.hunks', async (args) => {
  const a = ['-C', String(args.path || '.'), 'diff', '--no-color', '--', String(args.file)];
  if (args.range) a.splice(5, 0, args.range);
  const r = await git(a);
  if (!r.ok) return { error: r.err || r.stderr };
  const hunks = [];
  const lines = r.stdout.split('\n');
  let cur = null;
  for (const l of lines) {
    if (l.startsWith('@@')) {
      if (cur) hunks.push(cur);
      cur = { header: l, lines: [] };
    } else if (cur) {
      cur.lines.push(l);
    }
  }
  if (cur) hunks.push(cur);
  return { file: args.file, hunks, count: hunks.length };
});

ext.registerCommand('pr.checklist', async (args) => {
  const diffRes = await ext.runCommand('pr.diff', args).catch(() => null);
  const raw = diffRes && diffRes.result ? diffRes.result.raw : '';
  if (!raw) return { items: [], note: 'no diff' };
  const items = [];
  if (/^\+.*(console\.log|fmt\.Println|System\.out\.print|println!)/m.test(raw)) {
    items.push({ category: 'debug', hint: '检测到调试日志残留,合并前删除或改为正式日志' });
  }
  if (/^\+.*(TODO|FIXME|XXX)/m.test(raw)) {
    items.push({ category: 'todo', hint: '检测到 TODO/FIXME 注释,确认是否需要立即处理或新建 issue' });
  }
  const files = (raw.match(/^diff --git /mg) || []).length;
  const addedLines = (raw.match(/^\+/mg) || []).filter(l => !l.startsWith('+++')).length;
  if (addedLines > 400) {
    items.push({ category: 'large', hint: `本次改动 ${addedLines} 行,建议拆成多个 commit 便于 review` });
  }
  const testFiles = (raw.match(/^diff --git a\/.+(test|spec|_test)/mg) || []).length;
  if (files > 0 && testFiles / files < 0.3 && addedLines > 50) {
    items.push({ category: 'test_coverage',
      hint: `测试文件占比 ${Math.round(testFiles / files * 100)}%,低于 30% 建议补单测` });
  }
  return { items, files, added_lines: addedLines, test_files: testFiles };
});

// SDK 在 0.1.0 没暴露 runCommand — 我们直接调底层
ext.runCommand = async function (method, args) {
  // 复用本扩展的 handler
  const handlers = this._handlers || (this._handlers = {});
  const h = handlers[method];
  if (!h) return { error: 'unknown method' };
  return { result: await h(args || {}) };
};

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
