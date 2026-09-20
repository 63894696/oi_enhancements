'use strict';

/**
 * 时序图构建 v0.1.0
 *
 * 命令:
 *   seq.fromsteps { steps: [{from, to, message, type?}, ...] } → { mermaid, ascii, steps }
 *   seq.fromscript { lines }   → 同上,每行格式:"A ->> B: hello" / "A -->> B: reply"
 *   seq.parse { text }          → 同上,自动识别多行脚本(空行分隔)
 *
 * 输出 mermaid sequenceDiagram,可直接喂给 ext-mermaid 渲染。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'sequence-builder', name: '时序图', version: '0.1.0' });

function dedup(actors, a, b) {
  if (!actors.includes(a)) actors.push(a);
  if (b && !actors.includes(b)) actors.push(b);
}

function build(steps) {
  const actors = [];
  const mermaidLines = ['sequenceDiagram'];
  for (const s of steps) {
    dedup(actors, s.from, s.to);
    const arrow = (s.type || 'sync') === 'reply' ? '-->>'
              : (s.type === 'async') ? '->>'
              : '->>';
    mermaidLines.push(`    ${s.from} ${arrow} ${s.to}: ${s.message}`);
  }
  // participants 顺序按出现顺序,插到头部后
  if (actors.length) {
    const parts = actors.map(a => `    participant ${a}`);
    mermaidLines.splice(1, 0, ...parts);
  }
  return { mermaid: mermaidLines.join('\n'), actors };
}

function renderAscii(steps) {
  // 简易 ASCII:每个 actor 一列,按时间步
  const actors = [];
  for (const s of steps) {
    if (!actors.includes(s.from)) actors.push(s.from);
    if (s.to && !actors.includes(s.to)) actors.push(s.to);
  }
  if (!actors.length) return '';
  const colWidth = Math.max(...actors.map(a => a.length), 6) + 2;
  const lines = [];
  // 顶行
  lines.push(actors.map(a => a.padEnd(colWidth)).join(''));
  lines.push(actors.map(a => '-'.repeat(colWidth)).join(''));
  for (const s of steps) {
    const row = actors.map(() => ' '.repeat(colWidth)).join('');
    const chars = (s.type === 'reply') ? '-->>' : '-->';
    const fromIdx = actors.indexOf(s.from);
    const toIdx = s.to ? actors.indexOf(s.to) : fromIdx;
    // 简化:直接在 from 列打消息文本
    const arr = row.split('');
    let col = fromIdx * colWidth;
    arr[col] = s.from[0];
    const msg = `${chars} ${s.to || ''}: ${s.message}`.slice(0, colWidth * Math.abs(toIdx - fromIdx) + 30);
    lines.push(arr.join('') + '  ' + msg);
  }
  return lines.join('\n');
}

function parseLine(line) {
  const m = String(line).trim().match(/^(\w+)\s*(->>|-->>|--?>>|-->|->)\s*(\w+)\s*:\s*(.+)$/);
  if (!m) return null;
  const arrow = m[2];
  const type = arrow.includes('>>') ? (arrow === '-->>' ? 'reply' : 'async') : 'sync';
  return { from: m[1], to: m[3], message: m[4].trim(), type };
}

ext.registerCommand('seq.fromsteps', async (args) => {
  const steps = Array.isArray(args.steps) ? args.steps : [];
  if (!steps.length) return { error: 'steps empty' };
  const r = build(steps);
  return { mermaid: r.mermaid, ascii: renderAscii(steps), steps };
});

ext.registerCommand('seq.fromscript', async (args) => {
  const steps = String(args.lines || '').split(/\r?\n/).map(parseLine).filter(Boolean);
  if (!steps.length) return { error: 'no parseable lines (expect "A ->> B: msg" or "A -->> B: msg")' };
  const r = build(steps);
  return { mermaid: r.mermaid, ascii: renderAscii(steps), steps };
});

ext.registerCommand('seq.parse', async (args) => {
  const text = String(args.text || '').trim();
  if (!text) return { error: 'empty' };
  const steps = text.split(/\r?\n/).map(parseLine).filter(Boolean);
  if (!steps.length) return { error: 'no parseable lines' };
  const r = build(steps);
  return { mermaid: r.mermaid, ascii: renderAscii(steps), steps };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
