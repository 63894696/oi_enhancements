// run_browser.mjs — 在真实浏览器(Chromium)里加载同一份 wasm_meter.wasm。
// 验证第三个运行时:浏览器载体侧(SecBrowser 同源技术栈)结果一致。
// 通过本地 http 服务 wasm + 一个页面,puppeteer 打开页面跑 WebAssembly。
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const wasm = readFileSync(join(here, 'target', 'wasm32-unknown-unknown', 'release', 'wasm_meter.wasm'));

const html = `<!doctype html><meta charset=utf-8><title>wasm_meter</title><script>
window.runWasm = async (bytes) => {
  const auditLog = [];
  const ops = ['open','grant','charge','charge_rejected'];
  const imports = { env: { host_audit(op, amount){ auditLog.push({op:ops[op]??('op'+op), amount:Number(amount)}); } } };
  const { instance } = await WebAssembly.instantiate(bytes, imports);
  const m = instance.exports;
  const i64 = (n) => BigInt(n);
  const actual = [];
  const acct = m.meter_open(i64(3));
  actual.push(acct);
  actual.push(Number(m.meter_balance(acct)));
  actual.push(Number(m.meter_grant(acct, i64(5))));
  actual.push(Number(m.meter_charge(acct, i64(2))));
  actual.push(Number(m.meter_charge(acct, i64(2))));
  actual.push(Number(m.meter_charge(acct, i64(2))));
  actual.push(Number(m.meter_charge(acct, i64(5))));
  actual.push(Number(m.meter_balance(acct)));
  actual.push(m.meter_count());
  return { actual, auditLog };
};
</script>`;

const server = createServer((req, res) => {
  if (req.url === '/m.wasm') {
    res.writeHead(200, { 'Content-Type': 'application/wasm' });
    res.end(wasm);
  } else {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(html);
  }
});

await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

// 动态 import puppeteer(项目里已有 puppeteer-mcp,但这里直接用库更稳)
let puppeteer;
try {
  puppeteer = (await import('puppeteer')).default;
} catch {
  console.log('BROWSER_RESULT: SKIP (puppeteer 库不可 import,用 mcp 另验)');
  server.close();
  process.exit(2);
}

const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${port}/`);
  // 把 wasm 字节传进页面跑
  const bytesArr = Array.from(wasm);
  const out = await page.evaluate(async (arr) => {
    return window.runWasm(new Uint8Array(arr));
  }, bytesArr);
  const expected = [0, 3, 8, 6, 4, 2, -2, 2, 1];
  const pass = JSON.stringify(out.actual) === JSON.stringify(expected);
  console.log('=== wasm_meter @ Browser(Chromium) ===');
  console.log('actual  :', JSON.stringify(out.actual));
  console.log('audit   :', JSON.stringify(out.auditLog));
  console.log(pass ? 'BROWSER_RESULT: PASS' : 'BROWSER_RESULT: FAIL');
  process.exitCode = pass ? 0 : 1;
} finally {
  await browser.close();
  server.close();
}
