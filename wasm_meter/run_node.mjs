// run_node.mjs — 在 Node 原生 WebAssembly 运行时加载 wasm_meter。
// 验证:L2 统一运行时 —— 同一份 .wasm 字节码,在 Node 跑出预期记账结果;
// capability 安全模型 —— wasm 只能通过注入的导入函数触碰宿主能力。
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const wasmPath = join(here, 'target', 'wasm32-unknown-unknown', 'release', 'wasm_meter.wasm');
const bytes = readFileSync(wasmPath);

// ── capability 注入:host 决定给 wasm 哪些能力 ─────────────────────
const auditLog = [];
const imports = {
  env: {
    host_audit(op, amount) {
      const ops = ['open', 'grant', 'charge', 'charge_rejected'];
      auditLog.push({ op: ops[op] ?? `op${op}`, amount: Number(amount) });
    },
  },
};

const { instance } = await WebAssembly.instantiate(bytes, imports);
const m = instance.exports;

// i64 参数需传 BigInt;i32 仍是 number。
const i64 = (n) => BigInt(n);
const results = [];
const acct = m.meter_open(i64(3));              // 开户,配额 3
results.push(['open(3) acct', acct]);
results.push(['balance', Number(m.meter_balance(acct))]);
results.push(['grant(+5)', Number(m.meter_grant(acct, i64(5)))]);   // 8
results.push(['charge(2)', Number(m.meter_charge(acct, i64(2)))]);  // 6
results.push(['charge(2)', Number(m.meter_charge(acct, i64(2)))]);  // 4
results.push(['charge(2)', Number(m.meter_charge(acct, i64(2)))]);  // 2
const reject = Number(m.meter_charge(acct, i64(5))); // 2<5 拒付 → -2 (402)
results.push(['charge(5) reject', reject]);
results.push(['balance(final)', Number(m.meter_balance(acct))]);
results.push(['count', m.meter_count()]);

const expected = [0, 3, 8, 6, 4, 2, -2, 2, 1];
const actual = results.map(([, v]) => v);
const pass = JSON.stringify(actual) === JSON.stringify(expected);

console.log('=== wasm_meter @ Node ===');
for (const [label, v] of results) console.log(`  ${label.padEnd(18)} = ${v}`);
console.log('audit trail:', JSON.stringify(auditLog));
console.log('expected:', JSON.stringify(expected));
console.log('actual  :', JSON.stringify(actual));
console.log(pass ? 'NODE_RESULT: PASS' : 'NODE_RESULT: FAIL');

writeFileSync(join(here, '_result_node.json'), JSON.stringify({ actual, auditLog }));
process.exit(pass ? 0 : 1);
