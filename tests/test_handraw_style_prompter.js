/**
 * tests/test_handraw_style_prompter.js — Phase B 烟雾测试
 *
 * 设置 PRISIR_EXT_NO_START=1 让 index.js 不真启动 RPC,
 * 然后直接从 module.exports 拿 ext instance,跑命令 + 断言。
 *
 * 跑法:
 *   PRISIR_EXT_NO_START=1 node tests/test_handraw_style_prompter.js
 */

const path = require('path');
const assert = require('assert');

const SDK_PATH = path.resolve(__dirname, '..', 'extensions', 'handraw-style-prompter', 'node_modules', '@prisir', 'extension-sdk');
const { PrisIrExt } = require(SDK_PATH);

// 静默化 + 抓 injectCard
let injected = null;
PrisIrExt.prototype.log = function () {};
PrisIrExt.prototype.injectCard = function (payload) { injected = payload; };
PrisIrExt.prototype.start = function () { return Promise.resolve(); };

// require ext 模块
const extPath = path.resolve(__dirname, '..', 'extensions', 'handraw-style-prompter');
process.env.PRISIR_EXT_NO_START = '1';
const { ext, STYLES, COLORS, LAYOUTS } = require(extPath);

assert.ok(STYLES.length === 278, `styles count: ${STYLES.length} (expect 278)`);
assert.ok(COLORS.length === 36,  `colors count: ${COLORS.length} (expect 36)`);
assert.ok(LAYOUTS.length >= 120, `layouts count: ${LAYOUTS.length} (expect >= 120)`);
console.log(`data ok: styles=${STYLES.length} colors=${COLORS.length} layouts=${LAYOUTS.length}`);

async function cmd(method, args, ctx) {
  injected = null;
  const handler = ext.commands.get(method);
  assert.ok(handler, `command not registered: ${method}`);
  return await handler(args, ctx || {});
}

async function main() {
  // ── 1. poster.smart 主题 = "秋天的第一杯奶茶" ─────────────────────
  const r1 = await cmd('poster.smart', { theme: '秋天的第一杯奶茶' }, { sessionId: 'test-sess' });
  assert.strictEqual(r1.type, 'card');
  assert.ok(r1.html.includes('🪄 智能推荐'), 'should have 智能推荐 badge');
  assert.ok(r1.html.includes('秋天的第一杯奶茶'), 'should echo subject');
  assert.ok(r1.html.includes('🇨🇳 中文 prompt'), 'should have zh label');
  assert.ok(r1.html.includes('🇺🇸 English prompt'), 'should have en label');
  assert.ok(r1.meta.style && /^\d{3}$/.test(String(r1.meta.style).padStart(3, '0')), 'meta.style should be 3-digit');
  assert.ok(/^C-\d{2}$/.test(r1.meta.color), 'meta.color should be C-NN');
  assert.ok(injected && injected.sessionId === 'test-sess', 'should inject card');
  console.log('✓ poster.smart');

  // ── 2. poster.spec 精确指定 ─────────────────────────────────────
  const r2 = await cmd('poster.spec', {
    subject: '春节回家',
    style: '041', color: 'C-25', layout: 'SC-001',
  }, { sessionId: 's2' });
  assert.ok(r2.html.includes('041号风格'), 'should mention style 041');
  assert.ok(r2.html.includes('C-25'), 'should mention color C-25');
  assert.ok(r2.html.includes('SC-001'), 'should mention layout SC-001');
  assert.ok(String(r2.meta.style) === '041' || r2.meta.style === 41, `meta.style should be 041/41, got ${r2.meta.style}`);
  assert.strictEqual(r2.meta.color, 'C-25');
  assert.strictEqual(r2.meta.layout, 'SC-001');
  console.log('✓ poster.spec');

  // ── 3. poster.spec 无 subject 应友好报错 ─────────────────────────
  const r3 = await cmd('poster.spec', { style: '041' });
  assert.ok(r3.html.includes('需要主题'), 'should ask for subject');
  console.log('✓ poster.spec guard');

  // ── 4. poster.ai_design 8 字段 ────────────────────────────────────
  const r4 = await cmd('poster.ai_design', {
    subject: '城市夜景独白',
    scene: '雨后街道',
    audience: '都市青年',
    density: 3,
    emotion: '孤独',
    color: 'C-31',
    style: '043',
  }, { sessionId: 's4' });
  assert.ok(r4.html.includes('🎨 AI 海报设计'), 'should have AI design badge');
  assert.ok(r4.html.includes('城市夜景独白'), 'should echo subject');
  assert.ok(r4.html.includes('雨后街道'), 'should mention scene');
  assert.ok(r4.html.includes('受众:都市青年'), 'should mention audience');
  assert.ok(r4.html.includes('信息密度:3/5'), 'should mention density');
  assert.ok(r4.html.includes('情绪基调:孤独'), 'should mention emotion');
  console.log('✓ poster.ai_design');

  // ── 5. poster.ai_design 缺 subject 兜底 ──────────────────────────
  const r5 = await cmd('poster.ai_design', { scene: 'rain' });
  assert.ok(r5.html.includes('需要 subject'), 'should ask for subject');
  console.log('✓ poster.ai_design guard');

  // ── 6. poster.list 数据枚举 ────────────────────────────────────────
  const l1 = await cmd('poster.list', { kind: 'styles', q: 'watercolor' });
  assert.ok(l1.text.includes('·'), 'should list styles');
  assert.ok(l1.text.split('\n').length > 0, 'should have lines');
  const l2 = await cmd('poster.list', { kind: 'colors' });
  assert.ok(l2.text.includes('C-01'), 'should list C-01');
  const l3 = await cmd('poster.list', { kind: 'layouts', q: '漫画分镜' });
  assert.ok(l3.text.includes('SB-'), 'should list SB layouts');
  const l4 = await cmd('poster.list', { kind: 'unknown' });
  assert.ok(l4.text.includes('unknown kind'), 'should reject unknown');
  console.log('✓ poster.list');

  // ── 7. 卡片 HTML 结构 + 复制按钮 ──────────────────────────────────
  const r7 = await cmd('poster.smart', { theme: '亲子周末' }, { sessionId: 's7' });
  assert.ok(r7.html.includes('class="ext-card ext-poster"'), 'should have ext-card class');
  assert.ok(r7.html.includes('data-role="zh"'), 'should have zh pre tag');
  assert.ok(r7.html.includes('data-role="en"'), 'should have en pre tag');
  assert.ok(r7.html.includes('复制中文'), 'should have copy zh button');
  assert.ok(r7.html.includes('Copy EN'), 'should have copy en button');
  console.log('✓ card structure');

  // ── 8. 数据完整性 sanity ─────────────────────────────────────────
  for (const c of COLORS.slice(0, 5)) {
    assert.ok(c.n && c.name_zh && c.name_en, `color incomplete: ${JSON.stringify(c)}`);
  }
  for (const s of STYLES.slice(0, 5)) {
    assert.ok(s.n && s.ref_author && s.name_zh, `style incomplete: ${JSON.stringify(s)}`);
  }
  for (const l of LAYOUTS.slice(0, 5)) {
    assert.ok(l.n && l.cat && l.name_zh && l.prompt_zh, `layout incomplete: ${JSON.stringify(l)}`);
  }
  console.log('✓ data sanity (first 5 each)');

  console.log('\n所有 8 组断言通过 ✅');
}

main().catch((e) => { console.error(e); process.exit(1); });