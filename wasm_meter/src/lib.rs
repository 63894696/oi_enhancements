//! wasm_meter — 三件套 meter 记账能力的 WASM 模块。
//!
//! 验证"L2 统一运行时"核心判断:同一能力字节码,跨运行时(Node/Python/浏览器)
//! 跑出相同结果;capability 安全模型——wasm 只能调用 host 显式注入的能力。
//!
//! 设计:无 std 依赖的最小核心,记账状态放 wasm 线性内存(确定性)。
//! 账户表定长,操作:grant / charge / balance / count。

#![no_std]
#![no_main]

use core::panic::PanicInfo;

// ── capability 导入:host 注入的受限能力 ────────────────────────────
// wasm 模块默认无任何能力;要用宿主资源(这里是"记审计日志"),
// 必须由 host 在实例化时显式提供这个导入函数 —— 这就是 capability 模型:
// 没有授权(导入)的能力,wasm 根本触碰不到。对应三件套的 authz。
extern "C" {
    /// host 提供的审计能力:记录一条 (op, amount) 事件。
    /// host 可选择不提供此导入 → 链接期就失败,能力被硬拒绝。
    fn host_audit(op: u32, amount: i64);
}

// ── 账户表(定长,确定性;真实系统换 KV/存储)─────────────────────────
const MAX_ACCOUNTS: usize = 16;

struct State {
    // 平行数组:balance[i] 对应一个账户;count 为已用槽位数。
    balances: [i64; MAX_ACCOUNTS],
    count: u32,
}

// no_std 下用静态可变状态(单线程 wasm,无并发问题)。
static mut STATE: State = State {
    balances: [0; MAX_ACCOUNTS],
    count: 0,
};

// ── 对外 ABI(导出给 host 调用)────────────────────────────────────
// 返回码统一语义:
//   >=0 : 成功,返回当前余额(或计数)
//   -1  : 账户表满 / 账户不存在(视操作)
//   -2  : 配额不足(charge 拒付,对应 x402 的 402 语义)

/// 开账户(grant 初始配额)。返回新账户 id;-1 表满。
#[no_mangle]
pub extern "C" fn meter_open(initial_quota: i64) -> i32 {
    unsafe {
        if STATE.count as usize >= MAX_ACCOUNTS {
            return -1;
        }
        let id = STATE.count;
        STATE.balances[id as usize] = initial_quota;
        STATE.count += 1;
        host_audit(0, initial_quota); // op=0: open
        id as i32
    }
}

/// 充值(grant 追加配额)。返回新余额;-1 账户不存在。
#[no_mangle]
pub extern "C" fn meter_grant(account: u32, amount: i64) -> i64 {
    unsafe {
        if account >= STATE.count {
            return -1;
        }
        STATE.balances[account as usize] += amount;
        host_audit(1, amount); // op=1: grant
        STATE.balances[account as usize]
    }
}

/// 扣费(charge)。余额不足拒付返 -2(x402 402 语义);成功返新余额。
#[no_mangle]
pub extern "C" fn meter_charge(account: u32, amount: i64) -> i64 {
    unsafe {
        if account >= STATE.count {
            return -1;
        }
        let bal = STATE.balances[account as usize];
        if bal < amount {
            host_audit(3, amount); // op=3: charge_rejected (402)
            return -2;
        }
        STATE.balances[account as usize] = bal - amount;
        host_audit(2, amount); // op=2: charge
        STATE.balances[account as usize]
    }
}

/// 查余额。账户不存在返 -1。
#[no_mangle]
pub extern "C" fn meter_balance(account: u32) -> i64 {
    unsafe {
        if account >= STATE.count {
            return -1;
        }
        STATE.balances[account as usize]
    }
}

/// 账户数。
#[no_mangle]
pub extern "C" fn meter_count() -> u32 {
    unsafe { STATE.count }
}

#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
    loop {}
}
