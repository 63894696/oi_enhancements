'use strict';

/**
 * handraw-style-prompter v0.1.0 — 手绘海报 prompt 生成器(Phase B)
 *
 * 数据来源:本地 data/{styles,colors,layouts}.json(从 yang0/handraw-style 抽取,MIT)
 *
 * 命令:
 *   poster.smart       { theme, audience?, mood? }              → 推荐风格+颜色,弹卡片
 *   poster.spec        { style?, color?, layout?, subject }     → 精确指定,弹卡片
 *   poster.ai_design   { subject, scene?, audience?, density?,
 *                        emotion?, color?, style?, layout? }    → 8 字段海报设计
 *   poster.list        { kind: "styles"|"colors"|"layouts", q? }→ 列数据(给主对话 LLM 挑)
 *
 * 输出:中英双语 prompt 卡片(ui.inject.card),一键复制。
 * Phase D 会把卡片上的 prompt 喂给 video_creator.py 的 image-gen.from_poster_prompt。
 */

const path = require('path');
const fs = require('fs');
const { PrisIrExt } = require('@prisir/extension-sdk');

const BRAND = {
  paper:       '#fdfcf8',
  paper2:      '#fef9e7',
  ink:         '#2f3a34',
  inkMuted:    '#7a8580',
  accent:      '#c0392b',
  accent2:     '#3a7d6a',
  accentWarm:  '#d4a574',
  font:        "-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  serif:       "'Source Han Serif SC', 'Noto Serif CJK SC', serif",
  mono:        "'JetBrains Mono', 'SF Mono', monospace",
  stroke:      1.5,
  radius:      6,
};

// ── 数据加载(启动时一次,文件 100KB 内,内存无压力) ───────────────────
const DATA_DIR = path.join(__dirname, 'data');
function loadJson(name) {
  const p = path.join(DATA_DIR, name);
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}
const STYLES  = loadJson('styles.json');   // 278 条
const COLORS  = loadJson('colors.json');   // 36 条
const LAYOUTS = loadJson('layouts.json');  // 121 条

const ext = new PrisIrExt({
  id: 'handraw-style-prompter',
  name: '手绘海报 prompt 生成器',
  version: '0.1.0',
});

// ════════════════════════════════════════════════════════════════════════
// 推荐引擎(纯 JS,无网络):关键词 ↔ 风格/颜色 命中打分
// ════════════════════════════════════════════════════════════════════════

// 风格 → 情绪/题材标签(手挑,覆盖主流场景)
const STYLE_TAGS = {
  // A 组(幽默/社论漫画):适合反讽、轻松、社论、自嘲
  '001': ['doodle', 'humor', 'satire', 'daily', 'social', 'meme'],
  '002': ['editorial', 'concept', 'minimal', 'metaphor'],
  '011': ['meme', 'absurd', 'anti', 'dark-humor'],
  '013': ['satire', 'modern', 'flat', 'bold', 'social'],
  '019': ['webcomic', 'exaggeration', 'meme', 'animal'],
  // B 组(绘本/叙事):适合故事、温馨、儿童、童书
  '041': ['kids', 'absurd', 'whimsy', 'dr-seuss', 'storybook'],
  '042': ['animal', 'watercolor', 'classic', 'gentle', 'cute'],
  '046': ['geometric', 'kids', 'bold', 'picturebook'],
  '047': ['minimal', 'animal', 'deadpan', 'quiet', 'muted'],
  // C 组(平面/艺术):适合海报、复古、设计感
  '055': ['doodle', 'chaotic', 'crowd', 'color', 'pop'],
  '058': ['minimal', 'geometric', 'fashion', 'female', 'bold'],
  '059': ['silhouette', 'negative', 'high-contrast', 'fashion'],
  '061': ['midcentury', 'geometric', 'fairytale', 'kids'],
  '064': ['modernist', 'playful', 'collage', 'toy'],
  '071': ['street', 'kinetic', 'bold', 'pop'],
  '072': ['graffiti', 'symbolic', 'raw', 'urban'],
  // D 组(日系/插画)
  '083': ['ink', 'east-asian', 'editorial', 'dynamic'],
  '085': ['minimal', 'fashion', 'line', 'japanese'],
  '086': ['watercolor', 'japanese', 'editorial', 'quiet'],
  '099': ['japanese', 'warm', 'watercolor', 'cute'],
  '103': ['japanese', 'humor', 'everyday', 'satirical'],
  // E 组(中国)
  '124': ['chinese', 'ink', 'figure', 'classical'],
  '125': ['chinese', 'editorial', 'warm'],
  '127': ['chinese', 'watercolor', 'folk', 'illustration'],
  '142': ['chinese', 'retro', 'pop', 'commercial'],
  // F 组(通用网感)
  '162': ['memphis', 'geometric', 'bold', 'retro', 'poster'],
  '170': ['cyberpunk', 'neon', 'retro', 'dark'],
  '185': ['blueprint', 'technical', 'geometric', 'minimal'],
};

// 颜色 → 情绪/题材标签
const COLOR_TAGS = {
  // C-01 ~ C-06 蓝
  'C-01': ['vivid', 'art', 'spiritual', 'bold'],
  'C-02': ['tropical', 'vivid', 'warm', 'fashion'],
  'C-03': ['rational', 'deep', 'classic', 'editorial'],
  'C-04': ['daily', 'fresh', 'lively', 'clear'],
  'C-07': ['luxury', 'romantic', 'soft', 'lifestyle'],
  'C-10': ['natural', 'muted', 'calm', 'lifestyle'],
  'C-13': ['vintage', 'warm', 'lifestyle'],
  'C-19': ['romantic', 'passion', 'bold', 'female'],
  'C-21': ['soft', 'warm', 'romantic', 'female'],
  'C-23': ['romantic', 'poetic', 'quiet', 'female'],
  'C-25': ['luxury', 'warm', 'fashion', 'bold'],
  'C-27': ['warm', 'soft', 'daily', 'light'],
  'C-29': ['earthy', 'classical', 'muted', 'warm'],
  'C-30': ['pure', 'soft', 'minimal', 'calm'],
  'C-31': ['deep', 'cool', 'rational', 'editorial', 'monochrome'],
  'C-32': ['bold', 'modern', 'minimal', 'monochrome'],
  'C-34': ['soft', 'clean', 'minimal', 'breath'],
  'C-35': ['architectural', 'cool', 'rational', 'minimal'],
  'C-36': ['natural', 'muted', 'gentle'],
};

function scoreMatch(query, tags) {
  if (!query) return 0;
  const q = query.toLowerCase();
  let s = 0;
  for (const t of tags) {
    if (q.includes(t)) s += 2;
    if (t.includes(q.replace(/\s+/g, '-'))) s += 1;
  }
  // 标题词也加点分
  if (q.length >= 2) {
    for (const t of tags) {
      if (q.split(/[\s,]+/).some(w => w.length >= 2 && t.includes(w))) s += 1;
    }
  }
  return s;
}

function recommendStyle(theme) {
  const ranked = STYLES.map(s => {
    const tags = STYLE_TAGS[String(s.n).padStart(3, '0')] || [];
    return { ...s, _score: scoreMatch(theme, tags) + scoreMatch(theme, [s.name_zh.toLowerCase()]) };
  }).sort((a, b) => b._score - a._score);
  return ranked[0];
}

function recommendColor(theme) {
  const ranked = COLORS.map(c => {
    const tags = COLOR_TAGS[c.n] || [];
    return { ...c, _score: scoreMatch(theme, tags) + scoreMatch(theme, [c.name_zh, c.name_en.toLowerCase()]) };
  }).sort((a, b) => b._score - a._score);
  return ranked[0];
}

function pickByN(arr, n) {
  if (n == null) return null;
  const target = String(n).padStart(3, '0');           // "041"
  const targetNum = parseInt(target, 10);              // 41
  return arr.find(x => {
    const xs = String(x.n);
    return xs === target || parseInt(xs, 10) === targetNum;
  }) || null;
}

// ════════════════════════════════════════════════════════════════════════
// Prompt 拼接(参照 yang0 SKILL.md 输出结构)
// ════════════════════════════════════════════════════════════════════════

function buildPromptZh({ style, color, layout, subject }) {
  const lines = [];
  const styleLabel = style ? `${String(style.n).padStart(3, '0')}号风格:${style.name_zh}` : '';
  const colorLabel = color ? `${color.n} ${color.name_zh}` : '';
  const layoutLabel = layout ? `${layout.n} ${layout.name_zh}` : '';

  if (styleLabel) lines.push(`风格名称:${styleLabel}。`);
  if (colorLabel) lines.push(`主题色:${colorLabel}。`);

  // subject(主题)
  if (subject) lines.push(`主题:${subject}。`);

  // style traits
  if (style && style.traits_zh) lines.push(`核心风格特征:${style.traits_zh}。`);

  // layout prompt(纯图模式不嵌,图文模式强加)
  if (layout) {
    lines.push(`排版:${layout.prompt_zh}`);
  }
  return lines.join('\n');
}

function buildPromptEn({ style, color, subject }) {
  const lines = [];
  if (style) lines.push(`Style name: ${String(style.n).padStart(3, '0')} ${style.name_zh} (inspired by ${style.ref_author}).`);
  if (color) lines.push(`Theme color: ${color.name_en} (${color.name_zh}).`);
  if (subject) lines.push(`Subject: ${subject}.`);
  if (style && style.traits_zh) {
    // 翻译 traits 给 EN 用户(简化版:直接保留中文 traits + 关键英文标签)
    lines.push(`Core style traits: ${style.traits_zh.split(/[;,。]/)[0].trim()}.`);
  }
  return lines.join('\n');
}

// ════════════════════════════════════════════════════════════════════════
// 卡片 HTML
// ════════════════════════════════════════════════════════════════════════

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function wrapPromptCard({ title, style, color, layout, subject, promptZh, promptEn, source }) {
  const badgeMap = { smart: '🪄 智能推荐', spec: '🎯 精确指定', ai_design: '🎨 AI 海报设计' };
  const summary = [];
  if (style) summary.push(`<span class="ext-chip">${esc(String(style.n).padStart(3, '0'))} ${esc(style.name_zh)}</span>`);
  if (color) summary.push(`<span class="ext-chip ext-chip-color">${esc(color.n)} ${esc(color.name_zh)}</span>`);
  if (layout) summary.push(`<span class="ext-chip">${esc(layout.n)} ${esc(layout.name_zh)}</span>`);

  return [
    '<div class="ext-card ext-poster" data-ext="handraw-style-prompter">',
    '  <div class="ext-card-head">',
    `    <span class="ext-badge">${esc(badgeMap[source] || '🪄 海报 prompt')}</span>`,
    `    <span class="ext-title">${esc(title || '海报 prompt')}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">${summary.join(' ')}</div>`,
    '  <div class="ext-card-block">',
    '    <div class="ext-card-label">🇨🇳 中文 prompt</div>',
    `    <pre class="ext-card-pre" data-role="zh">${esc(promptZh)}</pre>`,
    '  </div>',
    '  <div class="ext-card-block">',
    '    <div class="ext-card-label">🇺🇸 English prompt</div>',
    `    <pre class="ext-card-pre" data-role="en">${esc(promptEn)}</pre>`,
    '  </div>',
    '  <div class="ext-card-foot">',
    '    <span>由 handraw-style-prompter@0.1.0 产出 · Phase D 可一键喂给 image-gen</span>',
    '    <button class="ext-copy-btn" data-copy-target="zh">复制中文</button>',
    '    <button class="ext-copy-btn" data-copy-target="en">Copy EN</button>',
    '  </div>',
    '</div>',
  ].join('\n');
}

// ════════════════════════════════════════════════════════════════════════
// 命令注册
// ════════════════════════════════════════════════════════════════════════

ext.registerCommand('poster.smart', async (args, ctx) => {
  const theme = String(args.theme || args.subject || '').trim();
  if (!theme) {
    return { type: 'card', html: wrapPromptCard({
      title: '需要主题',
      promptZh: '请提供主题,如:秋天的第一杯奶茶、城市夜景独白、儿童节绘本风',
      promptEn: 'Please provide a theme, e.g. "first milk tea in autumn", "city night monologue", "children\'s day storybook".',
      source: 'smart',
    }) };
  }
  const style  = recommendStyle(theme);
  const color  = recommendColor(theme);
  const promptZh = buildPromptZh({ style, color, subject: theme });
  const promptEn = buildPromptEn({ style, color, subject: theme });
  const html = wrapPromptCard({
    title: `推荐: ${theme}`,
    style, color, subject: theme,
    promptZh, promptEn,
    source: 'smart',
  });
  // 自动注入主对话
  if (ctx && ctx.sessionId) {
    ext.injectCard({ sessionId: ctx.sessionId, cardId: `poster-smart-${Date.now()}`, html });
  }
  return { type: 'card', html, meta: { style: style.n, color: color.n, theme, prompt_zh: promptZh, prompt_en: promptEn } };
});

ext.registerCommand('poster.spec', async (args, ctx) => {
  const subject = String(args.subject || args.theme || '').trim();
  const style  = pickByN(STYLES, args.style);
  const color  = pickByN(COLORS, args.color);
  const layout = pickByN(LAYOUTS, args.layout);
  if (!subject) {
    return { type: 'card', html: wrapPromptCard({
      title: '需要主题',
      promptZh: '精确模式请同时给 style/color/layout/subject,缺一报错。',
      promptEn: 'Spec mode requires subject (and optional style/color/layout).',
      source: 'spec',
    }) };
  }
  const promptZh = buildPromptZh({ style, color, layout, subject });
  const promptEn = buildPromptEn({ style, color, subject });
  const html = wrapPromptCard({
    title: `指定:${subject}`,
    style, color, layout, subject,
    promptZh, promptEn,
    source: 'spec',
  });
  if (ctx && ctx.sessionId) {
    ext.injectCard({ sessionId: ctx.sessionId, cardId: `poster-spec-${Date.now()}`, html });
  }
  return { type: 'card', html, meta: { style: style && style.n, color: color && color.n, layout: layout && layout.n, prompt_zh: promptZh, prompt_en: promptEn } };
});

ext.registerCommand('poster.ai_design', async (args, ctx) => {
  const subject  = String(args.subject || '').trim();
  if (!subject) {
    return { type: 'card', html: wrapPromptCard({
      title: '需要 subject',
      promptZh: 'AI 海报设计需 8 字段:subject 必填,scene/audience/density/emotion/color/style/layout 可选。',
      promptEn: 'AI poster design needs subject (required) + scene/audience/density/emotion/color/style/layout (optional).',
      source: 'ai_design',
    }) };
  }
  const style  = pickByN(STYLES, args.style)  || recommendStyle([subject, args.scene, args.emotion].filter(Boolean).join(' '));
  const color  = pickByN(COLORS, args.color)  || recommendColor([subject, args.emotion, args.audience].filter(Boolean).join(' '));
  const layout = pickByN(LAYOUTS, args.layout) || (args.density && args.density >= 4 ? LAYOUTS.find(l => l.cat === 'IG') : LAYOUTS.find(l => l.cat === 'SC'));

  const meta = { subject, scene: args.scene, audience: args.audience, density: args.density, emotion: args.emotion };
  const promptZh = [
    style  && `风格:${String(style.n).padStart(3, '0')} ${style.name_zh}`,
    color  && `主题色:${color.n} ${color.name_zh}`,
    layout && `版式:${layout.n} ${layout.name_zh}`,
    `主体:${subject}`,
    args.scene     && `场景:${args.scene}`,
    args.audience  && `受众:${args.audience}`,
    args.density   && `信息密度:${args.density}/5`,
    args.emotion   && `情绪基调:${args.emotion}`,
    style && style.traits_zh && `核心风格特征:${style.traits_zh}`,
    layout && `排版规则:${layout.prompt_zh}`,
  ].filter(Boolean).join('\n');
  const promptEn = buildPromptEn({ style, color, subject });
  const html = wrapPromptCard({
    title: `海报设计:${subject}`,
    style, color, layout, subject,
    promptZh, promptEn,
    source: 'ai_design',
  });
  if (ctx && ctx.sessionId) {
    ext.injectCard({ sessionId: ctx.sessionId, cardId: `poster-ai-${Date.now()}`, html });
  }
  return { type: 'card', html, meta: { ...meta, prompt_zh: promptZh, prompt_en: promptEn } };
});

ext.registerCommand('poster.list', async (args) => {
  const kind = String(args.kind || 'styles');
  const q = String(args.q || '').trim().toLowerCase();
  let arr;
  if (kind === 'styles') arr = STYLES;
  else if (kind === 'colors') arr = COLORS;
  else if (kind === 'layouts') arr = LAYOUTS;
  else return { type: 'text', text: `unknown kind: ${kind}` };
  let filtered = arr;
  if (q) {
    filtered = arr.filter(x => {
      const hay = JSON.stringify(x).toLowerCase();
      return hay.includes(q);
    });
  }
  return { type: 'text', text: filtered.slice(0, 50).map(x => {
    if (kind === 'styles')  return `${String(x.n).padStart(3, '0')} · ${x.ref_author} → ${x.name_zh}`;
    if (kind === 'colors')  return `${x.n} · ${x.name_zh} (${x.name_en})`;
    if (kind === 'layouts') return `${x.n} · ${x.name_zh}`;
    return JSON.stringify(x);
  }).join('\n') };
});

// ════════════════════════════════════════════════════════════════════════
// 钩子(Phase C 接入主对话 EXEC 链前的占位,本版本不做 onSessionMessage)
// ════════════════════════════════════════════════════════════════════════

ext.start().catch((e) => {
  ext.log('error', `start failed: ${e.message}`);
  process.exit(1);
});

// 测试钩子:test 时不启动 RPC,但要求能从外部拿到 ext instance
if (process.env.PRISIR_EXT_NO_START === '1') {
  // 不真的 start,导出 instance
  module.exports = { ext, STYLES, COLORS, LAYOUTS };
}