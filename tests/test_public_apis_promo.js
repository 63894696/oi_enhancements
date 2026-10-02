/**
 * tests/test_public_apis_promo.js — Phase B 烟雾测试
 *
 * 设置 PRISIR_EXT_NO_START=1 让 index.js 不真启动 RPC,
 * 然后直接从 module.exports 拿 ext instance,跑命令 + 断言。
 *
 * 跑法:
 *   PRISIR_EXT_NO_START=1 node tests/test_public_apis_promo.js
 */

const path = require('path');
const assert = require('assert');

const SDK_PATH = path.resolve(__dirname, '..', 'extensions', 'public-apis-promo', 'node_modules', '@prisir', 'extension-sdk');
const { PrisIrExt } = require(SDK_PATH);

// 静默化 + 抓 injectCard
let injected = null;
PrisIrExt.prototype.log = function () {};
PrisIrExt.prototype.injectCard = function (payload) { injected = payload; };
PrisIrExt.prototype.start = function () { return Promise.resolve(); };

// require ext 模块
const extPath = path.resolve(__dirname, '..', 'extensions', 'public-apis-promo');
process.env.PRISIR_EXT_NO_START = '1';
const { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME } = require(extPath);

assert.strictEqual(CATEGORIES.length, 51, `categories count: ${CATEGORIES.length} (expect 51)`);
assert.strictEqual(SERVICES.length, 1953, `services count: ${SERVICES.length} (expect 1953)`);
assert.ok(META.snapshot_date, 'meta.snapshot_date should be set');
console.log(`data ok: categories=${CATEGORIES.length} services=${SERVICES.length} snapshot=${META.snapshot_date}`);

async function cmd(method, args, ctx) {
  injected = null;
  const handler = ext.commands.get(method);
  assert.ok(handler, `command not registered: ${method}`);
  return await handler(args, ctx || {});
}

async function main() {
  // ── 1. api.find 关键词搜索「weather」 ────────────────────────
  const r1 = await cmd('api.find', { query: 'weather' }, { sessionId: 't1' });
  assert.strictEqual(r1.type, 'card');
  assert.ok(r1.html.includes('🔌 API 资源'), 'should have 🔌 API 资源 badge');
  assert.ok(r1.html.includes('关键词: weather'), 'should echo query in card');
  assert.ok(r1.meta.item_names.length > 0, 'should have at least 1 result');
  console.log(`✓ api.find('weather') → ${r1.meta.returned} 条 (total=${r1.meta.total})`);

  // ── 2. api.find 分类 + 关键词 ─────────────────────────────
  const r2 = await cmd('api.find', { query: 'cat', category: 'Animals' }, { sessionId: 't2' });
  assert.strictEqual(r2.type, 'card');
  assert.ok(r2.html.includes('分类: Animals'));
  assert.strictEqual(r2.meta.category, 'Animals');
  assert.ok(r2.meta.returned > 0);
  console.log(`✓ api.find(category='Animals', query='cat') → ${r2.meta.returned} 条`);

  // ── 3. api.find 不存在的分类友好报错 ─────────────────────
  const r3 = await cmd('api.find', { query: 'x', category: '不存在的分类XYZ' });
  assert.strictEqual(r3.type, 'text');
  assert.ok(r3.text.includes('分类不存在'));
  console.log('✓ api.find 拒绝未知分类');

  // ── 4. api.find 无 query 也无 cat 返前 N 条 ───────────────
  const r4 = await cmd('api.find', { limit: 5 });
  assert.strictEqual(r4.type, 'card');
  assert.strictEqual(r4.meta.returned, 5);
  console.log(`✓ api.find(无 query, limit=5) → 5 条`);

  // ── 5. api.find 空结果友好提示 ──────────────────────────
  const r5 = await cmd('api.find', { query: 'zzz绝对不存在zzz' });
  assert.strictEqual(r5.type, 'text');
  assert.ok(r5.text.includes('没有找到'));
  console.log('✓ api.find 空结果降级为文本');

  // ── 6. api.list_categories ─────────────────────────────
  const r6 = await cmd('api.list_categories', {});
  assert.strictEqual(r6.type, 'card');
  assert.ok(r6.html.includes('全部 51 个分类'));
  assert.strictEqual(r6.meta.total, 51);
  assert.strictEqual(r6.meta.category_names.length, 51);
  // 排序应该是按 n_items 降序
  const r6Sorted = r6.meta.category_names;
  // Development 是最大分类(202 项)
  assert.strictEqual(r6Sorted[0], 'Development', `最大分类应为 Development,实际 ${r6Sorted[0]}`);
  console.log(`✓ api.list_categories → 51 个分类,首项 ${r6Sorted[0]}`);

  // ── 7. api.detail 精确命中 ───────────────────────────
  const r7 = await cmd('api.detail', { name: 'Cat Facts' });
  assert.strictEqual(r7.type, 'card');
  assert.ok(r7.html.includes('Cat Facts'));
  assert.ok(r7.html.includes('alexwohlbruck.github.io') || r7.html.includes('catfact.ninja'));
  assert.strictEqual(r7.meta.cat, 'Animals');
  console.log(`✓ api.detail('Cat Facts') → Animals (${r7.meta.name})`);

  // ── 8. api.detail 模糊 name 命中 ────────────────────────
  const r8 = await cmd('api.detail', { name: 'GitHub' });
  assert.strictEqual(r8.type, 'card');
  assert.ok(r8.html.includes('GitHub'));
  console.log(`✓ api.detail('GitHub') → ${r8.meta.cat}`);

  // ── 9. api.detail 模糊 desc 命中 ────────────────────────
  // 'weather' 不一定在 name 中,但在 desc 中应该能命中
  const r9 = await cmd('api.detail', { name: 'forecast' });
  assert.ok(r9.type === 'card' || (r9.type === 'text' && r9.text.includes('匹配到多个')));
  console.log(`✓ api.detail('forecast') → ${r9.type === 'card' ? 'card' : 'fuzzy 列表'}`);

  // ── 10. api.detail 多匹配返 list ────────────────────────
  // 'api' 在很多 name/desc 里,触发多匹配
  const r10 = await cmd('api.detail', { name: 'photo' });
  if (r10.type === 'text' && r10.text.includes('匹配到多个')) {
    assert.ok(true);
    console.log(`✓ api.detail('photo') 触发多匹配降级`);
  } else {
    // 也允许单条命中
    assert.strictEqual(r10.type, 'card');
    console.log(`✓ api.detail('photo') 单条命中 (${r10.meta.name})`);
  }

  // ── 11. api.detail 缺 name 友好报错 ──────────────────────
  const r11 = await cmd('api.detail', {});
  assert.strictEqual(r11.type, 'text');
  assert.ok(r11.text.includes('name 必填'));
  console.log('✓ api.detail 缺 name 报错');

  // ── 12. api.random 分类内随机 ────────────────────────────
  const r12 = await cmd('api.random', { category: 'Animals' });
  assert.strictEqual(r12.type, 'card');
  assert.strictEqual(r12.meta.cat, 'Animals');
  console.log(`✓ api.random(category='Animals') → ${r12.meta.name}`);

  // ── 13. api.random 全局随机 ─────────────────────────────
  const r13a = await cmd('api.random', {});
  const r13b = await cmd('api.random', {});
  assert.strictEqual(r13a.type, 'card');
  assert.strictEqual(r13b.type, 'card');
  console.log(`✓ api.random 全局随机 → ${r13a.meta.name} / ${r13b.meta.name}`);

  // ── 14. meta.snapshot_date 渲染到 foot ─────────────────
  const r14 = await cmd('api.list_categories', {});
  assert.ok(r14.html.includes(META.snapshot_date), 'card foot 应含 snapshot_date');
  console.log(`✓ meta.snapshot_date=${META.snapshot_date} 已渲染到 card foot`);

  // ── 15. Auth/HTTPS/CORS chip 渲染 ───────────────────────
  const r15 = await cmd('api.detail', { name: 'Cat Facts' });
  assert.ok(r15.html.includes('Auth:'), '应含 Auth: chip');
  assert.ok(r15.html.includes('HTTPS:'), '应含 HTTPS: chip');
  assert.ok(r15.html.includes('CORS:'), '应含 CORS: chip');
  console.log(`✓ api.detail 卡片含 Auth/HTTPS/CORS 3 个 chip`);

  console.log('\n所有 15 组断言通过 ✅');
}

main().catch((e) => { console.error(e); process.exit(1); });
