'use strict';

/**
 * awesome-selfhosted-promo v0.1.0 — 自部署软件导航器(Phase B)
 *
 * 数据来源:本地 data/{categories,services,meta}.json(从 awesome-selfhosted/awesome-selfhosted 快照抽取,2026-10-02)
 *          原仓库无 AGENTS.md 限制,本扩展只读快照,绝不联网回写。
 *
 * 命令(命名空间 selfhost.*,避免与 free.* 冲突):
 *   selfhost.find           { query?, category?, license?, language?, limit? }   多过滤
 *   selfhost.list_categories {}                                                列所有分类 + 条目数
 *   selfhost.detail         { name }                                           查单个服务的完整信息
 *   selfhost.random         { category? }                                      随机推荐(给 LLM「给我一个 X」用)
 *
 * 输出:中文 UI 卡片(ui.inject.card),关键信息含分类、链接、证书/语言/不维护三档 chip。
 *       Phase C 接入主对话 EXEC 标记,LLM 自动识别「自建 Nextcloud / Joplin / Bitwarden 替代 SaaS」类需求。
 */

const path = require('path');
const fs = require('fs');
const { PrisIrExt } = require('@prisir/extension-sdk');

// ════════════════════════════════════════════════════════════════════════
// 数据加载(启动时一次,1260 条 < 800KB,内存无压力)
// ════════════════════════════════════════════════════════════════════════

const DATA_DIR = path.join(__dirname, 'data');
function loadJson(name) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, name), 'utf-8'));
}
const CATEGORIES = loadJson('categories.json');   // 95 项
const SERVICES   = loadJson('services.json');      // 1260 项
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
  // 命中优先级:name > licenses > languages > cat,大小写不敏感
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

function renderLicenseChips(licenses) {
  if (!licenses || licenses.length === 0) {
    return '<span class="ext-chip ext-chip-muted">证书: 未知</span>';
  }
  return licenses.map(l =>
    '<span class="ext-chip ext-chip-accent">证书: ' + esc(l) + '</span>'
  ).join(' ');
}

function renderLanguageChips(languages) {
  if (!languages || languages.length === 0) {
    return '';
  }
  // 显示前 3 个,过多不堆砌
  const display = languages.slice(0, 3);
  const more = languages.length > 3 ? ' +' + (languages.length - 3) : '';
  return display.map(l =>
    '<span class="ext-chip ext-chip-muted">语言: ' + esc(l) + esc(more) + '</span>'
  ).join(' ');
}

function renderWarningChip(hasWarning) {
  if (!hasWarning) return '';
  return '<span class="ext-chip ext-chip-warn">⚠ 不维护</span>';
}

function wrapServiceCard({ title, items, query, category, license, language, snapshot }) {
  const catPill = category
    ? '<span class="ext-chip ext-chip-accent">分类: ' + esc(category) + '</span>'
    : '';
  const queryPill = query
    ? '<span class="ext-chip">关键词: ' + esc(query) + '</span>'
    : '';
  const licPill = license
    ? '<span class="ext-chip">证书: ' + esc(license) + '</span>'
    : '';
  const langPill = language
    ? '<span class="ext-chip">语言: ' + esc(language) + '</span>'
    : '';
  const listHtml = items.map((s, i) => {
    const linkHtml = s.url
      ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener" class="ext-link">' + esc(s.url) + '</a>'
      : '<span class="ext-link ext-link-muted">(无链接)</span>';
    const scLink = s.source_code_url
      ? '<a href="' + esc(s.source_code_url) + '" target="_blank" rel="noopener" class="ext-link ext-link-sc">源码: ' + esc(s.source_code_url) + '</a>'
      : '';
    const chips = [
      renderLicenseChips(s.licenses),
      renderLanguageChips(s.languages),
      renderWarningChip(s.has_warning),
    ].filter(Boolean).join(' ');
    return [
      '  <div class="ext-svc-item">',
      '    <div class="ext-svc-head">',
      '      <span class="ext-svc-num">' + (i + 1) + '</span>',
      '      <span class="ext-svc-name">' + esc(s.name) + '</span>',
      '      <span class="ext-svc-cat">' + esc(s.cat) + '</span>',
      '    </div>',
      '    <div class="ext-svc-chips">' + chips + '</div>',
      '    <div class="ext-svc-foot">' + linkHtml + (scLink ? '<br>' + scLink : '') + '</div>',
      '  </div>',
    ].join('\n');
  }).join('\n');

  return [
    '<div class="ext-card ext-selfhosted" data-ext="awesome-selfhosted-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🏠 自部署</span>',
    '    <span class="ext-title">' + esc(title) + '</span>',
    '  </div>',
    '  <div class="ext-card-summary">' + catPill + ' ' + licPill + ' ' + langPill + ' ' + queryPill + '</div>',
    '  <div class="ext-svc-list">' + listHtml + '</div>',
    '  <div class="ext-card-foot">',
    '    <span>共 ' + items.length + ' 条 · 数据快照 ' + esc(snapshot) + ' · 来自 awesome-selfhosted/awesome-selfhosted</span>',
    '    <button class="ext-copy-btn" data-copy-target="list">复制清单</button>',
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapDetailCard({ svc, snapshot }) {
  const linkHtml = svc.url
    ? '<a href="' + esc(svc.url) + '" target="_blank" rel="noopener" class="ext-link">' + esc(svc.url) + '</a>'
    : '<span class="ext-link ext-link-muted">(无链接)</span>';
  const scLink = svc.source_code_url
    ? '<a href="' + esc(svc.source_code_url) + '" target="_blank" rel="noopener" class="ext-link ext-link-sc">源码: ' + esc(svc.source_code_url) + '</a>'
    : '';
  const chips = [
    renderLicenseChips(svc.licenses),
    renderLanguageChips(svc.languages),
    renderWarningChip(svc.has_warning),
  ].filter(Boolean).join(' ');
  return [
    '<div class="ext-card ext-selfhosted" data-ext="awesome-selfhosted-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🏠 自部署详情</span>',
    '    <span class="ext-title">' + esc(svc.name) + '</span>',
    '  </div>',
    '  <div class="ext-card-summary">',
    '    <span class="ext-chip ext-chip-accent">分类: ' + esc(svc.cat) + '</span>',
    '    ' + chips,
    '  </div>',
    '  <div class="ext-svc-list">',
    '    <div class="ext-svc-item">',
    '      <div class="ext-svc-foot">' + linkHtml + (scLink ? '<br>' + scLink : '') + '</div>',
    '    </div>',
    '  </div>',
    '  <div class="ext-card-foot">',
    '    <span>数据快照 ' + esc(snapshot) + ' · 来自 awesome-selfhosted/awesome-selfhosted</span>',
    '  </div>',
    '</div>',
  ].join('\n');
}

function wrapCategoriesCard({ categories, snapshot }) {
  const lines = categories.map(c =>
    '  <div class="ext-cat-item"><span class="ext-cat-name">' + esc(c.name) + '</span><span class="ext-cat-n">' + c.n_items + ' 条</span></div>'
  ).join('\n');
  return [
    '<div class="ext-card ext-selfhosted" data-ext="awesome-selfhosted-promo">',
    '  <div class="ext-card-head">',
    '    <span class="ext-badge">🏠 自部署分类</span>',
    '    <span class="ext-title">全部 ' + categories.length + ' 个分类</span>',
    '  </div>',
    '  <div class="ext-cat-list">\n' + lines + '\n  </div>',
    '  <div class="ext-card-foot">',
    '    <span>数据快照 ' + esc(snapshot) + ' · 来自 awesome-selfhosted/awesome-selfhosted</span>',
    '  </div>',
    '</div>',
  ].join('\n');
}

// ════════════════════════════════════════════════════════════════════════
// 扩展实例 + 命令注册
// ════════════════════════════════════════════════════════════════════════

const ext = new PrisIrExt({
  id: 'awesome-selfhosted-promo',
  name: '自部署软件导航器',
  version: '0.1.0',
});

// selfhost.find — 关键词 + 分类 + 证书 + 语言 四过滤
ext.registerCommand('selfhost.find', async (args, ctx) => {
  const query    = String(args.query || args.q || '').trim();
  const category = String(args.category || args.cat || '').trim();
  const license  = String(args.license || args.lic || '').trim();
  const language = String(args.language || args.lang || '').trim();
  const limit    = Math.min(Math.max(parseInt(args.limit, 10) || 10, 1), 50);

  let pool = SERVICES;

  // 按分类过滤
  if (category) {
    const catMeta = CAT_BY_NAME.get(category) ||
                    CAT_BY_NAME.get([...CAT_BY_NAME.keys()].find(k => k.toLowerCase() === category.toLowerCase()) || '');
    if (!catMeta) {
      const fuzzy = [...CAT_BY_NAME.keys()].find(k => k.toLowerCase().includes(category.toLowerCase()));
      if (!fuzzy) {
        return { type: 'text', text: '❌ 分类不存在: "' + category + '"。可用 selfhost.list_categories 命令列出全部 ' + CATEGORIES.length + ' 个分类。' };
      }
      pool = pool.filter(s => s.cat === fuzzy);
    } else {
      pool = pool.filter(s => s.cat === catMeta.name);
    }
  }

  // 按证书过滤
  if (license) {
    pool = pool.filter(s => (s.licenses || []).some(l => l.toLowerCase() === license.toLowerCase()));
  }

  // 按语言过滤
  if (language) {
    pool = pool.filter(s => (s.languages || []).some(l => l.toLowerCase() === language.toLowerCase()));
  }

  // 关键词打分
  let scored;
  if (!query) {
    scored = pool.map(s => ({ s, score: 0 }));
  } else {
    scored = pool
      .map(s => {
        const hay = s.name + ' ' + s.cat + ' ' + (s.languages || []).join(' ') + ' ' + (s.licenses || []).join(' ');
        return {
          s,
          score: Math.max(
            scoreMatch(s.name, query),
            scoreMatch(s.cat, query) * 0.6,
            scoreMatch((s.languages || []).join(' '), query) * 0.5,
            scoreMatch((s.licenses || []).join(' '), query) * 0.4,
            scoreMatch(hay, query) * 0.3,
          ),
        };
      })
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  if (scored.length === 0) {
    return { type: 'text', text: '🔍 没有找到匹配的自部署软件。\n分类: ' + (category || '(全部)') + '\n证书: ' + (license || '(无)') + '\n语言: ' + (language || '(无)') + '\n关键词: ' + (query || '(无)') + '\n试试 selfhost.list_categories 看有哪些分类。' };
  }
  const top = scored.slice(0, limit).map(x => x.s);
  const titleParts = [];
  if (query) titleParts.push(query);
  if (category) titleParts.push('分类:' + category);
  if (license) titleParts.push('证书:' + license);
  if (language) titleParts.push('语言:' + language);
  const title = titleParts.length ? titleParts.join(' · ') : '自部署软件一览';
  const html = wrapServiceCard({
    title: title,
    items: top,
    query: query,
    category: category,
    license: license,
    language: language,
    snapshot: META.snapshot_date,
  });
  return {
    type: 'card',
    html: html,
    meta: {
      query: query, category: category, license: license, language: language,
      total: scored.length, returned: top.length,
      item_names: top.map(s => s.name),
    },
  };
});

// selfhost.list_categories — 列分类
ext.registerCommand('selfhost.list_categories', async () => {
  const sorted = [...CATEGORIES].sort((a, b) => b.n_items - a.n_items);
  const html = wrapCategoriesCard({ categories: sorted, snapshot: META.snapshot_date });
  return {
    type: 'card',
    html: html,
    meta: {
      total: CATEGORIES.length,
      total_services: META.total_services,
      category_names: sorted.map(c => c.name),
    },
  };
});

// selfhost.detail — 查单个服务
ext.registerCommand('selfhost.detail', async (args) => {
  const name = String(args.name || '').trim();
  if (!name) {
    return { type: 'text', text: '❌ name 必填(如:"Nextcloud" 或 "Joplin")。' };
  }
  let svc = SVC_BY_NAME.get(name);
  if (!svc) {
    const lower = name.toLowerCase();
    const nameHits = SERVICES.filter(s => s.name.toLowerCase().includes(lower));
    const catHits = SERVICES.filter(s => !s.name.toLowerCase().includes(lower) && s.cat.toLowerCase().includes(lower));
    const fuzzy = [...nameHits, ...catHits].slice(0, 5);
    if (fuzzy.length === 0) {
      return { type: 'text', text: '🔍 没找到 "' + name + '"。试试 selfhost.find query="' + name + '" 模糊搜索,或 selfhost.list_categories 看有哪些分类。' };
    }
    if (fuzzy.length === 1) {
      svc = fuzzy[0];
    } else {
      const lines = fuzzy.map(s => '  - ' + s.name + '  (分类: ' + s.cat + ')');
      return { type: 'text', text: '🔍 "' + name + '" 匹配到多个:\n' + lines.join('\n') + '\n请用更精确的名字调用 selfhost.detail。' };
    }
  }
  const html = wrapDetailCard({ svc: svc, snapshot: META.snapshot_date });
  return { type: 'card', html: html, meta: { name: svc.name, cat: svc.cat, url: svc.url, licenses: svc.licenses, languages: svc.languages, has_warning: svc.has_warning } };
});

// selfhost.random — 随机推荐
ext.registerCommand('selfhost.random', async (args) => {
  const category = String(args.category || args.cat || '').trim();
  let pool = SERVICES;
  if (category) {
    pool = pool.filter(s => s.cat === category);
    if (pool.length === 0) {
      return { type: 'text', text: '❌ 分类 "' + category + '" 没有条目。' };
    }
  }
  const idx = Math.floor(Math.random() * pool.length);
  const svc = pool[idx];
  const html = wrapDetailCard({ svc: svc, snapshot: META.snapshot_date });
  return {
    type: 'card',
    html: html,
    meta: { name: svc.name, cat: svc.cat, url: svc.url },
  };
});

// ════════════════════════════════════════════════════════════════════════
// 测试钩子 — PRISIR_EXT_NO_START=1 时不启动 RPC
// ════════════════════════════════════════════════════════════════════════

if (process.env.PRISIR_EXT_NO_START === '1') {
  module.exports = { ext: ext, CATEGORIES: CATEGORIES, SERVICES: SERVICES, META: META, SVC_BY_NAME: SVC_BY_NAME, CAT_BY_NAME: CAT_BY_NAME };
}

// 作为依赖被 require 时不启动(避免污染测试 require)
if (process.env.PRISIR_EXT_NO_START !== '1' && require.main !== module) {
  // no-op
}

// 真启动路径(被 main 进程 spawn 后)
if (process.env.PRISIR_EXT_NO_START !== '1' && require.main === module) {
  ext.start().catch((e) => {
    ext.log && ext.log('start_failed', { error: String(e) });
  });
}
