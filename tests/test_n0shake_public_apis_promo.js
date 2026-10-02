/**
 * tests/test_n0shake_public_apis_promo.js — Sprint 2 Phase B 烟雾测试
 *
 * 设置 PRISIR_EXT_NO_START=1 让 index.js 不真启动 RPC,
 * 然后直接从 module.exports 拿 ext instance,跑命令 + 断言。
 *
 * 跑法:
 *   PRISIR_EXT_NO_START=1 node tests/test_n0shake_public_apis_promo.js
 */

const path = require('path');
const assert = require('assert');

const SDK_PATH = path.resolve(__dirname, '..', 'extensions', 'n0shake-public-apis-promo', 'node_modules', '@prisir', 'extension-sdk');
const { PrisIrExt } = require(SDK_PATH);

// 静默化 + 抓 injectCard
let injected = null;
PrisIrExt.prototype.log = function () {};
PrisIrExt.prototype.injectCard = function (payload) { injected = payload; };
PrisIrExt.prototype.start = function () { return Promise.resolve(); };

// require ext 模块
const extPath = path.resolve(__dirname, '..', 'extensions', 'n0shake-public-apis-promo');
process.env.PRISIR_EXT_NO_START = '1';
const { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME } = require(extPath);

assert.strictEqual(CATEGORIES.length, 56, `categories count: ${CATEGORIES.length} (expect 56)`);
assert.strictEqual(SERVICES.length, 481, `services count: ${SERVICES.length} (expect 481)`);
assert.ok(META.snapshot_date, 'meta.snapshot_date should be set');
console.log(`data ok: categories=${CATEGORIES.length} services=${SERVICES.length} snapshot=${META.snapshot_date}`);

async function cmd(method, args, ctx) {
  injected = null;
  const handler = ext.commands.get(method);
  assert.ok(handler, `command not registered: ${method}`);
  return await handler(args, ctx || {});
}

async function run() {
  // ── 1. nokeyapi.find 关键词搜索「weather」 ───────────────────────────
  const r1 = await cmd('nokeyapi.find', { query: 'weather' }, { sessionId: 't1' });
  assert.strictEqual(r1.type, 'card');
  assert.ok(r1.html.includes('🔓 免 key API'), 'should have 🔓 免 key API badge');
  assert.ok(r1.html.includes('关键词: weather'), 'should echo query in card');
  assert.ok(r1.meta.item_names.length > 0, 'should have at least 1 result');
  console.log(`✓ nokeyapi.find('weather') → ${r1.meta.returned} 条 (total=${r1.meta.total})`);

  // ── 2. nokeyapi.find 分类 + 关键词 ─────────────────────────────────
  const r2 = await cmd('nokeyapi.find', { query: 'spotify', category: 'Music' }, { sessionId: 't2' });
  assert.strictEqual(r2.type, 'card');
  assert.ok(r2.html.includes('分类: Music'));
  assert.strictEqual(r2.meta.category, 'Music');
  assert.ok(r2.meta.returned >= 1, '应至少命中 Spotify');
  console.log(`✓ nokeyapi.find(category='Music', query='spotify') → ${r2.meta.returned} 条`);

  // ── 3. nokeyapi.find 不存在的分类友好报错 ─────────────────────────
  const r3 = await cmd('nokeyapi.find', { query: 'x', category: '不存在的分类XYZ' });
  assert.strictEqual(r3.type, 'text');
  assert.ok(r3.text.includes('分类不存在'));
  console.log('✓ nokeyapi.find 拒绝未知分类');

  // ── 4. nokeyapi.find 无 query 也无 cat 返前 N 条 ───────────────────
  const r4 = await cmd('nokeyapi.find', { limit: 5 });
  assert.strictEqual(r4.type, 'card');
  assert.strictEqual(r4.meta.returned, 5);
  console.log(`✓ nokeyapi.find(无 query, limit=5) → 5 条`);

  // ── 5. nokeyapi.find 空结果友好提示 ──────────────────────────────
  const r5 = await cmd('nokeyapi.find', { query: 'zzz绝对不存在zzz' });
  assert.strictEqual(r5.type, 'text');
  assert.ok(r5.text.includes('没有找到'));
  console.log('✓ nokeyapi.find 空结果降级为文本');

  // ── 6. nokeyapi.list_categories ──────────────────────────────────
  const r6 = await cmd('nokeyapi.list_categories', {});
  assert.strictEqual(r6.type, 'card');
  assert.ok(r6.html.includes('全部 56 个分类'));
  assert.strictEqual(r6.meta.total, 56);
  assert.strictEqual(r6.meta.category_names.length, 56);
  // 排序应该是按 n_items 降序 — Miscellaneous 是最大(57 条)
  const r6Sorted = r6.meta.category_names;
  assert.strictEqual(r6Sorted[0], 'Miscellaneous', `最大分类应为 Miscellaneous,实际 ${r6Sorted[0]}`);
  console.log(`✓ nokeyapi.list_categories → 56 个分类,首项 ${r6Sorted[0]}`);

  // ── 7. nokeyapi.detail 精确命中 ─────────────────────────────────
  const r7 = await cmd('nokeyapi.detail', { name: 'Spotify' });
  assert.strictEqual(r7.type, 'card');
  assert.ok(r7.html.includes('Spotify'));
  assert.ok(r7.html.includes('Music'));
  assert.strictEqual(r7.meta.cat, 'Music');
  assert.strictEqual(r7.meta.open_trial, 'N/A');
  console.log(`✓ nokeyapi.detail('Spotify') → ${r7.meta.cat} open_trial=${r7.meta.open_trial}`);

  // ── 8. nokeyapi.detail 模糊 name 命中 ────────────────────────────
  const r8 = await cmd('nokeyapi.detail', { name: 'GitHub' });
  assert.strictEqual(r8.type, 'card');
  assert.ok(r8.html.includes('GitHub'));
  console.log(`✓ nokeyapi.detail('GitHub') → ${r8.meta.cat}`);

  // ── 9. nokeyapi.detail GitHub Licenses API 脏数据命中 ────────────
  const r9 = await cmd('nokeyapi.detail', { name: 'GitHub Licenses API' });
  assert.strictEqual(r9.type, 'card');
  assert.ok(r9.html.includes('Legal'), '应归属 Legal');
  assert.strictEqual(r9.meta.cat, 'Legal');
  assert.strictEqual(r9.meta.open_trial, 'N/A');
  console.log(`✓ nokeyapi.detail('GitHub Licenses API') → cat=Legal(脏数据兜底)`);

  // ── 10. nokeyapi.detail 多匹配返 list ───────────────────────────
  const r10 = await cmd('nokeyapi.detail', { name: 'api' });
  if (r10.type === 'text' && r10.text.includes('匹配到多个')) {
    assert.ok(true);
    console.log(`✓ nokeyapi.detail('api') 触发多匹配降级`);
  } else {
    assert.strictEqual(r10.type, 'card');
    console.log(`✓ nokeyapi.detail('api') 单条命中 (${r10.meta.name})`);
  }

  // ── 11. nokeyapi.detail 缺 name 友好报错 ────────────────────────
  const r11 = await cmd('nokeyapi.detail', {});
  assert.strictEqual(r11.type, 'text');
  assert.ok(r11.text.includes('name 必填'));
  console.log('✓ nokeyapi.detail 缺 name 报错');

  // ── 12. nokeyapi.random 分类内随机 ──────────────────────────────
  const r12 = await cmd('nokeyapi.random', { category: 'Music' });
  assert.strictEqual(r12.type, 'card');
  assert.strictEqual(r12.meta.cat, 'Music');
  console.log(`✓ nokeyapi.random(category='Music') → ${r12.meta.name} open_trial=${r12.meta.open_trial}`);

  // ── 13. nokeyapi.random 全局随机 ────────────────────────────────
  const r13a = await cmd('nokeyapi.random', {});
  const r13b = await cmd('nokeyapi.random', {});
  assert.strictEqual(r13a.type, 'card');
  assert.strictEqual(r13b.type, 'card');
  console.log(`✓ nokeyapi.random 全局随机 → ${r13a.meta.name} / ${r13b.meta.name}`);

  // ── 14. meta.snapshot_date 渲染到 foot ──────────────────────────
  const r14 = await cmd('nokeyapi.list_categories', {});
  assert.ok(r14.html.includes(META.snapshot_date), 'card foot 应含 snapshot_date');
  console.log(`✓ meta.snapshot_date=${META.snapshot_date} 已渲染到 card foot`);

  // ── 15. open_trial chip 三档配色 — good/warn/accent ─────────────
  //     N/A → ext-chip-good,💸 → ext-chip-warn,Open Source → ext-chip-accent
  // 抽 3 条不同 open_trial 的 detail 检查 chip class
  const naSvc = SERVICES.find(s => s.open_trial === 'N/A');
  const trialSvc = SERVICES.find(s => s.open_trial === '💸');
  const ossSvc = SERVICES.find(s => s.open_trial === 'Open Source');
  assert.ok(naSvc, '需要至少 1 条 N/A sample');
  const rNa = await cmd('nokeyapi.detail', { name: naSvc.name });
  assert.ok(rNa.html.includes('ext-chip-good'), 'N/A 应配 good 色');
  assert.ok(rNa.html.includes(`免 key: N/A`));
  if (trialSvc) {
    const rTr = await cmd('nokeyapi.detail', { name: trialSvc.name });
    assert.ok(rTr.html.includes('ext-chip-warn'), '💸 应配 warn 色');
    console.log(`  💸 试点 trial: ${trialSvc.name}`);
  }
  if (ossSvc) {
    const rOs = await cmd('nokeyapi.detail', { name: ossSvc.name });
    assert.ok(rOs.html.includes('ext-chip-accent'), 'Open Source 应配 accent 色');
    console.log(`  🔓 试点 open source: ${ossSvc.name}`);
  }
  console.log(`✓ open_trial 三档 chip 配色 (good/warn/accent) 全部就位`);

  console.log('\n所有 15 组断言通过 ✅');
}

run().catch((e) => { console.error(e); process.exit(1); });