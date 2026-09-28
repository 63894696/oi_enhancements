'use strict';

/**
 * free-for-dev-promo v0.1.0 — 免费资源导航器(Phase B)
 *
 * 数据来源:本地 data/{categories,services,meta}.json(从 ripienaar/free-for-dev 快照抽取,2026-09-27)
 *          原仓库 AGENTS.md 禁止 AI 贡献,本扩展只读快照,绝不联网回写。
 *
 * 命令:
 *   free.find           { query?, category?, limit?, level? }   关键词 + 分类双过滤
 *   free.list_categories {}                                   列所有分类 + 条目数
 *   free.detail         { name }                              查单个服务的完整描述
 *   free.random         { category? }                         随机推荐(给 LLM「给我一个 X」用)
 *
 * 输出:中英双语 UI 卡片(ui.inject.card),关键信息含分类、链接、免费额度。
 *       Phase C 接入主对话 EXEC 标记,LLM 自动识别「找免费 X」类需求。
 */

const path = require('path');
const fs = require('fs');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ════════════════════════════════════════════════════════════════════════
// 数据加载(启动时一次,1324 条 < 500KB,内存无压力)
// ════════════════════════════════════════════════════════════════════════

const DATA_DIR = path.join(__dirname, 'data');
function loadJson(name) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, name), 'utf-8'));
}
const CATEGORIES = loadJson('categories.json');   // 57 项
const SERVICES   = loadJson('services.json');      // 1324 项
const META       = loadJson('meta.json');          // 1 项

// 建索引:cat name → cat 元数据,用于快速反查 slug / n_items
const CAT_BY_NAME = new Map();
for (const c of CATEGORIES) CAT_BY_NAME.set(c.name, c);

// 建索引:name → service,用于 detail 命令精确查找(L1 直接命中,L2 含 ">" 也命中)
const SVC_BY_NAME = new Map();
for (const s of SERVICES) SVC_BY_NAME.set(s.name, s);

// ════════════════════════════════════════════════════════════════════════
// 工具函数
// ════════════════════════════════════════════════════════════════════════

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function scoreMatch(haystack, query) {
  // 命中优先级:name > desc > cat,大小写不敏感
  const h = haystack.toLowerCase();
  const q = query.toLowerCase();
  if (h === q) return 100;             // 完全相等
  if (h.startsWith(q)) return 80;      // 起始命中
  const terms = q.split(/\s+/).filter(Boolean);
  if (terms.length === 1) return h.includes(q) ? 50 : 0;
  // 多 term 全包含
  return terms.every(t => h.includes(t)) ? 40 : 0;
}

// 兜底 link:L2 无自身 url 时用 parent_url
function getLink(svc) {
  return svc.url || svc.parent_url || '';
}

// ════════════════════════════════════════════════════════════════════════
// 卡片 HTML 渲染
// ════════════════════════════════════════════════════════════════════════

function wrapServiceCard({ title, items, query, category, snapshot }) {
  const catPills = category
    ? `<span class="ext-chip ext-chip-accent">分类: ${esc(category)}</span>`
    : '';
  const queryPill = query
    ? `<span class="ext-chip">关键词: ${esc(query)}</span>`
    : '';
  const listHtml = items.map((s, i) => {
    const link = getLink(s);
    const linkHtml = link
      ? `<a href="${esc(link)}" target="_blank" rel="noopener" class="ext-link">${esc(link)}</a>`
      : `<span class="ext-link ext-link-muted">(无独立链接)</span>`;
    return [
      `  <div class="ext-svc-item">`,
      `    <div class="ext-svc-head">`,
      `      <span class="ext-svc-num">${i + 1}</span>`,
      `      <span class="ext-svc-name">${esc(s.name)}</span>`,
      `      <span class="ext-svc-cat">${esc(s.cat)}</span>`,
      `    </div>`,
      `    <div class="ext-svc-desc">${esc(s.desc || '(无描述)')}</div>`,
      `    <div class="ext-svc-foot">${linkHtml}</div>`,
      `  </div>`,
    ].join('\n');
  }).join('\n');

  return [
    '<div class="ext-card ext-free" data-ext="free-for-dev-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🎁 免费资源</span>',
    `    <span class="ext-title">${esc(title)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">${catPills} ${queryPill}</div>`,
    `  <div class="ext-svc-list">${listHtml}</div>`,
    `  <div class="ext-card-foot">`,
    `    <span>共 ${items.length} 条 · 数据快照 ${esc(snapshot)} · 来自 ripienaar/free-for-dev</span>`,
    `    <button class="ext-copy-btn" data-copy-target="list">复制清单</button>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapDetailCard({ svc, snapshot }) {
  const link = getLink(svc);
  const linkHtml = link
    ? `<a href="${esc(link)}" target="_blank" rel="noopener" class="ext-link">${esc(link)}</a>`
    : `<span class="ext-link ext-link-muted">(无独立链接)</span>`;
  const parentHtml = svc.parent
    ? `<div class="ext-svc-foot">所属:${esc(svc.parent)}</div>`
    : '';
  return [
    '<div class="ext-card ext-free" data-ext="free-for-dev-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🎁 免费资源详情</span>',
    `    <span class="ext-title">${esc(svc.name)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">`,
    `    <span class="ext-chip ext-chip-accent">分类: ${esc(svc.cat)}</span>`,
    `    <span class="ext-chip">层级: ${svc.level === 1 ? '独立' : '嵌套'}</span>`,
    `  </div>`,
    `  <div class="ext-svc-list">`,
    `    <div class="ext-svc-item">`,
    `      <div class="ext-svc-desc">${esc(svc.desc || '(无描述)')}</div>`,
    `      <div class="ext-svc-foot">${linkHtml}</div>`,
    `      ${parentHtml}`,
    `    </div>`,
    `  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 ripienaar/free-for-dev</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapCategoriesCard({ categories, snapshot }) {
  const lines = categories.map(c =>
    `  <div class="ext-cat-item"><span class="ext-cat-name">${esc(c.name)}</span><span class="ext-cat-n">${c.n_items} 条</span></div>`
  ).join('\n');
  return [
    '<div class="ext-card ext-free" data-ext="free-for-dev-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🎁 免费资源分类</span>',
    `    <span class="ext-title">全部 ${categories.length} 个分类</span>`,
    '  </div>',
    `  <div class="ext-cat-list">\n${lines}\n  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 ripienaar/free-for-dev</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

// ════════════════════════════════════════════════════════════════════════
// 扩展实例 + 命令注册
// ════════════════════════════════════════════════════════════════════════

const ext = new PrisIrExt({
  id: 'free-for-dev-promo',
  name: '免费资源导航器',
  version: '0.1.0',
});

// free.find — 关键词 + 分类双过滤
ext.registerCommand('free.find', async (args, ctx) => {
  const query    = String(args.query || args.q || '').trim();
  const category = String(args.category || args.cat || '').trim();
  const limit    = Math.min(Math.max(parseInt(args.limit, 10) || 10, 1), 50);
  const level    = args.level != null ? parseInt(args.level, 10) : null;

  let pool = SERVICES;
  // 按分类过滤
  if (category) {
    const catMeta = CAT_BY_NAME.get(category) ||
                    CAT_BY_NAME.get([...CAT_BY_NAME.keys()].find(k => k.toLowerCase() === category.toLowerCase()) || '');
    if (!catMeta) {
      // 模糊匹配分类名
      const fuzzy = [...CAT_BY_NAME.keys()].find(k => k.toLowerCase().includes(category.toLowerCase()));
      if (!fuzzy) {
        return { type: 'text', text: `❌ 分类不存在: "${category}"。可用 free.list_categories 命令列出全部 57 个分类。` };
      }
      pool = pool.filter(s => s.cat === fuzzy);
    } else {
      pool = pool.filter(s => s.cat === catMeta.name);
    }
  }
  // 按层级过滤
  if (level === 1 || level === 2) {
    pool = pool.filter(s => s.level === level);
  }
  // 关键词打分
  let scored;
  if (!query) {
    // 无 query → 按 cat 名排序后取前 limit
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
    return { type: 'text', text: `🔍 没有找到匹配的资源。\n分类: ${category || '(全部)'}\n关键词: ${query || '(无)'}\n试试 free.list_categories 看有哪些分类。` };
  }
  const top = scored.slice(0, limit).map(x => x.s);
  const html = wrapServiceCard({
    title: query ? `搜索:${query}` : (category ? `分类:${category}` : '免费资源一览'),
    items: top,
    query,
    category,
    snapshot: META.snapshot_date,
  });
  return {
    type: 'card',
    html,
    meta: {
      query, category, level,
      total: scored.length, returned: top.length,
      item_names: top.map(s => s.name),
    },
  };
});

// free.list_categories — 列分类
ext.registerCommand('free.list_categories', async () => {
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

// free.detail — 查单个服务
ext.registerCommand('free.detail', async (args) => {
  const name = String(args.name || '').trim();
  if (!name) {
    return { type: 'text', text: '❌ name 必填(如:"GitHub" 或 "Amazon Web Services > CloudFront")。' };
  }
  let svc = SVC_BY_NAME.get(name);
  if (!svc) {
    // 模糊:按 name 或 desc 含 query 的前 5 个,按 name 命中优先
    const lower = name.toLowerCase();
    const nameHits = SERVICES.filter(s => s.name.toLowerCase().includes(lower));
    const descHits = SERVICES.filter(s => !s.name.toLowerCase().includes(lower) && s.desc.toLowerCase().includes(lower));
    const fuzzy = [...nameHits, ...descHits].slice(0, 5);
    if (fuzzy.length === 0) {
      return { type: 'text', text: `🔍 没找到 "${name}"。试试 free.find query="${name}" 模糊搜索,或 free.list_categories 看有哪些分类。` };
    }
    if (fuzzy.length === 1) {
      svc = fuzzy[0];
    } else {
      const lines = fuzzy.map(s => `  - ${s.name}  (分类: ${s.cat})`);
      return { type: 'text', text: `🔍 "${name}" 匹配到多个:\n${lines.join('\n')}\n请用更精确的名字调用 free.detail。` };
    }
  }
  const html = wrapDetailCard({ svc, snapshot: META.snapshot_date });
  return { type: 'card', html, meta: { name: svc.name, cat: svc.cat, level: svc.level, url: getLink(svc) } };
});

// free.random — 随机推荐
ext.registerCommand('free.random', async (args) => {
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
    meta: { name: svc.name, cat: svc.cat, level: svc.level, url: getLink(svc) },
  };
});

// ════════════════════════════════════════════════════════════════════════
// 测试钩子 — PRISIR_EXT_NO_START=1 时不启动 RPC
// ════════════════════════════════════════════════════════════════════════

if (process.env.PRISIR_EXT_NO_START === '1') {
  module.exports = { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME };
}

// 真启动时跑 start() — 这是 SDK 约定的入口
if (process.env.PRISIR_EXT_NO_START !== '1' && require.main !== module) {
  // 作为依赖被 require 时不启动(避免污染测试 require)
}

// 真启动路径(被 main 进程 spawn 后)
if (process.env.PRISIR_EXT_NO_START !== '1' && require.main === module) {
  ext.start().catch((e) => {
    // 启动失败不要崩溃进程
    ext.log && ext.log('start_failed', { error: String(e) });
  });
}
