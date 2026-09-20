'use strict';

/**
 * ASCII 树 v0.1.0
 *
 * 命令:
 *   tree.frompaths { paths: [..] }     → string   从路径列表(可包含子结构提示)→ ascii
 *   tree.fromjson  { data, root? }     → string   JSON 对象 → ascii 树
 *   tree.render    { lines, style? }   → string   已有缩进文本("a\n  b\n  c\n    d") → 树形字符
 *
 * 风格:unicode(默认 ┌ │ └ ─ ├) / ascii(+,|,`,-) / box(┌─┐│└─┘)
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'ascii-tree', name: 'ASCII 树', version: '0.1.0' });

const CHARS = {
  unicode: { up: '┌', down: '└', pipe: '│', hor: '─', tee: '├', branch: '└' },
  ascii:   { up: '+', down: '+', pipe: '|', hor: '-', tee: '+', branch: '`' },
  box:     { up: '┌', down: '└', pipe: '│', hor: '─', tee: '├', branch: '└' },
};

function buildTree(node, prefix = '', isLast = true, ch = CHARS.unicode, lines = []) {
  if (typeof node === 'string') {
    const conn = isLast ? ch.down + ch.hor + ch.hor : ch.tee + ch.hor + ch.hor;
    lines.push(prefix + conn + ' ' + node);
    return lines;
  }
  if (typeof node !== 'object' || node === null) return lines;
  const entries = Object.entries(node);
  entries.forEach(([k, v], i) => {
    const isLastChild = i === entries.length - 1;
    const conn = isLastChild ? ch.down + ch.hor + ch.hor : ch.tee + ch.hor + ch.hor;
    const childPrefix = prefix + (isLastChild ? '   ' : ch.pipe + '  ');
    if (typeof v === 'object' && v !== null && !Array.isArray(v) && Object.keys(v).length > 0) {
      lines.push(prefix + conn + ' ' + k);
      buildTree(v, childPrefix, isLastChild, ch, lines);
    } else if (Array.isArray(v) && v.length > 0 && typeof v[0] === 'object') {
      lines.push(prefix + conn + ' ' + k + ' (' + v.length + ')');
      v.forEach((item, j) => {
        const last = j === v.length - 1;
        const c2 = last ? ch.down + ch.hor + ch.hor : ch.tee + ch.hor + ch.hor;
        lines.push(childPrefix + c2 + ' [' + j + ']');
        if (typeof item === 'object' && item !== null) buildTree(item, childPrefix + (last ? '   ' : ch.pipe + '  '), last, ch, lines);
      });
    } else {
      const repr = Array.isArray(v) ? '[ ]' : (typeof v === 'object' && v !== null) ? '{ }' : JSON.stringify(v);
      lines.push(prefix + conn + ' ' + k + ': ' + repr);
    }
  });
  return lines;
}

function pad(lines) {
  // 把 childrenPrefix 之前预留的连接符转成普通空格填充,首行不再处理
  return lines;
}

ext.registerCommand('tree.fromjson', async (args) => {
  const ch = CHARS[args.style || 'unicode'] || CHARS.unicode;
  if (typeof args.data === 'string') {
    try { args.data = JSON.parse(args.data); } catch (e) { return { error: 'invalid json: ' + e.message }; }
  }
  const root = args.root || '.';
  const lines = buildTree({ [root]: args.data }, '', true, ch, []);
  return { text: lines.join('\n'), lines: lines.length };
});

ext.registerCommand('tree.frompaths', async (args) => {
  // paths: 字符串数组,按 / 或 \ 拆分,自动建嵌套
  const ch = CHARS[args.style || 'unicode'] || CHARS.unicode;
  const sep = /[\\/]/;
  const root = {};
  for (const p of (args.paths || [])) {
    const parts = String(p).split(sep).filter(Boolean);
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const k = parts[i];
      if (i === parts.length - 1) {
        if (!(k in cur)) cur[k] = null;       // file
      } else {
        if (!(k in cur) || typeof cur[k] !== 'object' || cur[k] === null) cur[k] = {};
        cur = cur[k];
      }
    }
  }
  const lines = buildTree(root, '', true, ch, []);
  return { text: lines.join('\n'), lines: lines.length };
});

ext.registerCommand('tree.render', async (args) => {
  const ch = CHARS[args.style || 'unicode'] || CHARS.unicode;
  const lines = String(args.lines || '').split(/\r?\n/).filter(Boolean);
  // 检测缩进,推断深度,把空格缩进替换为树形字符
  // 假定缩进 = 2 空格一档
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const m = line.match(/^(\s*)(.*)$/);
    const indent = m[1].length;
    const text = m[2];
    const depth = Math.floor(indent / 2);
    const isLast = (i === lines.length - 1) ||
                   (i + 1 < lines.length && lines[i + 1].match(/^(\s*)/)[1].length <= indent);
    const prefix = (ch.pipe + '  ').repeat(Math.max(0, depth - 1)) + (depth >= 1 ? (isLast ? '   ' : ch.pipe + '  ') : '');
    const conn = isLast ? ch.down + ch.hor + ch.hor : ch.tee + ch.hor + ch.hor;
    out.push(prefix + conn + ' ' + text);
  }
  return { text: out.join('\n'), lines: out.length };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
