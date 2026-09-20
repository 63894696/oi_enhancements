'use strict';

/**
 * 待办 v0.1.0(独立于 zTasker,本地 JSON 持久化)
 *
 * 命令:
 *   todo.add    { title, due?, priority?, tags? }    → { item }
 *   todo.list   { status?, tag?, priority?, limit? } → { items, total, by_status }
 *   todo.done   { id }                              → { ok, item }
 *   todo.remove { id }                              → { ok }
 *   todo.update { id, ...fields }                   → { ok, item }
 *
 * 优先级:high / mid / low(默认 mid)。
 * 状态:open / done / canceled。
 * 数据:process.env.PRISIR_EXT_HOME/todo/items.json
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'todo', name: '待办事项', version: '0.1.0' });

const STORE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'items.json');

function load() {
  try {
    if (fs.existsSync(STORE())) return JSON.parse(fs.readFileSync(STORE(), 'utf8'));
  } catch {}
  return [];
}
function save(items) {
  try { fs.writeFileSync(STORE(), JSON.stringify(items, null, 2)); return true; } catch { return false; }
}
function nowIso() { return new Date().toISOString(); }

ext.registerCommand('todo.add', async (args) => {
  const title = String(args.title || '').trim();
  if (!title) return { error: 'title empty' };
  const item = {
    id: 't' + Date.now() + Math.random().toString(36).slice(2, 5),
    title,
    due: args.due || null,
    priority: ['high', 'mid', 'low'].includes(args.priority) ? args.priority : 'mid',
    tags: Array.isArray(args.tags) ? args.tags : [],
    status: 'open',
    created: nowIso(),
    updated: nowIso(),
  };
  const all = load();
  all.unshift(item);
  if (!save(all)) return { error: 'persist failed' };
  return { ok: true, item };
});

ext.registerCommand('todo.list', async (args) => {
  let items = load();
  if (args.status)  items = items.filter(i => i.status === args.status);
  if (args.tag)     items = items.filter(i => (i.tags || []).includes(args.tag));
  if (args.priority) items = items.filter(i => i.priority === args.priority);
  // 高优 → 低优,再按 due 升序(无 due 排最后)
  const rank = { high: 0, mid: 1, low: 2 };
  items.sort((a, b) => {
    const r = (rank[a.priority] ?? 1) - (rank[b.priority] ?? 1);
    if (r !== 0) return r;
    const da = a.due || '9999';
    const db = b.due || '9999';
    return da < db ? -1 : da > db ? 1 : 0;
  });
  const total = items.length;
  const lim = Number(args.limit) || 100;
  items = items.slice(0, lim);
  const by_status = items.reduce((m, i) => (m[i.status] = (m[i.status] || 0) + 1, m), {});
  return { items, total, by_status };
});

ext.registerCommand('todo.done', async (args) => {
  const all = load();
  const it = all.find(i => i.id === args.id);
  if (!it) return { ok: false, error: 'not found' };
  it.status = 'done';
  it.done_at = nowIso();
  it.updated = nowIso();
  return { ok: save(all), item: it };
});

ext.registerCommand('todo.remove', async (args) => {
  const all = load();
  const idx = all.findIndex(i => i.id === args.id);
  if (idx < 0) return { ok: false, error: 'not found' };
  all.splice(idx, 1);
  return { ok: save(all) };
});

ext.registerCommand('todo.update', async (args) => {
  const all = load();
  const it = all.find(i => i.id === args.id);
  if (!it) return { ok: false, error: 'not found' };
  for (const k of ['title', 'due', 'priority', 'tags', 'status']) {
    if (args[k] !== undefined) it[k] = args[k];
  }
  it.updated = nowIso();
  return { ok: save(all), item: it };
});

// onSessionMessage:AI 提到「待办 / todo」时,提示用户调用
ext.onSessionMessage(async (msg, ctx) => {
  if (!ctx.sessionId) return;
  const text = String(msg.text || '');
  if (/(加待办|新建待办|加入待办|新待办|添加待办|开个待办)/.test(text)) {
    const all = load().filter(i => i.status === 'open').length;
    ext.injectCard({
      sessionId: ctx.sessionId,
      cardId: `todo-${Date.now()}`,
      html: `<div class="ext-card">
  <div class="ext-card-head"><span class="ext-badge">📋 待办</span></div>
  <div class="ext-card-body" style="background:#fdfcf8;padding:14px;color:#2f3a34;font-size:13px;line-height:1.6">
    当前未完成: <b>${all}</b>。调用 <code style="background:#eee;padding:1px 6px;border-radius:3px">todo.add</code> 添加。
    <div style="margin-top:6px;color:#7a8580;font-size:11px">
      POST /api/extensions op=invoke id=todo method=todo.add params={title,priority,tags}
    </div>
  </div>
</div>`,
    });
  }
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
