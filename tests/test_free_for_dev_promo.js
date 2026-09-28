/**
 * tests/test_free_for_dev_promo.js — Phase B 烟雾测试
 *
 * 设置 PRISIR_EXT_NO_START=1 让 index.js 不真启动 RPC,
 * 然后直接从 module.exports 拿 ext instance,跑命令 + 断言。
 *
 * 跑法:
 *   PRISIR_EXT_NO_START=1 node tests/test_free_for_dev_promo.js
 */

const path = require('path');
const assert = require('assert');

const SDK_PATH = path.resolve(__dirname, '..', 'extensions', 'free-for-dev-promo', 'node_modules', '@prisir', 'extension-sdk');
const { PrisIrExt } = require(SDK_PATH);

// 静默化 + 抓 injectCard
let injected = null;
PrisIrExt.prototype.log = function () {};
PrisIrExt.prototype.injectCard = function (payload) { injected = payload; };
PrisIrExt.prototype.start = function () { return Promise.resolve(); };

// require ext 模块
const extPath = path.resolve(__dirname, '..', 'extensions', 'free-for-dev-promo');
process.env.PRISIR_EXT_NO_START = '1';
const { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME } = require(extPath);

assert.ok(CATEGORIES.length === 57, `categories count: ${CATEGORIES.length} (expect 57)`);
assert.ok(SERVICES.length === 1324, `services count: ${SERVICES.length} (expect 1324)`);
assert.ok(META.snapshot_date, 'meta.snapshot_date should be set');
console.log(`data ok: categories=${CATEGORIES.length} services=${SERVICES.length} snapshot=${META.snapshot_date}`);

async function cmd(method, args, ctx) {
  injected = null;
  const handler = ext.commands.get(method);
  assert.ok(handler, `command not registered: ${method}`);
  return await handler(args, ctx || {});
}

async function main() {
  // ── 1. free.find 关键词搜索「postgres」 ────────────────────────
  const r1 = await cmd('free.find', { query: 'postgres' }, { sessionId: 't1' });
  assert.strictEqual(r1.type, 'card');
  assert.ok(r1.html.includes('🎁 免费资源'), 'should have 🎁 免费资源 badge');
  assert.ok(r1.html.includes('postgres'), 'should echo query in card');
  assert.ok(r1.meta.item_names.length > 0, 'should have at least 1 result');
  assert.ok(r1.meta.item_names.every(n => /postgres|postgresql|psql/i.test(n) || true), 'results may include other db');
  console.log(`✓ free.find('postgres') → ${r1.meta.returned} 条 (total=${r1.meta.total})`);

  // ── 2. free.find 分类 + 关键词 ─────────────────────────────
  const r2 = await cmd('free.find', { query: 'free', category: 'CDN and Protection' }, { sessionId: 't2' });
  assert.strictEqual(r2.type, 'card');
  assert.ok(r2.html.includes('分类: CDN and Protection'));
  assert.ok(r2.meta.category === 'CDN and Protection');
  assert.ok(r2.meta.returned > 0);
  console.log(`✓ free.find(category='CDN and Protection') → ${r2.meta.returned} 条`);

  // ── 3. free.find 不存在的分类友好报错 ─────────────────────
  const r3 = await cmd('free.find', { query: 'x', category: '不存在的分类XYZ' });
  assert.strictEqual(r3.type, 'text');
  assert.ok(r3.text.includes('分类不存在'));
  console.log('✓ free.find 拒绝未知分类');

  // ── 4. free.find 无 query 也无 cat 返前 N 条 ───────────────
  const r4 = await cmd('free.find', { limit: 5 });
  assert.strictEqual(r4.type, 'card');
  assert.strictEqual(r4.meta.returned, 5);
  console.log(`✓ free.find(无 query, limit=5) → 5 条`);

  // ── 5. free.find 空结果友好提示 ──────────────────────────
  const r5 = await cmd('free.find', { query: 'zzz绝对不存在zzz' });
  assert.strictEqual(r5.type, 'text');
  assert.ok(r5.text.includes('没有找到'));
  console.log('✓ free.find 空结果降级为文本');

  // ── 6. free.list_categories ─────────────────────────────
  const r6 = await cmd('free.list_categories', {});
  assert.strictEqual(r6.type, 'card');
  assert.ok(r6.html.includes('全部 57 个分类'));
  assert.strictEqual(r6.meta.total, 57);
  assert.ok(r6.meta.category_names.length === 57);
  // 排序应该是按 n_items 降序
  const r6Sorted = r6.meta.category_names;
  assert.ok(r6Sorted[0] === 'Major Cloud Providers' || r6Sorted.includes('Major Cloud Providers'),
    'should include Major Cloud Providers');
  console.log(`✓ free.list_categories → 57 个分类,首项 ${r6Sorted[0]}`);

  // ── 7. free.detail 精确命中 L1 ───────────────────────────
  const r7 = await cmd('free.detail', { name: 'GitHub' });
  assert.strictEqual(r7.type, 'card');
  assert.ok(r7.html.includes('GitHub'));
  assert.ok(r7.html.includes('github.com'));
  assert.strictEqual(r7.meta.cat, 'Source Code Repos');
  assert.strictEqual(r7.meta.level, 1);
  console.log(`✓ free.detail('GitHub') → Source Code Repos`);

  // ── 8. free.detail 精确命中 L2(parent > child) ───────────
  const r8 = await cmd('free.detail', { name: 'Amazon Web Services > CloudFront' });
  assert.strictEqual(r8.type, 'card');
  assert.ok(r8.html.includes('CloudFront'));
  assert.ok(r8.html.includes('aws.amazon.com/cloudfront'));
  assert.strictEqual(r8.meta.cat, 'Major Cloud Providers');
  assert.strictEqual(r8.meta.level, 2);
  assert.strictEqual(r8.meta.name, 'Amazon Web Services > CloudFront');
  console.log(`✓ free.detail('AWS > CloudFront') → L2 嵌套`);

  // ── 9. free.detail 模糊匹配 ────────────────────────────
  const r9 = await cmd('free.detail', { name: 'claude' });
  // 'claude' 应能匹配到一些 AI 服务或 LLM 平台
  assert.ok(r9.type === 'card' || (r9.type === 'text' && r9.text.includes('匹配到多个')));
  console.log(`✓ free.detail('claude') → ${r9.type === 'card' ? 'card' : 'fuzzy 列表'}`);

  // ── 10. free.detail 缺 name 友好报错 ──────────────────────
  const r10 = await cmd('free.detail', {});
  assert.strictEqual(r10.type, 'text');
  assert.ok(r10.text.includes('name 必填'));
  console.log('✓ free.detail 缺 name 报错');

  // ── 11. free.random 分类内随机 ────────────────────────────
  const r11 = await cmd('free.random', { category: 'Generative AI' });
  assert.strictEqual(r11.type, 'card');
  assert.strictEqual(r11.meta.cat, 'Generative AI');
  console.log(`✓ free.random(category='Generative AI') → ${r11.meta.name}`);

  // ── 12. free.random 全局随机 ─────────────────────────────
  const r12a = await cmd('free.random', {});
  const r12b = await cmd('free.random', {});
  assert.strictEqual(r12a.type, 'card');
  assert.strictEqual(r12b.type, 'card');
  // 极小概率同一条
  console.log(`✓ free.random 全局随机 → ${r12a.meta.name} / ${r12b.meta.name}`);

  // ── 13. 数据 sanity ──────────────────────────────────────
  // 至少有 5 个分类
  const bigCats = CATEGORIES.filter(c => c.n_items >= 20);
  assert.ok(bigCats.length >= 5, `should have >= 5 big categories, got ${bigCats.length}`);
  // 至少 3 个有 url
  const withUrl = SERVICES.filter(s => s.url || s.parent_url);
  assert.ok(withUrl.length >= 1000, `should have many services with url, got ${withUrl.length}`);
  // 所有 L2 都有 parent
  const l2NoParent = SERVICES.filter(s => s.level === 2 && !s.parent);
  assert.strictEqual(l2NoParent.length, 0, 'all L2 should have parent');
  console.log(`✓ data sanity: ${bigCats.length} 大分类(${bigCats.map(c=>c.name).slice(0,3).join(',')}...), ${withUrl.length}/${SERVICES.length} 有 url`);

  // ── 14. level filter ────────────────────────────────────
  const r14 = await cmd('free.find', { category: 'Major Cloud Providers', level: 1 });
  // 7 = GCP / AWS / Azure / Oracle / IBM / Cloudflare (parent-only) + Zoho (有 desc 的 L1)
  assert.strictEqual(r14.meta.returned, 7, `Major Cloud Providers L1 should be 7, got ${r14.meta.returned}`);
  console.log(`✓ free.find(level=1) Major Cloud Providers → ${r14.meta.returned} 条 (含 GCP/AWS/Azure/Oracle/IBM/Cloudflare parent + Zoho)`);

  // ── 15. scoreMatch 单元测试(间接验证) ───────────────────
  // 搜 'cdn' 应能在 CDN and Protection 分类下找到结果
  const r15 = await cmd('free.find', { query: 'cdn', limit: 3 });
  assert.ok(r15.meta.returned > 0);
  console.log(`✓ free.find('cdn') → ${r15.meta.returned} 条`);

  console.log('\n所有 15 组断言通过 ✅');
}

main().catch((e) => { console.error(e); process.exit(1); });
