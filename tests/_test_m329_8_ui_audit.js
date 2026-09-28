/* M3.29.8 语伴页面 UI/UX 审计 — Puppeteer 真走 + 截图

覆盖用户 9 条反馈:
  1. 品牌定位:title="语伴" / avatar="语"
  2. 计时:初始 00:00 + 不启动不累加;点启动后累加;点挂断归零 + 不再累加
  3. 历史侧栏 🗑 单条删除 + 二次确认
  4. 音乐 UI 完全去除(无 #btnMusicLauncher / 无 #music-bar)
  5. settings 不再累积 "已配" grp
  6. settings 下拉 = ASR 厂商(非 LLM)
  7. settings 厂商字段含 url 端点栏
  8. settings 测试连接:空必填字段必返失败(非"连接 OK")
  9. settings 标题无 "(M3.24)" / "(M3.27)"
*/

const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const CHROME_PATH = 'C:\\Users\\Administrator\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';
const COMPANION_URL = 'http://127.0.0.1:18850/';
const SCREENSHOT_DIR = 'C:\\Users\\Administrator\\oi_enhancements\\tests\\screenshots';

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log((ok ? '[PASS] ' : '[FAIL] ') + name + (detail ? ' — ' + detail : ''));
}

async function shot(page, name) {
  try {
    if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, `m3298_${name}.png`), fullPage: false });
  } catch (e) { console.log('  shot err:', e.message); }
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: CHROME_PATH,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  let pass = 0, fail = 0;

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    page.on('console', (msg) => {
      const t = msg.type();
      if (t === 'error' || t === 'warn') {
        console.log(`  [console.${t}]`, msg.text().slice(0, 200));
      }
    });

    // ============================================================
    // T1 — 标题 + avatar = "语"
    // ============================================================
    await page.goto(COMPANION_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForSelector('#btnStart', { timeout: 8000 });
    await new Promise(r => setTimeout(r, 500));
    await shot(page, '01_initial');

    const title = await page.title();
    const hasYuban = title.includes('语伴');
    const noPeiliao = !title.includes('陪聊');
    check('T1.title 是"语伴"且无"陪聊"', hasYuban && noPeiliao, `title=${title}`);

    const avatarText = await page.$eval('#avatar', el => el.textContent.trim());
    check('T1.avatar 文字 = "语"', avatarText === '语', `avatar=${avatarText}`);

    const headerName = await page.$eval('.header-info .name', el => el.textContent);
    check('T1.header.name 含 "Prisir 语伴"', headerName.includes('Prisir 语伴'),
          `name=${headerName}`);

    // ============================================================
    // T2 — 计时:初始 00:00,1s 后仍 00:00(不启动不累加)
    // ============================================================
    const t1 = await page.$eval('#timer', el => el.textContent);
    await new Promise(r => setTimeout(r, 1500));
    const t2 = await page.$eval('#timer', el => el.textContent);
    check('T2.timer 初始 00:00 且不启动不累加', t1 === '00:00' && t2 === '00:00',
          `t1=${t1} t2=${t2}`);

    // ============================================================
    // T3 — 点启动后计时累加;点挂断后归零且不再累加
    // ============================================================
    await page.click('#btnStart');
    await new Promise(r => setTimeout(r, 2200));
    const tStart1 = await page.$eval('#timer', el => el.textContent);
    await new Promise(r => setTimeout(r, 1100));
    const tStart2 = await page.$eval('#timer', el => el.textContent);
    check('T3.timer 启动后累加', tStart1 !== '00:00' && tStart2 !== '00:00' && tStart2 > tStart1,
          `tStart1=${tStart1} tStart2=${tStart2}`);
    await shot(page, '02_started');

    await page.click('#btnStart');  // 再点切到挂断
    await new Promise(r => setTimeout(r, 800));
    const tStop1 = await page.$eval('#timer', el => el.textContent);
    await new Promise(r => setTimeout(r, 1500));
    const tStop2 = await page.$eval('#timer', el => el.textContent);
    check('T3.timer 挂断后归零且不再累加', tStop1 === '00:00' && tStop2 === '00:00',
          `tStop1=${tStop1} tStop2=${tStop2}`);
    await shot(page, '03_hungup');

    const btnText = await page.$eval('#btnStart', el => el.textContent.trim());
    check('T3.btnStart 回到 "▶ 启动"', btnText.includes('启动'), `btn=${btnText}`);

    // ============================================================
    // T4 — 音乐 UI 完全去除
    // ============================================================
    const musicLauncher = await page.$('#btnMusicLauncher');
    check('T4.无 #btnMusicLauncher', !musicLauncher, musicLauncher ? '存在' : 'OK');

    const musicBar = await page.$('#music-bar');
    check('T4.无 #music-bar', !musicBar, musicBar ? '存在' : 'OK');

    const musicLaunchToast = await page.$('#musicLaunchToast');
    check('T4.无 #musicLaunchToast', !musicLaunchToast, musicLaunchToast ? '存在' : 'OK');

    // ============================================================
    // T5 — header 按钮数 = 5 个(启动 / 历史 / 设置 / 派发 / emoji)+ TTS
    // ============================================================
    const headerBtns = await page.$$eval('header button.hdr-btn',
      els => els.map(e => e.id || e.textContent.trim()).filter(t => t));
    // 期望:btnStart btnHistory btnTts btnSettings btnDispatch btnEmoji  = 6
    const expected = ['btnStart', 'btnHistory', 'btnTts', 'btnSettings', 'btnDispatch', 'btnEmoji'];
    const missing = expected.filter(id => !headerBtns.some(b => b === id));
    check('T5.header 6 个按钮齐全,无音乐', missing.length === 0,
          `actual=${headerBtns.length} missing=${missing.join(',')}`);

    // ============================================================
    // T6 — settings 打开后无 "已配" grp 重复
    // ============================================================
    // 先点开 settings 5 次,看是否堆积
    for (let i = 0; i < 5; i++) {
      await page.click('#btnSettings');
      await new Promise(r => setTimeout(r, 200));
    }
    await new Promise(r => setTimeout(r, 800));
    await shot(page, '04_settings_open');

    // 不应该有任何 "已配:" 字样
    const settingsHtml = await page.$eval('#settingsPanel', el => el.innerHTML);
    const hasConfigured = settingsHtml.includes('已配:') || settingsHtml.includes('已配');
    check('T6.settings 面板无 "已配" 重复 grp', !hasConfigured,
          hasConfigured ? '仍含 "已配"' : 'OK');

    // ============================================================
    // T7 — settings 下拉 = ASR 厂商(非 LLM)
    // ============================================================
    const opts = await page.$$eval('#selProvider option',
      els => els.map(e => e.textContent.trim()));
    const isAsrCatalog = opts.some(t => /Paraformer|百炼|SenseVoice|FunASR|本地|Whisper|OpenAI|百度|讯飞|阿里云|腾讯|字节|微软|Google|Azure|AWS|华为|京东/i.test(t));
    check('T7.settings 下拉含 ASR 厂商(Paraformer/百炼 等)', isAsrCatalog,
          `opts=${JSON.stringify(opts.slice(0, 5))}...`);

    // ============================================================
    // T8 — settings 字段含 url 端点栏
    // ============================================================
    // 选 local-funasr(它有 endpoint 字段),不用 optValues[0](现在第一个非空是 bailian,只有 api_key)
    await page.select('#selProvider', 'local-funasr');
    await new Promise(r => setTimeout(r, 400));
    const fields = await page.$$eval('#providerFields input',
      els => els.map(e => ({ name: e.getAttribute('data-field'), type: e.type })));
    const hasUrl = fields.some(f => f.name === 'base_url' || f.name === 'endpoint');
    check('T8.选 local-funasr 后字段含 url 端点栏(endpoint)',
          hasUrl, `fields=${JSON.stringify(fields.map(f => f.name))}`);

    // ============================================================
    // T9 — 测试连接:空必填字段必返失败(非"连接 OK")
    // ============================================================
    await page.click('#btnTest');
    await new Promise(r => setTimeout(r, 3500));   // 等后端真握手
    const testOut = await page.$eval('#testOut', el => ({
      text: el.textContent,
      cls: el.className,
    }));
    const isFail = testOut.text.includes('必填') || testOut.text.includes('❌');
    check('T9.空必填测试连接必返失败', isFail, `testOut=${testOut.text}`);
    await shot(page, '05_test_empty');

    // ============================================================
    // T10 — settings 标题无 "(M3.24)" / "(M3.27)" 残留
    // ============================================================
    const settingsTxt = await page.$eval('#settingsPanel', el => el.textContent);
    const noDevLabel = !settingsTxt.includes('(M3.24)') && !settingsTxt.includes('(M3.27)');
    check('T10.settings 标题无 M3.x 模块命名', noDevLabel,
          noDevLabel ? 'OK' : `txt=${settingsTxt.slice(0, 80)}`);

    // 关掉 settings 面板
    await page.click('#btnSettingsClose');
    await new Promise(r => setTimeout(r, 300));

    // ============================================================
    // T11 — 历史侧栏 🗑 单条删除
    // ============================================================
    // 注:在没有真实聊天的环境,历史可能为空。验证:
    //  (a) 打开历史侧栏能跑通
    //  (b) 不论有无数据,🗑 按钮存在与否应与 row 数量一致
    //  (c) 后端 DELETE /api/sessions?sid=fake → 返 ok:true 但 removed:false
    await page.click('#btnHistory');
    await new Promise(r => setTimeout(r, 600));
    await shot(page, '06_history');

    const sessCount = await page.$$eval('#sessionsList .sessitem', els => els.length);
    const delCount = await page.$$eval('#sessionsList .sess-del', els => els.length);
    check('T11.历史侧栏每条 sessitem 有对应 🗑 按钮',
          sessCount === delCount, `sess=${sessCount} del=${delCount}`);

    // 直接调后端 DELETE,确认能返回正确 JSON
    const delResult = await page.evaluate(async () => {
      const r = await fetch('/api/sessions?sid=__test_fake_sid_xyz__', { method: 'DELETE' });
      return await r.json();
    });
    check('T11.DELETE /api/sessions?sid=xxx 后端返 ok',
          delResult.ok === true, JSON.stringify(delResult));

    // 关掉
    await page.click('#btnHistory');
    await new Promise(r => setTimeout(r, 200));

    // ============================================================
    // T12-T17 — M3.29.9 settings 4 段折叠 + optgroup + prep_hint + 派发测按钮 + URL override
    // ============================================================
    // 打开 settings 面板
    await page.click('#btnSettings');
    await new Promise(r => setTimeout(r, 800));

    // T12 — settings 有 4 个 <details>
    const detailsCount = await page.$$eval('#settingsPanel details', els => els.length);
    check('T12.settings 4 段折叠 details', detailsCount === 4, `details=${detailsCount}`);

    // T13 — ASR 段默认展开,其它默认收起
    const openSummaries = await page.$$eval('#settingsPanel details[open] summary',
      els => els.map(e => e.textContent.trim()));
    check('T13.ASR 段默认展开(其它收起)',
          openSummaries.some(t => t.includes('ASR')),
          `open=${JSON.stringify(openSummaries)}`);

    // T14 — selProvider 有 2 个 <optgroup>(简单组 + 高级组)
    const optgroups = await page.$$eval('#selProvider optgroup',
      els => els.map(e => e.getAttribute('label')));
    check('T14.selProvider 有 2 组(简单组 + 高级组)',
          optgroups.length === 2 && optgroups[0].includes('简单组') && optgroups[1].includes('高级组'),
          `optgroups=${JSON.stringify(optgroups)}`);

    // T15 — 选 qwen3-asr-flash 后字段区有黄色 prep_hint 含 DASHSCOPE_WORKSPACE_ID
    await page.select('#selProvider', 'qwen3-asr-flash');
    await new Promise(r => setTimeout(r, 400));
    const prepText = await page.$eval('#providerFields',
      el => el.textContent || '');
    check('T15.选 qwen3-asr-flash 显示 prep_hint 含 DASHSCOPE_WORKSPACE_ID',
          prepText.includes('DASHSCOPE_WORKSPACE_ID'),
          prepText.length > 100 ? '含 hint' : `short: ${prepText.slice(0,80)}`);
    await shot(page, '07_prep_hint');

    // T16 — 选 local-funasr → friendly error(不需真起 server,直接点测试会失败)
    await page.select('#selProvider', 'local-funasr');
    await new Promise(r => setTimeout(r, 400));
    const localPrepText = await page.$eval('#providerFields', el => el.textContent || '');
    check('T16.选 local-funasr 显示 sherpa-onnx 准备提示',
          localPrepText.includes('sherpa-onnx'),
          localPrepText.length > 50 ? 'OK' : `short: ${localPrepText.slice(0,80)}`);

    // T17 — 派发段:无 btnM327DispatchAll,有 btnM327Test,真打 /api/dispatch/test
    // 先展开派发段 <details>(默认收起)并强制滚动到视区
    await page.evaluate(() => {
      const d = document.querySelector('#settingsPanel details.m327-section');
      if (d) { d.open = true; d.scrollIntoView({block:'center'}); }
      const b = document.querySelector('#btnM327Test');
      if (b) b.scrollIntoView({block:'center'});
    });
    await new Promise(r => setTimeout(r, 600));

    const hasDispatchAll = await page.$('#btnM327DispatchAll');
    const hasTestBtn = await page.$('#btnM327Test');
    check('T17.派发段无 btnM327DispatchAll + 有 btnM327Test',
          !hasDispatchAll && !!hasTestBtn,
          `old=${!!hasDispatchAll} new=${!!hasTestBtn}`);

    // 真点测试 PrisirAI 连接 → m327Status 应有 "可达"/"不可达"
    // 用 evaluate 直接 click,避免 details 嵌套 + viewport 边缘带来的"not clickable"
    await page.evaluate(() => {
      const b = document.querySelector('#btnM327Test');
      if (b) b.click();
    });
    await new Promise(r => setTimeout(r, 3500));
    const m327Status = await page.$eval('#m327Status', el => el.textContent || '');
    check('T17b.点测试 PrisirAI 连接 → m327Status 含 可达/不可达',
          /可达|不可达/.test(m327Status),
          m327Status.slice(0, 80));
    await shot(page, '08_dispatch_test');

    // T18 — 高级段默认收起;展开后 input 含 placeholder "18802"
    const advancedOpen = await page.$eval(
      '#settingsPanel details.m327-advanced-section',
      el => el.hasAttribute('open'));
    check('T18.高级段默认收起', !advancedOpen, `open=${advancedOpen}`);

    await page.evaluate(() => {
      const d = document.querySelector('#settingsPanel details.m327-advanced-section');
      if (d) d.open = true;
    });
    await new Promise(r => setTimeout(r, 300));
    const placeholder = await page.$eval('#m327PrisiraiUrl',
      el => el.getAttribute('placeholder') || '');
    check('T18b.展开高级段后 input placeholder 含 18802',
          placeholder.includes('18802'),
          `ph=${placeholder}`);
    await shot(page, '09_advanced_open');

    // T19 — 高级段存 URL override(空字符串 = 恢复默认;设个假端口)
    await page.evaluate(() => {
      const u = document.querySelector('#m327PrisiraiUrl');
      if (u) { u.value = 'http://127.0.0.1:65530/test'; }
      const b = document.querySelector('#btnM327UrlSave');
      if (b) b.scrollIntoView({block:'center'});
    });
    await new Promise(r => setTimeout(r, 300));
    await page.evaluate(() => {
      const b = document.querySelector('#btnM327UrlSave');
      if (b) b.click();
    });
    await new Promise(r => setTimeout(r, 800));
    const urlStatus = await page.$eval('#m327UrlStatus', el => el.textContent || '');
    check('T19.保存 URL override → m327UrlStatus 显示 已保存 + 当前生效',
          urlStatus.includes('已保存') && urlStatus.includes('65530'),
          urlStatus.slice(0, 80));

    // 恢复默认
    await page.evaluate(() => {
      const b = document.querySelector('#btnM327UrlReset');
      if (b) b.click();
    });
    await new Promise(r => setTimeout(r, 800));
    const urlStatusReset = await page.$eval('#m327UrlStatus', el => el.textContent || '');
    const inputValAfterReset = await page.$eval('#m327PrisiraiUrl', el => el.value);
    check('T19b.恢复默认 → input 清空 + status 仍 ok',
          inputValAfterReset === '' && urlStatusReset.includes('已保存'),
          `val="${inputValAfterReset}" status="${urlStatusReset.slice(0, 60)}"`);

    // 关掉 settings
    await page.evaluate(() => {
      const b = document.querySelector('#btnSettingsClose');
      if (b) b.click();
    });
    await new Promise(r => setTimeout(r, 200));

  } catch (e) {
    check('exception', false, e.message + '\n' + (e.stack || '').slice(0, 400));
  } finally {
    await browser.close();
  }

  pass = results.filter(r => r.ok).length;
  fail = results.filter(r => !r.ok).length;
  console.log('\n' + '='.repeat(60));
  console.log(`M3.29.8+9 语伴 UI 审计 ${pass}/${results.length} PASS`);
  console.log('='.repeat(60));
  if (fail > 0) {
    console.log('\nFAIL:');
    results.filter(r => !r.ok).forEach(r => console.log('  ✗', r.name, '—', r.detail));
  }
  process.exit(fail === 0 ? 0 : 1);
})();