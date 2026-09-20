'use strict';

/**
 * ext-mermaid v0.1.0 — 设计稿渲染扩展(示例)
 *
 * 行为:
 *   - 监听 AI 输出(onSessionMessage 钩子 + event.session.message.assistant)
 *   - 检测 ```mermaid ... ``` fenced block
 *   - 调 render.mermaid 命令(等价的同步调用入口)→ 返回 SVG HTML 卡片
 *   - 通过 ui.inject 通知主进程把卡片塞进当前会话
 *
 * 品牌令牌(写死,跟 diagram-design-prisir skill 一致):
 *   paper=#fdfcf8 / ink=#2f3a34 / accent=#c0392b / accent2=#3a7d6a /
 *   font=-apple-system,'PingFang SC','Microsoft YaHei',sans-serif /
 *   stroke=1.5px / radius=6px / 密度 4/10 / WCAG AA
 *
 * 注意:本示例不依赖 mermaid npm 包(避免装包),改用极简的 mermaid → SVG 渲染器
 * 支持子集:flowchart (graph LR/TD)、sequenceDiagram、erDiagram、stateDiagram-v2。
 * 后续可换 mermaid@11 CLI 或 js 库。
 */

const path = require('path');
const { PrisIrExt } = require('@prisir/extension-sdk');

const BRAND = {
  paper:       '#fdfcf8',
  ink:         '#2f3a34',
  inkMuted:    '#7a8580',
  accent:      '#c0392b',
  accent2:     '#3a7d6a',
  accentWarm:  '#d4a574',
  paper2:      '#fef9e7',
  font:        "-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  serif:       "'Source Han Serif SC', 'Noto Serif CJK SC', serif",
  mono:        "'JetBrains Mono', 'SF Mono', monospace",
  stroke:      1.5,
  radius:      6,
};

const ext = new PrisIrExt({
  id: 'mermaid',
  name: '设计稿渲染',
  version: '0.1.0',
});

// ──────────────── 渲染命令(主进程可主动 invoke,也供钩子内部调) ────────────────
ext.registerCommand('render.mermaid', async (args, ctx) => {
  const code = String(args.code || args.source || '');
  const title = String(args.title || '图');
  if (!code.trim()) {
    return { type: 'card', html: '<div class="ext-err">空代码块</div>' };
  }
  // 简单子集派发
  const lower = code.trim().toLowerCase();
  let svg;
  if (lower.startsWith('flowchart') || lower.startsWith('graph ')) {
    svg = renderFlowchart(code);
  } else if (lower.startsWith('sequencediagram')) {
    svg = renderSequence(code);
  } else if (lower.startsWith('erdiagram')) {
    svg = renderEr(code);
  } else if (lower.startsWith('statediagram')) {
    svg = renderState(code);
  } else {
    svg = renderFallback(code, title);
  }
  return {
    type: 'card',
    html: wrapCard(title, svg, ctx.sessionId || ''),
    meta: { engine: 'ext-mermaid@0.1.0', brand: 'prisIr' },
  };
});

// ──────────────── 钩子:AI 输出含 mermaid 时自动注入 ────────────────
ext.onSessionMessage(async (msg, ctx) => {
  const text = String(msg.text || msg.content || '');
  const sid = ctx.sessionId || msg.session_id || '';
  if (!text || !sid) return;
  const blocks = extractMermaidBlocks(text);
  if (!blocks.length) return;
  ext.log('info', `detected ${blocks.length} mermaid block(s)`);
  for (let i = 0; i < blocks.length; i += 1) {
    try {
      const card = await ext.commands.get('render.mermaid')(
        { code: blocks[i].code, title: blocks[i].title || '图' },
        ctx
      );
      ext.injectCard({
        sessionId: sid,
        cardId: `mermaid-${Date.now()}-${i}`,
        html: card.html,
      });
    } catch (e) {
      ext.log('error', `render err: ${e.message}`);
    }
  }
});

ext.start().catch((e) => {
  ext.log('error', `start failed: ${e.message}`);
  process.exit(1);
});

// ════════════════════════════════════════════════════════════════════════════
// 极简渲染器(只覆盖核心 4 种类型,够 demo;完整走 mermaid CLI/Puppeteer 是 Phase 2)
// ════════════════════════════════════════════════════════════════════════════

function extractMermaidBlocks(text) {
  const out = [];
  const re = /```mermaid\s*\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const code = m[1].trim();
    // 提取 %%title 注释作为卡片标题
    const tm = code.match(/^\s*%%\s*title\s*:?\s*(.+)$/m);
    const title = tm ? tm[1].trim() : '';
    out.push({ code, title });
  }
  return out;
}

function wrapCard(title, innerSvg, sid) {
  return [
    '<div class="ext-card ext-mermaid" data-ext="mermaid" data-sid="' + esc(sid) + '">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🎨 设计稿渲染</span>',
    '    <span class="ext-title">' + esc(title) + '</span>',
    '  </div>',
    '  <div class="ext-card-body" style="background:' + BRAND.paper + '">',
    innerSvg,
    '  </div>',
    '  <div class="ext-card-foot">由 ext-mermaid@0.1.0 渲染 · PrisirAI 品牌令牌</div>',
    '</div>',
  ].join('\n');
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>')
    .replace(/"/g, '"').replace(/'/g, '&#39;');
}

// ── flowchart (graph LR/TD) ──
function renderFlowchart(code) {
  // 极简解析:跳过 %%init 行,识别 A-->B / A---B / A-->|label|B 形式
  const lines = code.split(/\r?\n/).filter(l => l.trim() && !l.trim().startsWith('%%'));
  const dirMatch = lines[0].match(/graph\s+(LR|TD|BT|RL)/i);
  const dir = (dirMatch ? dirMatch[1].toUpperCase() : 'LR');
  const horiz = dir === 'LR' || dir === 'RL';
  const nodes = new Map(); // id → label
  const edges = []; // {from, to, label}
  for (const line of lines.slice(1)) {
    // A-->B / A---B / A-->|label|B / A[label]-->B[label]
    const link = line.match(/^\s*([A-Za-z0-9_一-龥]+)(?:\[([^\]]*)\])?\s*(-->|---|--\)|==>)\s*(?:\|([^|]*)\|\s*)?([A-Za-z0-9_一-龥]+)(?:\[([^\]]*)\])?\s*$/);
    if (link) {
      const from = link[1], fromLabel = link[2] || from;
      const to = link[5],   toLabel   = link[6] || to;
      const lbl = link[4] || '';
      if (!nodes.has(from)) nodes.set(from, fromLabel);
      if (!nodes.has(to))   nodes.set(to, toLabel);
      edges.push({ from, to, label: lbl });
    } else {
      // 单纯节点声明: A[label]
      const single = line.match(/^\s*([A-Za-z0-9_一-龥]+)\[([^\]]+)\]\s*$/);
      if (single && !nodes.has(single[1])) nodes.set(single[1], single[2]);
    }
  }
  // 布局:简易网格
  const ids = Array.from(nodes.keys());
  const colW = 160, rowH = 70, padX = 30, padY = 30;
  const cols = horiz ? Math.max(2, Math.ceil(Math.sqrt(edges.length + ids.length))) : 3;
  const pos = new Map();
  ids.forEach((id, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    pos.set(id, horiz
      ? { x: padX + c * colW, y: padY + r * rowH }
      : { x: padX + c * colW, y: padY + r * rowH });
  });
  const width = horiz ? padX * 2 + cols * colW : padX * 2 + cols * colW;
  const height = horiz ? padY * 2 + Math.ceil(ids.length / cols) * rowH : padY * 2 + Math.ceil(ids.length / cols) * rowH;
  // 渲染
  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:${BRAND.font};font-size:13px;max-width:100%;height:auto">`);
  parts.push(`<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${BRAND.ink}"/></marker></defs>`);
  // 边
  for (const e of edges) {
    const a = pos.get(e.from), b = pos.get(e.to);
    if (!a || !b) continue;
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${BRAND.ink}" stroke-width="${BRAND.stroke}" marker-end="url(#arr)"/>`);
    if (e.label) {
      parts.push(`<text x="${mx}" y="${my - 4}" text-anchor="middle" fill="${BRAND.inkMuted}" font-size="11">${esc(e.label)}</text>`);
    }
  }
  // 节点(2 个 accent,其他墨色)
  const accent = new Set([ids[0], ids[1]].filter(Boolean));
  ids.forEach((id, idx) => {
    const p = pos.get(id);
    const w = 110, h = 36;
    const isAcc = accent.has(id) && idx < 2;
    const fill = isAcc ? BRAND.accent : BRAND.paper;
    const stroke = isAcc ? BRAND.accent : BRAND.ink;
    const text = isAcc ? BRAND.paper : BRAND.ink;
    parts.push(`<rect x="${p.x - w/2}" y="${p.y - h/2}" width="${w}" height="${h}" rx="${BRAND.radius}" fill="${fill}" stroke="${stroke}" stroke-width="${BRAND.stroke}"/>`);
    parts.push(`<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" fill="${text}" font-weight="600">${esc(nodes.get(id))}</text>`);
  });
  parts.push('</svg>');
  return parts.join('');
}

// ── sequence ──
function renderSequence(code) {
  const lines = code.split(/\r?\n/).filter(l => l.trim() && !l.trim().startsWith('%%'));
  const participants = []; // [{id, label}]
  const seen = new Set();
  for (const line of lines) {
    const pm = line.match(/^\s*participant\s+([A-Za-z0-9_一-龥]+)(?:\s+as\s+(.+))?$/);
    if (pm) {
      if (!seen.has(pm[1])) { participants.push({ id: pm[1], label: pm[2] || pm[1] }); seen.add(pm[1]); }
      continue;
    }
    const m = line.match(/^\s*([A-Za-z0-9_一-龥]+)\s*(-{3,}>>| -{3,}|-->>)\s*([A-Za-z0-9_一-龥]+)\s*(?::\s*(.+))?$/);
    if (m) {
      if (!seen.has(m[1])) { participants.push({ id: m[1], label: m[1] }); seen.add(m[1]); }
      if (!seen.has(m[3])) { participants.push({ id: m[3], label: m[3] }); seen.add(m[3]); }
    }
  }
  if (!participants.length) return renderFallback(code, '时序图');
  const colW = 160, lifeH = 30;
  const padX = 30, padY = 40;
  const width = padX * 2 + participants.length * colW;
  const height = padY * 2 + participants.length * lifeH + 60;
  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:${BRAND.font};font-size:13px;max-width:100%;height:auto">`);
  participants.forEach((p, i) => {
    const x = padX + i * colW + colW / 2;
    parts.push(`<rect x="${x - 60}" y="${padY - 20}" width="120" height="32" rx="${BRAND.radius}" fill="${BRAND.paper}" stroke="${BRAND.ink}" stroke-width="${BRAND.stroke}"/>`);
    parts.push(`<text x="${x}" y="${padY}" text-anchor="middle" fill="${BRAND.ink}" font-weight="600">${esc(p.label)}</text>`);
    parts.push(`<line x1="${x}" y1="${padY + 12}" x2="${x}" y2="${height - padY}" stroke="${BRAND.inkMuted}" stroke-width="${BRAND.stroke}" stroke-dasharray="3,3"/>`);
  });
  let y = padY + 50;
  for (const line of lines) {
    const m = line.match(/^\s*([A-Za-z0-9_一-龥]+)\s*(-{3,}>>| -{3,}|-->>)\s*([A-Za-z0-9_一-龥]+)\s*(?::\s*(.+))?$/);
    if (!m) continue;
    const fromIdx = participants.findIndex(p => p.id === m[1]);
    const toIdx = participants.findIndex(p => p.id === m[3]);
    if (fromIdx < 0 || toIdx < 0) continue;
    const x1 = padX + fromIdx * colW + colW / 2;
    const x2 = padX + toIdx * colW + colW / 2;
    const solid = m[2].includes('>');
    parts.push(`<line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${BRAND.ink}" stroke-width="${BRAND.stroke}" ${solid ? '' : 'stroke-dasharray="4,3"'}/>`);
    parts.push(`<polygon points="${solid ? `${x2 - 8},${y - 4} ${x2},${y} ${x2 - 8},${y + 4}` : ''}" fill="${BRAND.ink}"/>`);
    if (m[4]) {
      const mx = (x1 + x2) / 2;
      parts.push(`<text x="${mx}" y="${y - 6}" text-anchor="middle" fill="${BRAND.ink}" font-size="11">${esc(m[4])}</text>`);
    }
    y += 30;
  }
  parts.push('</svg>');
  return parts.join('');
}

// ── er ──
function renderEr(code) {
  const lines = code.split(/\r?\n/).filter(l => l.trim() && !l.trim().startsWith('%%'));
  const entities = new Map(); // name → {label}
  const rels = []; // {from, to, label, card}
  for (const line of lines) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s+([|o\{\}-]{4,7}-[|o\{\}-]{4,7})\s+([A-Z_][A-Z0-9_]*)\s*(?::\s*"?(.+?)"?)?\s*$/);
    if (!m) continue;
    const from = m[1], to = m[3], card = m[2], label = m[4] || '';
    if (!entities.has(from)) entities.set(from, { label: from });
    if (!entities.has(to))   entities.set(to,   { label: to });
    rels.push({ from, to, label, card });
  }
  const ids = Array.from(entities.keys());
  if (!ids.length) return renderFallback(code, 'ER 图');
  const colW = 180, rowH = 80, padX = 40, padY = 40;
  const cols = Math.max(2, Math.ceil(Math.sqrt(ids.length)));
  const pos = new Map();
  ids.forEach((id, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    pos.set(id, { x: padX + c * colW, y: padY + r * rowH });
  });
  const width = padX * 2 + cols * colW;
  const height = padY * 2 + Math.ceil(ids.length / cols) * rowH;
  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:${BRAND.font};font-size:13px;max-width:100%;height:auto">`);
  for (const r of rels) {
    const a = pos.get(r.from), b = pos.get(r.to);
    if (!a || !b) continue;
    parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${BRAND.ink}" stroke-width="${BRAND.stroke}"/>`);
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    if (r.label) {
      parts.push(`<text x="${mx}" y="${my - 4}" text-anchor="middle" fill="${BRAND.ink}" font-size="11">${esc(r.label)}</text>`);
    }
  }
  ids.forEach((id) => {
    const p = pos.get(id);
    const w = 130, h = 38;
    parts.push(`<rect x="${p.x - w/2}" y="${p.y - h/2}" width="${w}" height="${h}" rx="${BRAND.radius}" fill="${BRAND.paper}" stroke="${BRAND.accent2}" stroke-width="${BRAND.stroke}"/>`);
    parts.push(`<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" fill="${BRAND.ink}" font-weight="600">${esc(entities.get(id).label)}</text>`);
  });
  parts.push('</svg>');
  return parts.join('');
}

// ── state ──
function renderState(code) {
  const lines = code.split(/\r?\n/).filter(l => l.trim() && !l.trim().startsWith('%%'));
  const states = new Set();
  const trans = []; // {from, to}
  for (const line of lines) {
    const m = line.match(/^\s*([^\s]+)\s*-->\s*([^\s:]+)(?:\s*:\s*(.+))?\s*$/);
    if (m) {
      states.add(m[1]); states.add(m[2]);
      trans.push({ from: m[1], to: m[2], label: m[3] || '' });
    }
  }
  // [*] 跳过(代表起止)
  const ids = Array.from(states).filter(s => s !== '[*]');
  if (!ids.length) return renderFallback(code, '状态机');
  const colW = 160, rowH = 70, padX = 30, padY = 30;
  const cols = Math.max(2, Math.ceil(Math.sqrt(ids.length)));
  const pos = new Map();
  ids.forEach((id, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    pos.set(id, { x: padX + c * colW, y: padY + r * rowH });
  });
  const width = padX * 2 + cols * colW;
  const height = padY * 2 + Math.ceil(ids.length / cols) * rowH;
  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:${BRAND.font};font-size:13px;max-width:100%;height:auto">`);
  parts.push(`<defs><marker id="arr2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${BRAND.ink}"/></marker></defs>`);
  for (const t of trans) {
    const a = pos.get(t.from), b = pos.get(t.to);
    if (!a || !b) continue;
    parts.push(`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${BRAND.ink}" stroke-width="${BRAND.stroke}" marker-end="url(#arr2)"/>`);
  }
  ids.forEach((id, idx) => {
    const p = pos.get(id);
    const w = 120, h = 40;
    const isAcc = idx < 2;
    const fill = isAcc ? BRAND.accent2 : BRAND.paper;
    const stroke = BRAND.ink;
    const text = isAcc ? BRAND.paper : BRAND.ink;
    parts.push(`<rect x="${p.x - w/2}" y="${p.y - h/2}" width="${w}" height="${h}" rx="${BRAND.radius + 4}" fill="${fill}" stroke="${stroke}" stroke-width="${BRAND.stroke}"/>`);
    parts.push(`<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" fill="${text}" font-weight="600">${esc(id)}</text>`);
  });
  parts.push('</svg>');
  return parts.join('');
}

function renderFallback(code, title) {
  return [
    '<div style="background:' + BRAND.paper2 + ';padding:14px;border-radius:' + BRAND.radius + 'px;border:1px dashed ' + BRAND.accentWarm + ';font-family:' + BRAND.mono + ';font-size:12px;color:' + BRAND.ink + ';white-space:pre-wrap;max-height:300px;overflow:auto">',
    esc(code),
    '</div>',
    '<div style="margin-top:8px;font-size:11px;color:' + BRAND.inkMuted + '">暂未渲染为 SVG(支持 flowchart / sequence / er / state,其他类型降级显示源码)。Phase 2 接 mermaid CLI。</div>',
  ].join('');
}
