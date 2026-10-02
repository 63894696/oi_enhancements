/**
 * tests/test_public_apis_cn_promo.js — Phase B 烟雾测试
 *
 * 设置 PRISIR_EXT_NO_START=1 让 index.js 不真启动 RPC,
 * 然后直接从 module.exports 拿 ext instance,跑命令 + 断言。
 *
 * 跑法:
 *   PRISIR_EXT_NO_START=1 node tests/test_public_apis_cn_promo.js
 */

const path = require('path');
const assert = require('assert');

const SDK_PATH = path.resolve(__dirname, '..', 'extensions', 'public-apis-cn-promo', 'node_modules', '@prisir', 'extension-sdk');
const { PrisIrExt } = require(SDK_PATH);

// 静默化 + 抓 injectCard
let injected = null;
PrisIrExt.prototype.log = function () {};
PrisIrExt.prototype.injectCard = function (payload) { injected = payload; };
PrisIrExt.prototype.start = function () { return Promise.resolve(); };

// require ext 模块
const extPath = path.resolve(__dirname, '..', 'extensions', 'public-apis-cn-promo');
process.env.PRISIR_EXT_NO_START = '1';
const { ext, CATEGORIES, SERVICES, META, SVC_BY_NAME, CAT_BY_NAME } = require(extPath);

assert.strictEqual(CATEGORIES.length, 54, `categories count: ${CATEGORIES.length} (expect 54)`);
assert.strictEqual(SERVICES.length, 1493, `services count: ${SERVICES.length} (expect 1493)`);
assert.ok(META.snapshot_date, 'meta.snapshot_date should be set');
console.log(`data ok: categories=${CATEGORIES.length} services=${SERVICES.length} snapshot=${META.snapshot_date}`);

async function cmd(method, args, ctx) {
  injected = null;
  const handler = ext.commands.get(method);
  assert.ok(handler, `command not registered: ${method}`);
  return await handler(args, ctx || {});
}

async function main() {
  // ── 1. api_cn.find 关键词搜索「天气」 ────────────────────────
  const r1 = await cmd('api_cn.find', { query: '天气' }, { sessionId: 't1' });
  assert.strictEqual(r1.type, 'card');
  assert.ok(r1.html.includes('🇨🇳 国内 API'), 'should have 🇨🇳 国内 API badge');
  assert.ok(r1.html.includes('关键词: 天气'), 'should echo query in card');
  assert.ok(r1.meta.item_names.length > 0, 'should have at least 1 result');
  console.log(`✓ api_cn.find('天气') → ${r1.meta.returned} 条 (total=${r1.meta.total})`);

  // ── 2. api_cn.find 分类 + 关键词 ─────────────────────────────
  const r2 = await cmd('api_cn.find', { query: '地图', category: '地理编码' }, { sessionId: 't2' });
  assert.strictEqual(r2.type, 'card');
  assert.ok(r2.html.includes('分类: 地理编码'));
  assert.strictEqual(r2.meta.category, '地理编码');
  assert.ok(r2.meta.returned > 0);
  console.log(`✓ api_cn.find(category='地理编码', query='地图') → ${r2.meta.returned} 条`);

  // ── 3. api_cn.find 不存在的分类友好报错 ─────────────────────
  const r3 = await cmd('api_cn.find', { query: 'x', category: '不存在的分类XYZ' });
  assert.strictEqual(r3.type, 'text');
  assert.ok(r3.text.includes('分类不存在'));
  console.log('✓ api_cn.find 拒绝未知分类');

  // ── 4. api_cn.find 无 query 也无 cat 返前 N 条 ───────────────
  const r4 = await cmd('api_cn.find', { limit: 5 });
  assert.strictEqual(r4.type, 'card');
  assert.strictEqual(r4.meta.returned, 5);
  console.log(`✓ api_cn.find(无 query, limit=5) → 5 条`);

  // ── 5. api_cn.find 空结果友好提示 ──────────────────────────
  const r5 = await cmd('api_cn.find', { query: 'zzz绝对不存在zzz' });
  assert.strictEqual(r5.type, 'text');
  assert.ok(r5.text.includes('没有找到'));
  console.log('✓ api_cn.find 空结果降级为文本');

  // ── 6. api_cn.list_categories ─────────────────────────────
  const r6 = await cmd('api_cn.list_categories', {});
  assert.strictEqual(r6.type, 'card');
  assert.ok(r6.html.includes('全部 54 个分类'));
  assert.strictEqual(r6.meta.total, 54);
  assert.strictEqual(r6.meta.category_names.length, 54);
  // 排序按 n_items 降序 — 开发 120 最大
  assert.strictEqual(r6.meta.category_names[0], '开发', `最大分类应为 开发,实际 ${r6.meta.category_names[0]}`);
  console.log(`✓ api_cn.list_categories → 54 个分类,首项 ${r6.meta.category_names[0]} (n_items 最多)`);

  // ── 7. api_cn.detail 精确命中 ───────────────────────────
  // 找一个肯定存在的 API(测试时可探测)
  const sampleName = SERVICES[0].name;
  const r7 = await cmd('api_cn.detail', { name: sampleName });
  assert.strictEqual(r7.type, 'card');
  assert.ok(r7.html.includes(sampleName));
  console.log(`✓ api_cn.detail('${sampleName}') → ${r7.meta.cat}`);

  // ── 8. api_cn.detail 模糊 name 命中 ────────────────────────
  // 中文环境:挑一个 4 字 API,模糊匹配前两字
  const fourChar = SERVICES.find(s => s.name.length >= 4);
  if (fourChar) {
    const prefix2 = fourChar.name.slice(0, 2);
    const r8 = await cmd('api_cn.detail', { name: prefix2 });
    if (r8.type === 'card') {
      assert.ok(r8.html.includes(prefix2));
      console.log(`✓ api_cn.detail('${prefix2}' 前两字) → ${r8.meta.name}`);
    } else {
      // 多匹配返 list
      assert.ok(r8.text.includes('匹配到多个'));
      console.log(`✓ api_cn.detail('${prefix2}') 触发多匹配降级`);
    }
  }

  // ── 9. api_cn.detail 模糊 desc 命中 ────────────────────────
  // '天气' 在 desc 中应该能命中
  const r9 = await cmd('api_cn.detail', { name: '天气' });
  assert.ok(r9.type === 'card' || (r9.type === 'text' && r9.text.includes('匹配到多个')));
  console.log(`✓ api_cn.detail('天气') → ${r9.type === 'card' ? 'card' : 'fuzzy 列表'}`);

  // ── 10. api_cn.detail 多匹配返 list ────────────────────────
  // 'API' 在很多 name/desc 里,触发多匹配
  const r10 = await cmd('api_cn.detail', { name: '免费' });
  if (r10.type === 'text' && r10.text.includes('匹配到多个')) {
    console.log(`✓ api_cn.detail('免费') 触发多匹配降级`);
  } else {
    assert.strictEqual(r10.type, 'card');
    console.log(`✓ api_cn.detail('免费') 单条命中 (${r10.meta.name})`);
  }

  // ── 11. api_cn.detail 缺 name 友好报错 ──────────────────────
  const r11 = await cmd('api_cn.detail', {});
  assert.strictEqual(r11.type, 'text');
  assert.ok(r11.text.includes('name 必填'));
  console.log('✓ api_cn.detail 缺 name 报错');

  // ── 12. api_cn.random 分类内随机 ────────────────────────────
  const r12 = await cmd('api_cn.random', { category: '天气' });
  assert.strictEqual(r12.type, 'card');
  assert.strictEqual(r12.meta.cat, '天气');
  console.log(`✓ api_cn.random(category='天气') → ${r12.meta.name}`);

  // ── 13. api_cn.random 全局随机 ─────────────────────────────
  const r13a = await cmd('api_cn.random', {});
  const r13b = await cmd('api_cn.random', {});
  assert.strictEqual(r13a.type, 'card');
  assert.strictEqual(r13b.type, 'card');
  console.log(`✓ api_cn.random 全局随机 → ${r13a.meta.name} / ${r13b.meta.name}`);

  // ── 14. meta.snapshot_date 渲染到 foot ─────────────────
  const r14 = await cmd('api_cn.list_categories', {});
  assert.ok(r14.html.includes(META.snapshot_date), 'card foot 应含 snapshot_date');
  console.log(`✓ meta.snapshot_date=${META.snapshot_date} 已渲染到 card foot`);

  // ── 15. 中文 chip 渲染(无 cors) ───────────────────────
  const r15 = await cmd('api_cn.detail', { name: SERVICES[0].name });
  assert.ok(r15.html.includes('认证:'), '应含 认证: chip');
  assert.ok(r15.html.includes('HTTPS:'), '应含 HTTPS: chip');
  assert.ok(!r15.html.includes('CORS:'), '不应含 CORS chip(public-apis-cn 无 cors 字段)');
  console.log(`✓ api_cn.detail 卡片含 认证/HTTPS 2 个 chip,无 CORS`);

  console.log('\n所有 15 组断言通过 ✅');
}

main().catch((e) => { console.error(e); process.exit(1); });