'use strict';

/**
 * public-apis-cn-promo v0.1.0 — 国内 API 资源导航器(Phase B)
 *
 * 数据来源:本地 data/{categories,services,meta}.json(从 llf007/public-apis-cn 快照抽取,2026-10-02)
 *          原仓库无 AGENTS.md 限制,本扩展只读快照,绝不联网回写。
 *
 * 命令(命名空间 api_cn.*):
 *   api_cn.find           { query?, category?, limit? }   关键词 + 分类双过滤
 *   api_cn.list_categories {}                            列所有分类 + 条目数
 *   api_cn.detail         { name }                       查单个 API 的完整描述
 *   api_cn.random         { category? }                  随机推荐
 *
 * 输出:中文 UI 卡片(ui.inject.card),关键信息含分类、链接、Auth/HTTPS 两档 chip。
 *       Phase C 接入主对话 EXEC 标记,LLM 自动识别「找国内 API / 国内可访问 API」类需求。
 *       与 public-apis-promo 是平行扩展,本扩展强调「国内可访问」+「中文描述」。
 */

const path = require('path');
const fs = require('fs');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ════════════════════════════════════════════════════════════════════════
// 数据加载(启动时一次,1493 条 < 600KB,内存无压力)
// ════════════════════════════════════════════════════════════════════════

const DATA_DIR = path.join(__dirname, 'data');
function loadJson(name) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, name), 'utf-8'));
}
const CATEGORIES = loadJson('categories.json');   // 54 项(中文名)
const SERVICES   = loadJson('services.json');      // 1493 项
const META       = loadJson('meta.json');          // 1 项

// 建索引:cat name → cat 元数据,用于快速反查 slug / n_items
const CAT_BY_NAME = new Map();
for (const c of CATEGORIES) CAT_BY_NAME.set(c.name, c);

// 建索引:name → service,用于 detail 命令精确查找
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
  // 命中优先级:name > desc > cat,大小写不敏感(中文场景大小写无意义,保持字符串原样)
  const h = String(haystack || '').toLowerCase();
  const q = String(query || '').toLowerCase();
  if (h === q) return 100;             // 完全相等
  if (h.startsWith(q)) return 80;      // 起始命中
  const terms = q.split(/\s+/).filter(Boolean);
  if (terms.length === 1) return h.includes(q) ? 50 : 0;
  // 多 term 全包含
  return terms.every(t => h.includes(t)) ? 40 : 0;
}

// ════════════════════════════════════════════════════════════════════════
// 卡片 HTML 渲染
// ════════════════════════════════════════════════════════════════════════

function renderAuthChip(auth) {
  // public-apis-cn auth 字段:是 / 否 / apiKey / OAuth / 无 / User-Agent / 未知
  // 「否」或「无」= 免 auth(亮点);「是」/「apiKey」/「OAuth」= 需注册
  const a = a_str(auth);
  const cls = (a === '否' || a === '无')
    ? 'ext-chip ext-chip-good'
    : (a === 'apiKey' || a === 'OAuth' || a === 'User-Agent' || a === '是')
      ? 'ext-chip ext-chip-warn'
      : 'ext-chip ext-chip-muted';
  return `<span class="${cls}">认证: ${esc(auth)}</span>`;
}

function a_str(s) {
  return String(s || '').trim();
}

function renderYesNoChip(label, value) {
  // 中文场景:是=good, 否=warn, 未知=muted
  const v = a_str(value);
  const cls = v === '是'
    ? 'ext-chip ext-chip-good'
    : v === '否'
      ? 'ext-chip ext-chip-warn'
      : 'ext-chip ext-chip-muted';
  return `<span class="${cls}">${esc(label)}: ${esc(value)}</span>`;
}

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
    const chips = [
      renderAuthChip(s.auth),
      renderYesNoChip('HTTPS', s.https),
    ].join(' ');
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
    '<div class="ext-card ext-api-cn" data-ext="public-apis-cn-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🇨🇳 国内 API</span>',
    `    <span class="ext-title">${esc(title)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">${catPills} ${queryPill}</div>`,
    `  <div class="ext-svc-list">${listHtml}</div>`,
    `  <div class="ext-card-foot">`,
    `    <span>共 ${items.length} 条 · 数据快照 ${esc(snapshot)} · 来自 llf007/public-apis-cn</span>`,
    `    <button class="ext-copy-btn" data-copy-target="list">复制清单</button>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapDetailCard({ svc, snapshot }) {
  const linkHtml = svc.url
    ? `<a href="${esc(svc.url)}" target="_blank" rel="noopener" class="ext-link">${esc(svc.url)}</a>`
    : `<span class="ext-link ext-link-muted">(无链接)</span>`;
  const chips = [
    renderAuthChip(svc.auth),
    renderYesNoChip('HTTPS', svc.https),
  ].join(' ');
  return [
    '<div class="ext-card ext-api-cn" data-ext="public-apis-cn-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🇨🇳 国内 API 详情</span>',
    `    <span class="ext-title">${esc(svc.name)}</span>`,
    '  </div>',
    `  <div class="ext-card-summary">`,
    `    <span class="ext-chip ext-chip-accent">分类: ${esc(svc.cat)}</span>`,
    `    ${chips}`,
    `  </div>`,
    `  <div class="ext-svc-list">`,
    `    <div class="ext-svc-item">`,
    `      <div class="ext-svc-desc">${esc(svc.desc || '(无描述)')}</div>`,
    `      <div class="ext-svc-foot">${linkHtml}</div>`,
    `    </div>`,
    `  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 llf007/public-apis-cn</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapCategoriesCard({ categories, snapshot }) {
  const lines = categories.map(c =>
    `  <div class="ext-cat-item"><span class="ext-cat-name">${esc(c.name)}</span><span class="ext-cat-n">${c.n_items} 条</span></div>`
  ).join('\n');
  return [
    '<div class="ext-card ext-api-cn" data-ext="public-apis-cn-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🇨🇳 国内 API 分类</span>',
    `    <span class="ext-title">全部 ${categories.length} 个分类</span>`,
    '  </div>',
    `  <div class="ext-cat-list">\n${lines}\n  </div>`,
    `  <div class="ext-card-foot">`,
    `    <span>数据快照 ${esc(snapshot)} · 来自 llf007/public-apis-cn</span>`,
    '  </div>',
    '</div>',
  ].join('\n');
}

// ════════════════════════════════════════════════════════════════════════
// 扩展实例 + 命令注册
// ════════════════════════════════════════════════════════════════════════

const ext = new PrisIrExt({
  id: 'public-apis-cn-promo',
  name: '国内 API 资源导航器',
  version: '0.1.0',
});

// api_cn.find — 关键词 + 分类双过滤
ext.registerCommand('api_cn.find', async (args, ctx) => {
  const query    = String(args.query || args.q || '').trim();
  const category = String(args.category || args.cat || '').trim();
  const limit    = Math.min(Math.max(parseInt(args.limit, 10) || 10, 1), 50);

  let pool = SERVICES;
  // 按分类过滤(中文场景直接精确匹配 cat 名;若用户给简化中文也支持原值模糊)
  if (category) {
    let catMeta = CAT_BY_NAME.get(category);
    if (!catMeta) {
      // 模糊:分类包含子串
      const fuzzy = [...CAT_BY_NAME.keys()].find(k => k.includes(category));
      if (!fuzzy) {
        return { type: 'text', text: `❌ 分类不存在: "${category}"。可用 api_cn.list_categories 命令列出全部 ${CATEGORIES.length} 个分类。` };
      }
      pool = pool.filter(s => s.cat === fuzzy);
    } else {
      pool = pool.filter(s => s.cat === catMeta.name);
    }
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
    return { type: 'text', text: `🔍 没有找到匹配的 API。\n分类: ${category || '(全部)'}\n关键词: ${query || '(无)'}\n试试 api_cn.list_categories 看有哪些分类。` };
  }
  const top = scored.slice(0, limit).map(x => x.s);
  const html = wrapServiceCard({
    title: query ? `搜索:${query}` : (category ? `分类:${category}` : '国内 API 一览'),
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

// api_cn.list_categories — 列分类
ext.registerCommand('api_cn.list_categories', async () => {
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

// api_cn.detail — 查单个 API
ext.registerCommand('api_cn.detail', async (args) => {
  const name = String(args.name || '').trim();
  if (!name) {
    return { type: 'text', text: '❌ name 必填(如:"高德地图" 或 "心知天气")。' };
  }
  let svc = SVC_BY_NAME.get(name);
  if (!svc) {
    // 模糊:按 name 或 desc 含 query 的前 5 个,按 name 命中优先
    const nameHits = SERVICES.filter(s => s.name.includes(name));
    const descHits = SERVICES.filter(s => !s.name.includes(name) && s.desc.includes(name));
    const fuzzy = [...nameHits, ...descHits].slice(0, 5);
    if (fuzzy.length === 0) {
      return { type: 'text', text: `🔍 没找到 "${name}"。试试 api_cn.find query="${name}" 模糊搜索,或 api_cn.list_categories 看有哪些分类。` };
    }
    if (fuzzy.length === 1) {
      svc = fuzzy[0];
    } else {
      const lines = fuzzy.map(s => `  - ${s.name}  (分类: ${s.cat})`);
      return { type: 'text', text: `🔍 "${name}" 匹配到多个:\n${lines.join('\n')}\n请用更精确的名字调用 api_cn.detail。` };
    }
  }
  const html = wrapDetailCard({ svc, snapshot: META.snapshot_date });
  return { type: 'card', html, meta: { name: svc.name, cat: svc.cat, url: svc.url, auth: svc.auth, https: svc.https } };
});

// api_cn.random — 随机推荐
ext.registerCommand('api_cn.random', async (args) => {
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
    meta: { name: svc.name, cat: svc.cat, url: svc.url },
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
