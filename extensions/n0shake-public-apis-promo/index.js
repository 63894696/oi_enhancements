'use strict';

/**
 * n0shake-public-apis-promo v0.1.0 — 免 key/试用/开源 API 资源导航器(Sprint 2 Phase B)
 * 数据源:本地 data/{categories,services,meta}.json(n0shake/Public-APIs 快照,2026-10-02)
 * 与 public-apis-promo 差异:命名空间 nokeyapi.*;筛选维度改 open_trial 三档;badge 🔓
 * 命令:nokeyapi.find / list_categories / detail / random
 */

const path = require('path');
const fs = require('fs');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ── 数据加载(481 条 < 300KB,启动时一次,内存无压力) ──

const DATA_DIR = path.join(__dirname, 'data');
function loadJson(name) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, name), 'utf-8'));
}
const CATEGORIES = loadJson('categories.json');   // 56 项
const SERVICES   = loadJson('services.json');      // 481 项
const META       = loadJson('meta.json');          // 1 项

const CAT_BY_NAME = new Map();
for (const c of CATEGORIES) CAT_BY_NAME.set(c.name, c);

const SVC_BY_NAME = new Map();
for (const s of SERVICES) SVC_BY_NAME.set(s.name, s);

// ── 工具函数 ──

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function scoreMatch(haystack, query) {
  const h = haystack.toLowerCase();
  const q = query.toLowerCase();
  if (h === q) return 100;
  if (h.startsWith(q)) return 80;
  const terms = q.split(/\s+/).filter(Boolean);
  if (terms.length === 1) return h.includes(q) ? 50 : 0;
  return terms.every(t => h.includes(t)) ? 40 : 0;
}

// open_trial 三档配色:N/A → good(亮点),💸 → warn(收费提示),Open Source → accent
function renderOpenTrialChip(value) {
  let cls = 'ext-chip';
  if (value === 'N/A') cls = 'ext-chip ext-chip-good';
  else if (value === '💸') cls = 'ext-chip ext-chip-warn';
  else if (value === 'Open Source') cls = 'ext-chip ext-chip-accent';
  return `<span class="${cls}">免 key: ${esc(value || '(未标注)')}</span>`;
}

// ── 卡片 HTML 渲染 ──

function wrapServiceCard({ title, items, query, category, snapshot }) {
  const catPills = category
    ? `<span class="ext-chip ext-chip-accent">分类: ${esc(category)}</span>`
    : '';
  const queryPill = query
    ? `<span class="ext-chip">关键词: ${esc(query)}</span>`
    : '';
  const listHtml = items.map((s, i) => {
    const linkHtml = s.url
      ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" class="ext-link">${esc(s.url)}</a>`
      : `<span class="ext-link ext-link-muted">(无链接)</span>`;
    const chips = renderOpenTrialChip(s.open_trial);
    return [
      `  <div class="ext-svc-item">`,
      `    <div class="ext-svc-head">`,
      `      <span class="ext-svc-num">${i + 1}</span>`,
      `      <span class="ext-svc-name">${esc(s.name)}</span>`,
      `      <span class="ext-svc-cat">${esc(s.cat)}</span>`,
      `    </div>`,
      `    <div class="ext-svc-desc">${esc(s.desc || '(无描述)')}</div>`,
      `    <div class="ext-svc-chips">${chips}</div>`,
      `    <div class="ext-svc-foot">${linkHtml}</div>`,
      `  </div>`,
    ].join('\n');
  }).join('\n');

  return [
    '<div class="ext-card ext-nokey-api" data-ext="n0shake-public-apis-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🔓 免 key API</span>',
    `    <span class="ext-title">${esc(title)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">${catPills} ${queryPill}</div>`,
    `  <div class="ext-svc-list">${listHtml}</div>`,
    `  <div class="ext-card-foot">`,
    `    <span>共 ${items.length} 条 · 数据快照 ${esc(snapshot)} · 来自 n0shake/Public-APIs</span>`,
    `    <button class="ext-copy-btn" data-copy-target="list">复制清单</button>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapDetailCard({ svc, snapshot }) {
  const linkHtml = svc.url
    ? `<a href="${esc(svc.url)}" target="_blank" rel="noopener" class="ext-link">${esc(svc.url)}</a>`
    : `<span class="ext-link ext-link-muted">(无链接)</span>`;
  const chip = renderOpenTrialChip(svc.open_trial);
  return [
    '<div class="ext-card ext-nokey-api" data-ext="n0shake-public-apis-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🔓 免 key API 详情</span>',
    `    <span class="ext-title">${esc(svc.name)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">`,
    `    <span class="ext-chip ext-chip-accent">分类: ${esc(svc.cat)}</span>`,
    `    ${chip}`,
    `  </div>`,
    `  <div class="ext-svc-list">`,
    `    <div class="ext-svc-item">`,
    `      <div class="ext-svc-desc">${esc(svc.desc || '(无描述)')}</div>`,
    `      <div class="ext-svc-foot">${linkHtml}</div>`,
    `    </div>`,
    `  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 n0shake/Public-APIs</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapCategoriesCard({ categories, snapshot }) {
  const lines = categories.map(c =>
    `  <div class="ext-cat-item"><span class="ext-cat-name">${esc(c.name)}</span><span class="ext-cat-n">${c.n_items} 条</span></div>`
  ).join('\n');
  return [
    '<div class="ext-card ext-nokey-api" data-ext="n0shake-public-apis-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🔓 免 key API 分类</span>',
    `    <span class="ext-title">全部 ${categories.length} 个分类</span>`,
    '  </div>',
    `  <div class="ext-cat-list">\n${lines}\n  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 n0shake/Public-APIs</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

// ── 扩展实例 + 命令注册 ──

const ext = new PrisIrExt({
  id: 'n0shake-public-apis-promo',
  name: '免 key API 资源导航器',
  version: '0.1.0',
});

// nokeyapi.find — 关键词 + 分类双过滤
ext.registerCommand('nokeyapi.find', async (args) => {
  const query    = String(args.query || args.q || '').trim();
  const category = String(args.category || args.cat || '').trim();
  const limit    = Math.min(Math.max(parseInt(args.limit, 10) || 10, 1), 50);

  let pool = SERVICES;
  if (category) {
    const catMeta = CAT_BY_NAME.get(category) ||
                    CAT_BY_NAME.get([...CAT_BY_NAME.keys()].find(k => k.toLowerCase() === category.toLowerCase()) || '');
    if (!catMeta) {
      const fuzzy = [...CAT_BY_NAME.keys()].find(k => k.toLowerCase().includes(category.toLowerCase()));
      if (!fuzzy) {
        return { type: 'text', text: `❌ 分类不存在: "${category}"。可用 nokeyapi.list_categories 命令列出全部 ${CATEGORIES.length} 个分类。` };
      }
      pool = pool.filter(s => s.cat === fuzzy);
    } else {
      pool = pool.filter(s => s.cat === catMeta.name);
    }
  }
  let scored;
  if (!query) {
    scored = pool.map(s => ({ s, score: 0 }));
  } else {
    scored = pool
      .map(s => ({
        s,
        score: Math.max(
          scoreMatch(s.name, query),
          scoreMatch(s.desc, query) * 0.7,
          scoreMatch(s.cat, query) * 0.5,
        ),
      }))
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  if (scored.length === 0) {
    return { type: 'text', text: `🔍 没有找到匹配的 API。\n分类: ${category || '(全部)'}\n关键词: ${query || '(无)'}\n试试 nokeyapi.list_categories 看有哪些分类。` };
  }
  const top = scored.slice(0, limit).map(x => x.s);
  const html = wrapServiceCard({
    title: query ? `搜索:${query}` : (category ? `分类:${category}` : '免 key API 一览'),
    items: top,
    query,
    category,
    snapshot: META.snapshot_date,
  });
  return {
    type: 'card',
    html,
    meta: {
      query, category,
      total: scored.length, returned: top.length,
      item_names: top.map(s => s.name),
    },
  };
});

// nokeyapi.list_categories — 列分类
ext.registerCommand('nokeyapi.list_categories', async () => {
  const sorted = [...CATEGORIES].sort((a, b) => b.n_items - a.n_items);
  const html = wrapCategoriesCard({ categories: sorted, snapshot: META.snapshot_date });
  return {
    type: 'card',
    html,
    meta: {
      total: CATEGORIES.length,
      total_services: META.total_services,
      category_names: sorted.map(c => c.name),
    },
  };
});

// nokeyapi.detail — 查单个 API
ext.registerCommand('nokeyapi.detail', async (args) => {
  const name = String(args.name || '').trim();
  if (!name) {
    return { type: 'text', text: '❌ name 必填(如:"Spotify" 或 "iTunes Search")。' };
  }
  let svc = SVC_BY_NAME.get(name);
  if (!svc) {
    const lower = name.toLowerCase();
    const nameHits = SERVICES.filter(s => s.name.toLowerCase().includes(lower));
    const descHits = SERVICES.filter(s => !s.name.toLowerCase().includes(lower) && s.desc.toLowerCase().includes(lower));
    const fuzzy = [...nameHits, ...descHits].slice(0, 5);
    if (fuzzy.length === 0) {
      return { type: 'text', text: `🔍 没找到 "${name}"。试试 nokeyapi.find query="${name}" 模糊搜索,或 nokeyapi.list_categories 看有哪些分类。` };
    }
    if (fuzzy.length === 1) {
      svc = fuzzy[0];
    } else {
      const lines = fuzzy.map(s => `  - ${s.name}  (分类: ${s.cat})`);
      return { type: 'text', text: `🔍 "${name}" 匹配到多个:\n${lines.join('\n')}\n请用更精确的名字调用 nokeyapi.detail。` };
    }
  }
  const html = wrapDetailCard({ svc, snapshot: META.snapshot_date });
  return { type: 'card', html, meta: { name: svc.name, cat: svc.cat, url: svc.url, open_trial: svc.open_trial } };
});

// nokeyapi.random — 随机推荐
ext.registerCommand('nokeyapi.random', async (args) => {
  const category = String(args.category || args.cat || '').trim();
  let pool = SERVICES;
  if (category) {
    pool = pool.filter(s => s.cat === category);
    if (pool.length === 0) {
      return { type: 'text', text: `❌ 分类 "${category}" 没有条目。` };
    }
  }
  const idx = Math.floor(Math.random() * pool.length);
  const svc = pool[idx];
  const html = wrapDetailCard({ svc, snapshot: META.snapshot_date });
  return {
    type: 'card',
    html,
    meta: { name: svc.name, cat: svc.cat, url: svc.url, open_trial: svc.open_trial },
  };
});

// ════════════════════════════════════════════════════════════════════════
// 测试钩子 — PRISIR_EXT_NO_START=1 时不启动 RPC
// ════════════════════════════════════════════════════════════════════════

if (process.env.PRISIR_EXT_NO_START === '1') {
  module.exports = { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME };
}

if (process.env.PRISIR_EXT_NO_START !== '1' && require.main !== module) {
  // 作为依赖被 require 时不启动(避免污染测试 require)
}

if (process.env.PRISIR_EXT_NO_START !== '1' && require.main === module) {
  ext.start().catch((e) => {
    ext.log && ext.log('start_failed', { error: String(e) });
  });
}