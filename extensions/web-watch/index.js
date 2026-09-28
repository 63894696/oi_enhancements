'use strict';

/**
 * web-watch v0.1.0 — URL 内容监控 + Windows toast 通知
 *
 * 行为:
 *   - 周期性 fetch 用户添加的 URL(prisir_work.web_fetch 统一门面)
 *   - normalize + SHA256 比对,内容变化 / 抓取失败 / 抓取恢复 都写 notifications 表
 *   - 抓取失败 → Windows toast(不阻塞主流程,失败只 log)
 *   - 零 npm 依赖:node:sqlite + child_process + crypto
 *   - 暴露命令见 README.md
 *
 * ── 来源信息途径越多越好,先有东西再处理 ──
 *   - web_fetch.fetch 失败 / 超时 → 写 fetch_failed 通知,last_hash 不动
 *   - 上次失败本次成功 → 写 recovered 通知,继续比对 hash
 *   - SQLite 写失败 → log warning,不抛
 *   - toast 失败 → log warning,不抛
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { PrisIrExt } = require('@prisir/extension-sdk');

const ext = new PrisIrExt({
  id: 'web-watch',
  name: 'URL 监控 + Windows toast 通知',
  version: '0.1.0',
});

// ─── 持久化目录 ─────────────────────────────────────────────────
const HOME = () => process.env.PRISIR_EXT_HOME || '.';
const DB_PATH = () => path.join(HOME(), 'state.db');

// ─── SQLite ────────────────────────────────────────────────────
let _db = null;
function db() {
  if (_db) return _db;
  const { DatabaseSync } = require('node:sqlite');
  fs.mkdirSync(HOME(), { recursive: true });
  _db = new DatabaseSync(DB_PATH());
  _db.exec(`
    CREATE TABLE IF NOT EXISTS watches (
      id              TEXT PRIMARY KEY,
      url             TEXT NOT NULL,
      selector        TEXT NOT NULL DEFAULT '',
      interval_sec    INTEGER NOT NULL DEFAULT 600,
      last_hash       TEXT NOT NULL DEFAULT '',
      last_checked_at INTEGER NOT NULL DEFAULT 0,
      last_changed_at INTEGER NOT NULL DEFAULT 0,
      created_at      INTEGER NOT NULL,
      updated_at      INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notifications (
      id          TEXT PRIMARY KEY,
      watch_id    TEXT NOT NULL,
      url         TEXT NOT NULL,
      detected_at INTEGER NOT NULL,
      diff_kind   TEXT NOT NULL,
      old_hash    TEXT NOT NULL DEFAULT '',
      new_hash    TEXT NOT NULL DEFAULT '',
      summary     TEXT NOT NULL DEFAULT '',
      read        INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_notif_watch ON notifications(watch_id);
    CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(read, detected_at DESC);
  `);
  return _db;
}

function safePrepare(sql) {
  try { return db().prepare(sql); }
  catch (e) { ext.log('warn', `prepare fail: ${e.message}`); return null; }
}

function safeRun(stmt, ...args) {
  if (!stmt) return { changes: 0 };
  try { return stmt.run(...args); }
  catch (e) { ext.log('warn', `db run fail: ${e.message}`); return { changes: 0 }; }
}

function safeAll(stmt, ...args) {
  if (!stmt) return [];
  try { return stmt.all(...args); }
  catch (e) { ext.log('warn', `db all fail: ${e.message}`); return []; }
}

// ─── 工具 ──────────────────────────────────────────────────────
function newId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeContent(raw, selector) {
  let text = String(raw == null ? '' : raw);
  // strip HTML tags only if looks like HTML
  if (/<[a-z][\s\S]*>/i.test(text)) {
    text = text.replace(/<script[\s\S]*?<\/script>/gi, ' ');
    text = text.replace(/<style[\s\S]*?<\/style>/gi, ' ');
    text = text.replace(/<!--[\s\S]*?-->/g, ' ');
    text = text.replace(/<[^>]+>/g, ' ');
  }
  if (selector && typeof selector === 'string' && selector.trim()) {
    // very rough: try to extract selector text once
    const sel = selector.trim();
    const tagMatch = sel.match(/^([a-zA-Z][a-zA-Z0-9]*)/);
    const idMatch = sel.match(/#([\w-]+)/);
    const classMatch = sel.match(/\.([\w-]+)/);
    let fragment = '';
    if (idMatch) {
      const re = new RegExp(`id=["']${idMatch[1]}["'][\\s\\S]*?<\\/\\w+>`, 'i');
      const m = text.match(re);
      if (m) fragment = m[0];
    } else if (tagMatch) {
      const tag = tagMatch[1];
      const re = new RegExp(`<${tag}[\\s\\S]*?<\\/${tag}>`, 'i');
      const m = text.match(re);
      if (m) fragment = m[0];
    } else if (classMatch) {
      const re = new RegExp(`class=["'][^"']*\\b${classMatch[1]}\\b[^"']*["'][\\s\\S]*?<\\/\\w+>`, 'i');
      const m = text.match(re);
      if (m) fragment = m[0];
    }
    if (fragment) text = fragment;
  }
  // collapse whitespace
  text = text.replace(/\s+/g, ' ').trim();
  // truncation 256KB 防 hash 失控
  if (text.length > 256 * 1024) text = text.slice(0, 256 * 1024);
  return text;
}

function sha256(s) {
  return crypto.createHash('sha256').update(String(s), 'utf8').digest('hex');
}

// ─── fetch 复用 web_fetch.py ───────────────────────────────────
// 找仓库根:从 __dirname 一路往上找直到看到 prisir_work/web_fetch.py
function repoRoot() {
  let dir = __dirname;
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(dir, 'prisir_work', 'web_fetch.py'))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

function fetchUrl(url, timeoutSec = 10) {
  return new Promise((resolve) => {
    let py;
    const root = repoRoot();
    const env = Object.assign({}, process.env, {
      PYTHONPATH: root + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ''),
      PYTHONIOENCODING: 'utf-8',
    });
    try {
      py = spawn('python', [
        '-c',
        `from prisir_work.web_fetch import fetch as _f; import sys,json; print(json.dumps(_f(sys.argv[1], {'timeout': ${Number(timeoutSec) || 10}})))`,
        String(url || ''),
      ], { stdio: ['ignore', 'pipe', 'pipe'], env, cwd: root });
    } catch (e) {
      resolve({ ok: false, error: 'spawn_failed: ' + (e.message || e.code || '') });
      return;
    }
    let out = '', err = '';
    const timer = setTimeout(() => {
      try { py.kill('SIGKILL'); } catch {}
      resolve({ ok: false, error: 'timeout' });
    }, (Number(timeoutSec) + 2) * 1000);
    py.stdout.on('data', d => { out += d.toString('utf8'); });
    py.stderr.on('data', d => { err += d.toString('utf8'); });
    py.on('close', (code) => {
      clearTimeout(timer);
      const trimmed = out.trim();
      if (!trimmed) {
        resolve({ ok: false, error: 'empty_output', stderr: err.slice(0, 200), code });
        return;
      }
      try {
        const parsed = JSON.parse(trimmed);
        resolve({ ok: true, ...parsed });
      } catch (e) {
        resolve({ ok: false, error: 'bad_json: ' + e.message, stderr: err.slice(0, 200), code });
      }
    });
    py.on('error', (e) => {
      clearTimeout(timer);
      resolve({ ok: false, error: 'spawn_failed: ' + (e.code || e.message || '') });
    });
  });
}

// ─── toast(Windows.UI.Notifications) ───────────────────────────
function psEscape(s) {
  return String(s == null ? '' : s).replace(/'/g, "''").slice(0, 120);
}

function notify(title, message) {
  const t = psEscape(title);
  const m = psEscape(message).slice(0, 100);
  const psScript = `
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$nodes = $xml.GetElementsByTagName('text')
$nodes[0].AppendChild($xml.CreateTextNode('${t}')) | Out-Null
$nodes[1].AppendChild($xml.CreateTextNode('${m}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('PrisirAI').Show($toast)
`;
  try {
    const cp = spawn('powershell', ['-NoProfile', '-Command', psScript], {
      stdio: 'ignore', windowsHide: true,
    });
    cp.on('error', () => {}); // 静默失败,不阻塞
    cp.on('close', () => {});
  } catch (_) { /* 不要让 toast 失败毁掉整个 run */ }
}

// ─── 写通知 + 可选 toast ──────────────────────────────────────
function writeNotification({ watch_id, url, diff_kind, old_hash, new_hash, summary, notify_toast }) {
  const id = newId('n');
  const detected_at = Date.now();
  const stmt = safePrepare(
    `INSERT INTO notifications (id, watch_id, url, detected_at, diff_kind, old_hash, new_hash, summary, read)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)`
  );
  safeRun(stmt, id, String(watch_id), String(url), detected_at,
          String(diff_kind), String(old_hash || ''), String(new_hash || ''), String(summary || ''));
  if (notify_toast) notify(`Web-Watch ${diff_kind}`, `${url}\n${summary}`);
  return { id, detected_at };
}

// ─── 核心:跑一次 watch ──────────────────────────────────────────
async function runWatchOnce(watch, { force = false, toast = true } = {}) {
  const id = watch.id;
  const url = watch.url;
  const selector = watch.selector || '';
  const lastHash = watch.last_hash || '';
  const previous_check_ok = (lastHash !== '') ? true : null;

  const result = await fetchUrl(url, 10);
  const now = Date.now();

  if (!result.ok) {
    // 抓取失败 → fetch_failed 通知,但 last_hash 不变
    const summary = result.error || 'fetch failed';
    writeNotification({
      watch_id: id, url,
      diff_kind: 'fetch_failed',
      old_hash: lastHash, new_hash: '',
      summary: `抓取失败: ${summary}`,
      notify_toast: toast,
    });
    // 更新 last_checked_at,last_hash 不动
    const ust = safePrepare(`UPDATE watches SET last_checked_at=?, updated_at=? WHERE id=?`);
    safeRun(ust, now, now, id);
    return { ok: true, run_id: newId('r'), changed: false, summary: `fetch_failed: ${summary}`, fetch_ok: false };
  }

  const content = result.content || '';
  const fetcher = result.fetcher || '';
  const cached = !!result.cached;
  const normalized = normalizeContent(content, selector);
  const hash = sha256(normalized);

  if (!lastHash) {
    // 首次 → 只存 hash,不告警
    const ust = safePrepare(`UPDATE watches SET last_hash=?, last_checked_at=?, updated_at=? WHERE id=?`);
    safeRun(ust, hash, now, now, id);
    return { ok: true, run_id: newId('r'), changed: false,
             summary: 'first_check (baseline saved)', fetch_ok: true, hash };
  }

  if (hash !== lastHash || force) {
    // 内容变化
    const diffLen = normalized.length;
    writeNotification({
      watch_id: id, url,
      diff_kind: 'content_changed',
      old_hash: lastHash, new_hash: hash,
      summary: `内容变化(${diffLen} bytes, fetcher=${fetcher}, cached=${cached})`,
      notify_toast: toast,
    });
    const ust = safePrepare(`UPDATE watches SET last_hash=?, last_checked_at=?, last_changed_at=?, updated_at=? WHERE id=?`);
    safeRun(ust, hash, now, now, now, id);
    return { ok: true, run_id: newId('r'), changed: true,
             summary: 'content_changed', fetch_ok: true, hash };
  }

  // 无变化(如果之前 fetch 失败过,标记 recovered)
  if (previous_check_ok === null) {
    // 无前一状态参考,安全起见不写 recovered
  }
  const ust = safePrepare(`UPDATE watches SET last_checked_at=?, updated_at=? WHERE id=?`);
  safeRun(ust, now, now, id);
  return { ok: true, run_id: newId('r'), changed: false,
           summary: 'no_change', fetch_ok: true, hash };
}

// ─── 命令:watch.add ────────────────────────────────────────────
ext.registerCommand('watch.add', async (args) => {
  const url = String(args.url || '').trim();
  if (!url) return { ok: false, error: 'url required' };
  if (!/^https?:\/\//i.test(url) && !/^file:\/\//i.test(url)) {
    return { ok: false, error: 'url must start with http(s):// or file://' };
  }
  const selector = String(args.selector || '');
  const interval_sec = Math.max(10, Math.min(86400, Number(args.interval_sec) || 600));
  const id = newId('w');
  const now = Date.now();
  const stmt = safePrepare(
    `INSERT INTO watches (id, url, selector, interval_sec, last_hash, last_checked_at, last_changed_at, created_at, updated_at)
     VALUES (?, ?, ?, ?, '', 0, 0, ?, ?)`
  );
  safeRun(stmt, id, url, selector, interval_sec, now, now);
  return {
    ok: true, id, watch: { id, url, selector, interval_sec,
      last_hash: '', last_checked_at: 0, last_changed_at: 0, created_at: now, updated_at: now },
  };
});

// ─── 命令:watch.list ───────────────────────────────────────────
ext.registerCommand('watch.list', async (args) => {
  const limit = Math.max(1, Math.min(500, Number(args.limit) || 100));
  const stmt = safePrepare(`SELECT id, url, selector, interval_sec, last_hash,
      last_checked_at, last_changed_at, created_at, updated_at
      FROM watches ORDER BY created_at DESC LIMIT ?`);
  const rows = safeAll(stmt, limit);
  const watches = rows.map(r => ({
    id: r.id,
    url: r.url,
    selector: r.selector,
    interval_sec: r.interval_sec,
    last_hash: r.last_hash,
    last_checked_at: r.last_checked_at,
    last_changed_at: r.last_changed_at,
    created_at: r.created_at,
    updated_at: r.updated_at,
  }));
  return { ok: true, watches, total: watches.length };
});

// ─── 命令:watch.remove ─────────────────────────────────────────
ext.registerCommand('watch.remove', async (args) => {
  const id = String(args.id || '');
  if (!id) return { ok: false, error: 'id required' };
  const stmt = safePrepare(`DELETE FROM watches WHERE id=?`);
  const result = safeRun(stmt, id);
  const removed = (result && result.changes) ? result.changes : 0;
  return { ok: true, removed };
});

// ─── 命令:watch.run ────────────────────────────────────────────
ext.registerCommand('watch.run', async (args) => {
  const id = String(args.id || '');
  if (!id) return { ok: false, error: 'id required' };
  const force = !!args.force;
  const toast = args.toast !== false; // default true
  const stmt = safePrepare(`SELECT id, url, selector, interval_sec, last_hash,
      last_checked_at, last_changed_at, created_at, updated_at FROM watches WHERE id=?`);
  const rows = safeAll(stmt, id);
  if (!rows.length) return { ok: false, error: 'watch not found' };
  const watch = {
    id: rows[0].id, url: rows[0].url, selector: rows[0].selector,
    interval_sec: rows[0].interval_sec, last_hash: rows[0].last_hash,
    last_checked_at: rows[0].last_checked_at, last_changed_at: rows[0].last_changed_at,
    created_at: rows[0].created_at, updated_at: rows[0].updated_at,
  };
  return await runWatchOnce(watch, { force, toast });
});

// ─── 命令:watch.check ──────────────────────────────────────────
ext.registerCommand('watch.check', async (args) => {
  const targetId = args.id ? String(args.id) : null;
  const all = !args.id && !!args.all;
  const toast = args.toast !== false;
  let rows;
  if (targetId) {
    const s = safePrepare(`SELECT id, url, selector, interval_sec, last_hash,
      last_checked_at, last_changed_at, created_at, updated_at FROM watches WHERE id=?`);
    rows = safeAll(s, targetId);
  } else if (all) {
    const s = safePrepare(`SELECT id, url, selector, interval_sec, last_hash,
      last_checked_at, last_changed_at, created_at, updated_at FROM watches ORDER BY created_at DESC LIMIT 1000`);
    rows = safeAll(s);
  } else {
    return { ok: false, error: 'pass { all: true } or { id: "..." }' };
  }
  if (!rows || !rows.length) return { ok: true, ran: 0, changed: 0 };
  let ran = 0, changed = 0;
  for (const r of rows) {
    const watch = {
      id: r.id, url: r.url, selector: r.selector,
      interval_sec: r.interval_sec, last_hash: r.last_hash,
      last_checked_at: r.last_checked_at, last_changed_at: r.last_changed_at,
      created_at: r.created_at, updated_at: r.updated_at,
    };
    const out = await runWatchOnce(watch, { toast });
    ran++;
    if (out.changed) changed++;
  }
  return { ok: true, ran, changed };
});

// ─── 启动 ──────────────────────────────────────────────────────
if (require.main !== module && process.env.PRISIR_WEB_WATCH_TEST !== '1') {
  ext.start().catch((e) => {
    ext.log('error', `start failed: ${e.message}`);
    process.exit(1);
  });
}

module.exports = { ext };
