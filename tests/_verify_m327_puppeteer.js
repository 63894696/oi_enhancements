/* M3.27 Puppeteer 验证脚本 — 真走 mock 两端 + 截图 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

// 用本机 Chrome(避免 puppeteer 下载 200MB+ chromium)
const CHROME_PATH = 'C:\\Users\\Administrator\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';

const COMPANION_URL = 'http://127.0.0.1:18850/';
const PRISIRAI_URL = 'http://127.0.0.1:18800/';
const SCREENSHOT_DIR = 'C:\\Users\\Administrator\\oi_enhancements\\tests\\screenshots';

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log((ok ? '[PASS] ' : '[FAIL] ') + name + (detail ? ' — ' + detail : ''));
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: CHROME_PATH,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  try {
    // ============================================================
    // 步骤 1: 起 companion tab
    // ============================================================
    const companion = await browser.newPage();
    await companion.setViewport({ width: 1100, height: 780 });
    companion.on('console', m => console.log('  [companion console]', m.type(), m.text()));
    companion.on('pageerror', e => console.log('  [companion pageerror]', e.message));

    await companion.goto(COMPANION_URL, { waitUntil: 'networkidle2', timeout: 15000 });
    // 等 ws 连上(sidebar statusTxt 显示「已连接」)
    await new Promise(r => setTimeout(r, 1500));

    // 检查 #btnDispatch 存在 + 顶栏可见
    const btnExists = await companion.$('#btnDispatch');
    check('step 1: companion #btnDispatch 存在', !!btnExists, btnExists ? '' : 'selector not found');

    // 截图 m327_before.png(看到📤派发按钮 + 输入框)
    await companion.screenshot({ path: path.join(SCREENSHOT_DIR, 'm327_before.png'), fullPage: false });
    check('step 2: m327_before.png 截图', fs.existsSync(path.join(SCREENSHOT_DIR, 'm327_before.png')));

    // ============================================================
    // 步骤 3: 模拟用户输入一段对话,触发 ws → 写入 dispatch buffer
    // ============================================================
    // 找输入框 #in,先 focus + 输文字 + 点击 #btnSend
    await companion.waitForSelector('#in', { timeout: 5000 });
    await companion.click('#in');
    await companion.type('#in', '帮我写一个 Python 贪吃蛇');
    // 注意:这里文本不含触发词,会真发消息;后端 ws handler 会把 user+assistant 都写进 buf
    await companion.click('#btnSend');
    // 等 ai_done(看 .badge / msgs 中出现气泡)
    await new Promise(r => setTimeout(r, 2500));

    // 验 buffer 已写(用 mock 调试端点 __all__ 拿所有 sid 概况)
    const bufInfo1 = await companion.evaluate(async () => {
      const r = await fetch('/api/dispatch/buffer?sid=__all__');
      return await r.json();
    });
    const sids1 = Object.keys(bufInfo1.all || {});
    const total1 = sids1.reduce((sum, s) => sum + (bufInfo1.all[s].total || 0), 0);
    console.log('  [buf after 1 user_text] sids=' + JSON.stringify(sids1) + ' total=' + total1);
    check('step 3a: dispatch buf ≥ 2 (user + assistant)',
      total1 >= 2,
      'total=' + total1 + ' sids=' + sids1.length);

    // 再发一轮,验证增量
    await companion.click('#in');
    await companion.type('#in', '用 curses 终端版就行');
    await companion.click('#btnSend');
    await new Promise(r => setTimeout(r, 2500));

    const bufInfo2 = await companion.evaluate(async () => {
      const r = await fetch('/api/dispatch/buffer?sid=__all__');
      return await r.json();
    });
    const sids2 = Object.keys(bufInfo2.all || {});
    const total2 = sids2.reduce((sum, s) => sum + (bufInfo2.all[s].total || 0), 0);
    console.log('  [buf after 2 user_text] sids=' + JSON.stringify(sids2) + ' total=' + total2);
    check('step 3b: dispatch buf ≥ 4 (2 轮)',
      total2 >= 4,
      'total=' + total2 + ' sids=' + sids2.length);

    // ============================================================
    // 步骤 4: 点 #btnDispatch → POST /api/dispatch → mock PrisirAI 收
    // ============================================================
    await companion.click('#btnDispatch');
    await new Promise(r => setTimeout(r, 1500));

    // 验 dispatch 已被记录 + sys msg 出现
    const sysMsgText = await companion.evaluate(() => {
      const sysMsgs = Array.from(document.querySelectorAll('.sys-msg, .sys, .sysmsg, .system-msg, [class*="sys"]'))
        .map(el => el.textContent.trim())
        .filter(t => t.includes('派发'));
      const allText = document.body.textContent;
      const m = allText.match(/已派发[^◌]*?条到 PrisirAI/);
      return m ? m[0] : (sysMsgs[0] || '(none)');
    });
    console.log('  [sys msg after dispatch]', sysMsgText);
    check('step 4a: companion 显示「已派发 #1 · N 条到 PrisirAI」',
      sysMsgText.includes('已派发') && sysMsgText.includes('到 PrisirAI'),
      'sysMsg=' + sysMsgText);

    // 截图 m327_dispatched.png
    await companion.screenshot({ path: path.join(SCREENSHOT_DIR, 'm327_dispatched.png'), fullPage: false });
    check('step 4b: m327_dispatched.png 截图', fs.existsSync(path.join(SCREENSHOT_DIR, 'm327_dispatched.png')));

    // ============================================================
    // 步骤 5: 起 PrisirAI tab,等 polling 拿到 inject → 填 #input
    // ============================================================
    const prisirai = await browser.newPage();
    await prisirai.setViewport({ width: 1100, height: 780 });
    prisirai.on('console', m => console.log('  [prisirai console]', m.type(), m.text()));
    prisirai.on('pageerror', e => console.log('  [prisirai pageerror]', e.message));

    await prisirai.goto(PRISIRAI_URL, { waitUntil: 'networkidle2', timeout: 15000 });
    // 先在页面里装一个 MutationObserver 捕捉 inject-flash 触发瞬间
    await prisirai.evaluate(() => {
      window.__flashSeen = false;
      const ta = document.getElementById('input');
      if (ta) {
        const mo = new MutationObserver(muts => {
          for (const m of muts) {
            if (m.type === 'attributes' && ta.classList.contains('inject-flash')) {
              window.__flashSeen = true;
            }
          }
        });
        mo.observe(ta, { attributes: true, attributeFilter: ['class'] });
      }
    });
    // 等 polling 周期(900ms)+ DOM 更新 → 验 #input.value 非空
    await new Promise(r => setTimeout(r, 2500));

    const prisiraiState = await prisirai.evaluate(() => {
      const ta = document.getElementById('input');
      return {
        value: ta ? ta.value : null,
        valueLen: ta ? ta.value.length : 0,
        hasFlash: ta ? ta.classList.contains('inject-flash') : null,
        seenSize: (window.__injectSeen && window.__injectSeen.size) || 0,
        sendCalled: !!window.sendMessageCalled,
      };
    });
    console.log('  [prisirai state]', JSON.stringify(prisiraiState));

    // value 应含 [对话上下文 · 来自 Prisir 陪聊]
    check('step 5a: PrisirAI #input.value 非空',
      prisiraiState.valueLen > 0,
      'len=' + prisiraiState.valueLen);
    check('step 5b: PrisirAI #input.value 含「对话上下文 · 来自 Prisir 陪聊」',
      prisiraiState.value && prisiraiState.value.includes('对话上下文 · 来自 Prisir 陪聊'),
      'snippet=' + (prisiraiState.value || '').slice(0, 80));
    check('step 6: sendMessageCalled === false(不自动 send)',
      prisiraiState.sendCalled === false,
      'sendCalled=' + prisiraiState.sendCalled);

    // 等 inject-flash 触发(MutationObserver 兜底,不需要抢 1.5s 时间窗)
    const flashSeen = await prisirai.evaluate(() => !!window.__flashSeen);
    check('step 7: inject-flash class 已被加上过(1.5s 内任意时刻)',
      flashSeen === true,
      'flashSeen=' + flashSeen);

    // 截图 m327_filled.png
    await prisirai.screenshot({ path: path.join(SCREENSHOT_DIR, 'm327_filled.png'), fullPage: false });
    check('step 8: m327_filled.png 截图', fs.existsSync(path.join(SCREENSHOT_DIR, 'm327_filled.png')));

    // 顺便验 companion buffer 已经更新(cursor 推进)
    const bufAfterDispatch = await companion.evaluate(async () => {
      const r = await fetch('/api/dispatch/buffer?sid=__all__');
      return await r.json();
    });
    const sidsAfter = Object.keys(bufAfterDispatch.all || {});
    const dispatchedTotal = sidsAfter.reduce((sum, s) => sum + (bufAfterDispatch.all[s].dispatched || 0), 0);
    const dispatchesTotal = sidsAfter.reduce((sum, s) => sum + (bufAfterDispatch.all[s].dispatches || 0), 0);
    console.log('  [buf after dispatch] sids=' + JSON.stringify(sidsAfter) + ' dispatched=' + dispatchedTotal + ' dispatches=' + dispatchesTotal);
    check('step 9: companion cursor 已推进(dispatches≥1, dispatched≥2)',
      dispatchesTotal >= 1 && dispatchedTotal >= 2,
      'dispatches=' + dispatchesTotal + ' dispatched=' + dispatchedTotal);

    await companion.close();
    await prisirai.close();
  } catch (e) {
    console.log('[FATAL]', e && e.stack ? e.stack : e);
    results.push({ name: 'fatal', ok: false, detail: String(e) });
  } finally {
    await browser.close();
  }

  // ============================================================
  // 输出汇总
  // ============================================================
  console.log('\n=== 验证汇总 ===');
  let pass = 0, fail = 0;
  for (const r of results) {
    if (r.ok) pass++; else fail++;
    console.log((r.ok ? '✓' : '✗') + ' ' + r.name + (r.detail ? ' — ' + r.detail : ''));
  }
  console.log(`\n总计: ${pass} PASS, ${fail} FAIL`);

  // 截图大小
  console.log('\n=== 截图 ===');
  for (const f of ['m327_before.png', 'm327_dispatched.png', 'm327_filled.png']) {
    const fp = path.join(SCREENSHOT_DIR, f);
    if (fs.existsSync(fp)) {
      const sz = fs.statSync(fp).size;
      console.log(`${f}: ${(sz / 1024).toFixed(1)} KB`);
    } else {
      console.log(`${f}: MISSING`);
    }
  }

  process.exit(fail > 0 ? 1 : 0);
})();

