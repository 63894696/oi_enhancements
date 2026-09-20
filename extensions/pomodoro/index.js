'use strict';

/**
 * 番茄钟 v0.1.0
 *
 * 命令:
 *   pomo.start { work?, break? }   → { state, ends_at }
 *   pomo.stop  {}                  → { state, completed_today }
 *   pomo.status {}                 → { state, remaining_s, ends_at, completed_today }
 *   pomo.stats  {}                 → { today, all }
 *
 * 计时:setInterval,每秒检测一次,完成自动切状态。
 * 持久化:~/.../pomodoro/stats.json(今日完成数 + 累计)。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'pomodoro', name: '番茄钟', version: '0.1.0' });

const STATE_FILE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'stats.json');

function todayKey() { return new Date().toISOString().slice(0, 10); }
function loadStats() {
  try {
    if (fs.existsSync(STATE_FILE())) return JSON.parse(fs.readFileSync(STATE_FILE(), 'utf8'));
  } catch {}
  return { today: {}, all: 0 };
}
function saveStats(s) {
  try { fs.writeFileSync(STATE_FILE(), JSON.stringify(s, null, 2)); } catch {}
}

let timer = null;
let active = {
  state: 'idle',           // idle | work | break
  started_at: 0,
  ends_at: 0,
  work_ms: 25 * 60 * 1000,
  break_ms: 5 * 60 * 1000,
};

function ensureTimer() {
  if (timer) return;
  timer = setInterval(() => {
    if (active.state === 'idle') return;
    if (Date.now() >= active.ends_at) {
      // 完成一段
      if (active.state === 'work') {
        const s = loadStats();
        s.today[todayKey()] = (s.today[todayKey()] || 0) + 1;
        s.all = (s.all || 0) + 1;
        saveStats(s);
        ext.injectCard({ cardId: `pomo-${Date.now()}`, html:
          `<div class="ext-card"><div class="ext-card-head"><span class="ext-badge">🍅 番茄完成</span></div>` +
          `<div class="ext-card-body" style="background:#fdfcf8;padding:14px;color:#2f3a34;font-size:13px">` +
          `工作段完成!今日累计 <b>${s.today[todayKey()]}</b> 个,休息 5 分钟。</div></div>` });
        active.state = 'break';
        active.ends_at = Date.now() + active.break_ms;
      } else {
        ext.injectCard({ cardId: `pomo-${Date.now()}`, html:
          `<div class="ext-card"><div class="ext-card-head"><span class="ext-badge">🍅 休息结束</span></div>` +
          `<div class="ext-card-body" style="background:#fdfcf8;padding:14px;color:#2f3a34;font-size:13px">` +
          `休息结束,准备开始下一个工作段。</div></div>` });
        active.state = 'idle';
        active.ends_at = 0;
      }
    }
  }, 1000);
}

ext.registerCommand('pomo.start', async (args) => {
  if (args.work) active.work_ms = Math.max(60_000, Number(args.work) * 60_000);
  if (args.break_) active.break_ms = Math.max(60_000, Number(args.break_) * 60_000);
  if (args.break)  active.break_ms = Math.max(60_000, Number(args.break)  * 60_000);
  active.state = 'work';
  active.started_at = Date.now();
  active.ends_at = active.started_at + active.work_ms;
  ensureTimer();
  return { state: active.state, ends_at: active.ends_at, work_min: active.work_ms / 60000 };
});

ext.registerCommand('pomo.stop', async () => {
  active.state = 'idle';
  active.ends_at = 0;
  const s = loadStats();
  return { state: active.state, completed_today: s.today[todayKey()] || 0 };
});

ext.registerCommand('pomo.status', async () => {
  ensureTimer();
  const remaining = active.state === 'idle' ? 0 : Math.max(0, active.ends_at - Date.now());
  const s = loadStats();
  return {
    state: active.state,
    remaining_s: Math.round(remaining / 1000),
    ends_at: active.ends_at || null,
    completed_today: s.today[todayKey()] || 0,
  };
});

ext.registerCommand('pomo.stats', async () => {
  const s = loadStats();
  return { today: s.today[todayKey()] || 0, all: s.all || 0, by_day: s.today };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
