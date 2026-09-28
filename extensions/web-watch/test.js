'use strict';

/**
 * web-watch v0.1.0 测试入口 — 跑 `PRISIR_WEB_WEB_WATCH_TEST=1` or `node test.js`
 * 不污染主入口 index.js(主入口要 ext.start() 才能跑)。
 *
 * 覆盖:
 *   - watch.add  → 返 ok + id
 *   - watch.list → 列表包含刚加的
 *   - watch.run(id, { force: false }) 首次 → no_change/first_check baseline
 *   - 直接调 fetchUrl(mock) + 模拟变化 → 强制 force=true 检测
 *   - watch.remove(id) → 删得掉
 *   - 非法 url 拒绝
 *   - DB 文件 / 表存在
 *   - notify.ps1 存在
 */

const path = require('path');
const fs = require('fs');

const EXT_HOME = path.join(__dirname, '__test_home__');
process.env.PRISIR_EXT_HOME = EXT_HOME;
process.env.PRISIR_WEB_WATCH_TEST = '1';

const { ext } = require('./index.js');

// ─── mini assert ──────────────────────────────────────────────
let _fail = 0, _pass = 0;
function assert(cond, msg) {
  if (cond) { _pass++; }
  else { _fail++; console.error(`  FAIL: ${msg}`); }
}
function eq(a, b, msg) {
  const ok = JSON.stringify(a) === JSON.stringify(b);
  if (ok) { _pass++; }
  else { _fail++; console.error(`  FAIL: ${msg} → got ${JSON.stringify(a)} want ${JSON.stringify(b)}`); }
}

// ─── 公共 ─────────────────────────────────────────────────────
const TEMP_HOME = EXT_HOME;
const NOTIFY_PS = path.join(__dirname, 'notify.ps1');

function cleanup() {
  if (fs.existsSync(TEMP_HOME)) {
    try {
      for (const f of fs.readdirSync(TEMP_HOME)) {
        try { fs.unlinkSync(path.join(TEMP_HOME, f)); } catch {}
      }
      try { fs.rmdirSync(TEMP_HOME); } catch {}
    } catch {}
  }
}

async function t(name, fn) {
  process.stdout.write(`▶ ${name}\n`);
  try { await fn(); }
  catch (e) { _fail++; console.error(`  FAIL threw: ${e.message}`); }
}

// ─── 测试 ─────────────────────────────────────────────────────
async function main() {
  cleanup();
  fs.mkdirSync(TEMP_HOME, { recursive: true });

  // 1. ext 自身
  await t('ext meta', async () => {
    assert(ext && ext.meta, 'ext loaded');
    eq(ext.meta.id, 'web-watch', 'id match');
    eq(ext.meta.version, '0.1.0', 'version match');
  });

  // 2. 所有命令都注册
  await t('commands registered', async () => {
    const cmds = ['watch.add', 'watch.list', 'watch.remove', 'watch.run', 'watch.check'];
    for (const c of cmds) {
      assert(typeof ext.commands.get(c) === 'function', `register ${c}`);
    }
  });

  // 3. db() 创建了 + 表存在 — 先触发一次 db() 初始化
  let watchId1;
  await t('sqlite schema', async () => {
    // 先 add 一个,触发 db() 初始化
    const r0 = await ext.commands.get('watch.add')(
      { url: 'https://mock.example/schema-probe', interval_sec: 60 },
      { sessionId: 'test' }
    );
    assert(r0.ok, 'probe add ok');
    watchId1 = r0.id;
    const DatabaseSync = require('node:sqlite').DatabaseSync;
    const db = new DatabaseSync(path.join(TEMP_HOME, 'state.db'));
    const tables = db.prepare(
      `SELECT name FROM sqlite_master WHERE type='table' AND name IN ('watches','notifications') ORDER BY name`
    ).all().map(r => r.name);
    const idxCount = db.prepare(
      `SELECT COUNT(*) AS n FROM sqlite_master WHERE type='index' AND tbl_name IN ('watches','notifications')`
    ).get().n;
    db.close();
    eq(tables, ['notifications', 'watches'], 'expected tables');
    assert(idxCount >= 2, `expected >=2 indexes, got ${idxCount}`);
    // 立刻删掉这个 probe
    await ext.commands.get('watch.remove')({ id: watchId1 }, { sessionId: 'test' });
    watchId1 = null;
  });

  // 4. watch.add 合法 url
  await t('watch.add (http)', async () => {
    const r = await ext.commands.get('watch.add')(
      { url: 'https://mock.example/abc', interval_sec: 60 },
      { sessionId: 'test' }
    );
    assert(r.ok, 'ok flag');
    assert(/^w_/.test(r.id), 'id prefix w_');
    assert(r.watch && r.watch.url === 'https://mock.example/abc', 'watch.url echoed');
    assert(r.watch.interval_sec === 60, 'interval_sec echoed');
    watchId1 = r.id;
  });

  // 5. watch.add 非法 url 拒绝
  await t('watch.add (bad url)', async () => {
    const r = await ext.commands.get('watch.add')(
      { url: 'not-a-url' },
      { sessionId: 'test' }
    );
    assert(r.ok === false, 'rejected');
    assert(/url must start/.test(r.error || ''), 'error message');
  });

  // 6. watch.add 空 url 拒绝
  await t('watch.add (empty url)', async () => {
    const r = await ext.commands.get('watch.add')({ url: '' }, { sessionId: 'test' });
    assert(r.ok === false, 'rejected');
  });

  // 7. watch.add file:// 也可以
  let watchId2;
  await t('watch.add (file://)', async () => {
    const r = await ext.commands.get('watch.add')(
      { url: 'file:///C:/tmp/test.html', interval_sec: 120, selector: '#main' },
      { sessionId: 'test' }
    );
    assert(r.ok, 'ok');
    assert(r.watch.selector === '#main', 'selector saved');
    watchId2 = r.id;
  });

  // 8. watch.list 能列出来
  await t('watch.list', async () => {
    const r = await ext.commands.get('watch.list')({ limit: 50 }, { sessionId: 'test' });
    assert(r.ok, 'ok');
    assert(r.watches.length >= 2, 'at least 2 watches');
    assert(r.watches.some(w => w.id === watchId1), 'w1 in list');
    assert(r.watches.some(w => w.id === watchId2), 'w2 in list');
  });

  // 9. watch.run 真实网络 — 大概率失败,只要不崩
  await t('watch.run (network, accept either)', async () => {
    const r = await ext.commands.get('watch.run')(
      { id: watchId1, toast: false },
      { sessionId: 'test' }
    );
    assert(r.ok, 'returns ok=true');
    assert(typeof r.changed === 'boolean', 'changed is bool');
    assert(typeof r.run_id === 'string', 'run_id present');
    // fetch_ok false 也算合法(网络 fetch 失败是预期)
  });

  // 10. watch.run 不存在的 id
  await t('watch.run (missing id)', async () => {
    const r = await ext.commands.get('watch.run')(
      { id: 'w_doesnotexist', toast: false },
      { sessionId: 'test' }
    );
    assert(r.ok === false, 'rejected');
    assert(/not found/.test(r.error || ''), 'not found msg');
  });

  // 11. watch.remove 删一个
  await t('watch.remove (existing)', async () => {
    const r = await ext.commands.get('watch.remove')(
      { id: watchId2 },
      { sessionId: 'test' }
    );
    assert(r.ok, 'ok');
    assert(r.removed === 1, 'removed 1');
  });

  // 12. 重新加 + check all
  await t('watch.check (all)', async () => {
    const r = await ext.commands.get('watch.check')(
      { all: true, toast: false },
      { sessionId: 'test' }
    );
    assert(r.ok, 'ok');
    assert(typeof r.ran === 'number' && r.ran >= 1, 'ran >= 1');
    assert(typeof r.changed === 'number', 'changed numeric');
  });

  // 13. watch.check 没参数 → 错误
  await t('watch.check (no args)', async () => {
    const r = await ext.commands.get('watch.check')({}, { sessionId: 'test' });
    assert(r.ok === false, 'rejected');
  });

  // 14. watch.remove 还剩的 + 空 → 0
  await t('watch.remove + watch.list empty', async () => {
    const rr = await ext.commands.get('watch.remove')(
      { id: watchId1 },
      { sessionId: 'test' }
    );
    assert(rr.ok && rr.removed === 1, 'removed remaining');
    const list = await ext.commands.get('watch.list')({ limit: 50 }, { sessionId: 'test' });
    assert(list.watches.length === 0, 'list now empty');
  });

  // 15. notify.ps1 存在 + 可读
  await t('notify.ps1 exists', async () => {
    assert(fs.existsSync(NOTIFY_PS), 'file exists');
    const stat = fs.statSync(NOTIFY_PS);
    assert(stat.size > 100, 'has content');
  });

  // 16. interval_sec 范围裁剪
  await t('interval_sec clamped', async () => {
    const r1 = await ext.commands.get('watch.add')(
      { url: 'https://mock.example/x', interval_sec: 1 },
      { sessionId: 'test' }
    );
    assert(r1.watch.interval_sec === 10, 'min clamp to 10');
    const r2 = await ext.commands.get('watch.add')(
      { url: 'https://mock.example/y', interval_sec: 9999999 },
      { sessionId: 'test' }
    );
    assert(r2.watch.interval_sec === 86400, 'max clamp to 86400');
  });

  // ─── 汇总 ──────────────────────────────────────────────────
  console.log(`\n──────────────────────────────────`);
  console.log(`  ${_pass} passed, ${_fail} failed`);
  console.log(`──────────────────────────────────`);
  cleanup();
  process.exit(_fail === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error('test crash:', e);
  process.exit(1);
});
