'use strict';

/**
 * 快速笔记 v0.1.0
 *
 * 存储:~/.prisir/extensions/quick-note/notes.json(主进程 spawn 时已建)
 * 命令:add / list / get / search / delete
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'quick-note', name: '快速笔记', version: '0.1.0' });

const STORE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'notes.json');

function load() {
  try {
    if (fs.existsSync(STORE())) return JSON.parse(fs.readFileSync(STORE(), 'utf8'));
  } catch {}
  return [];
}

function save(items) {
  try {
    fs.writeFileSync(STORE(), JSON.stringify(items, null, 2));
    return true;
  } catch (e) { return false; }
}

ext.registerCommand('note.add', async (args) => {
  const title = String(args.title || '').trim() || `note ${new Date().toISOString().slice(0,10)}`;
  const body = String(args.body || '');
  const tags = Array.isArray(args.tags) ? args.tags : [];
  const item = {
    id: `n${Date.now()}`,
    title, body, tags,
    created: new Date().toISOString(),
  };
  const all = load();
  all.unshift(item);
  if (!save(all)) return { error: 'persist failed' };
  return { ok: true, note: item };
});

ext.registerCommand('note.list', async (args) => {
  const items = load().slice(0, Number(args.limit) || 50);
  return { items, total: items.length };
});

ext.registerCommand('note.get', async (args) => {
  const all = load();
  const note = all.find(n => n.id === args.id);
  if (!note) return { error: 'not found' };
  return { note };
});

ext.registerCommand('note.search', async (args) => {
  const q = String(args.q || '').toLowerCase();
  const all = load();
  const items = q ? all.filter(n =>
    (n.title + ' ' + n.body + ' ' + (n.tags || []).join(' ')).toLowerCase().includes(q)
  ) : all;
  return { items: items.slice(0, 50), total: items.length };
});

ext.registerCommand('note.delete', async (args) => {
  const all = load();
  const idx = all.findIndex(n => n.id === args.id);
  if (idx < 0) return { ok: false, error: 'not found' };
  all.splice(idx, 1);
  return { ok: save(all) };
});

// onSessionMessage:AI 说「记一下」/ 「note」时,推卡片提示用户新建
ext.onSessionMessage(async (msg, ctx) => {
  if (!ctx.sessionId) return;
  const text = String(msg.text || '');
  if (/(记一下|记一笔|note\s+it)/i.test(text)) {
    ext.injectCard({
      sessionId: ctx.sessionId,
      cardId: `note-${Date.now()}`,
      html: `<div class="ext-card">
  <div class="ext-card-head">
    <span class="ext-badge">📝 快速笔记</span>
  </div>
  <div class="ext-card-body" style="background:#fdfcf8;padding:14px;color:#2f3a34;font-size:13px;line-height:1.6">
    AI 提到要记一笔 → 调用 <code style="background:#eee;padding:1px 6px;border-radius:3px">note.add</code> 即可。
    <div style="margin-top:8px;color:#7a8580;font-size:11px">
      调用:<code style="font-size:11px">POST /api/extensions op=invoke id=quick-note method=note.add params={title,body,tags}</code>
    </div>
  </div>
</div>`,
    });
  }
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
