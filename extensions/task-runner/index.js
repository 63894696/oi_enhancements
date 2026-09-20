'use strict';

/**
 * 任务调度 v0.1.0 (Phase B-1, 2026-09-20)
 *
 * 把多个扩展的 registerCommand 调用串成 DAG,支持手动触发 + 定时触发。
 * 独立 SQLite 存任务定义和执行历史(Node 18+ 内置 node:sqlite,零外部依赖)。
 *
 * ── DAG schema ─────────────────────────────────────────────────────
 *   task = {
 *     id: "t_<ts>",
 *     name: "启动开发环境",
 *     trigger: "manual" | "schedule",
 *     schedule: "" | "every 5m" | "onstart" | "daily 03:00",
 *     dag: {
 *       "<node_id>": {
 *         ext:     "system-watchdog",     // 目标扩展 id(必须已 enabled)
 *         method:  "watch.start",          // 目标扩展的命令
 *         params:  { ... },                // 传给命令的参数
 *         needs:   [ "<other_node_id>", ... ]   // 依赖,无依赖可省
 *       }
 *     }
 *   }
 *
 * ── 命令 ──────────────────────────────────────────────────────────
 *   task.upsert       { name, dag, trigger?, schedule? }   → { ok, id, task }
 *   task.list         { limit? }                          → { tasks, total }
 *   task.get          { id }                              → { task | null }
 *   task.delete       { id }                              → { ok }
 *   task.run          { id, wait? }                       → { ok, run_id } 或 wait=true 同步等结果
 *   task.runs         { task_id?, limit? }                → { runs, total }
 *   task.schedule.start { interval_sec? }                 → { ok, running, interval_sec }
 *   task.schedule.stop                                   → { ok }
 *   task.schedule.status                                 → { running, interval_sec, next_tick_at }
 *
 * ── 跨扩展调用 ────────────────────────────────────────────────────
 *   用 SDK ext.invokeExt(target_ext_id, method, params, timeoutMs)
 *   Phase B-1 新增,主进程转发层 (_ext_proxy_dispatch) 负责实际 RPC,
 *   SDK 自带死循环防护(同 req_id 转发链出现重复 ext 直接 reject)。
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const path = require('path');
const fs = require('fs');

const ext = new PrisIrExt({ id: 'task-runner', name: '任务调度', version: '0.1.0' });

const HOME = () => process.env.PRISIR_EXT_HOME || '.';
const DB_PATH = () => path.join(HOME(), 'state.db');

// ─── SQLite (Node 18+ 内置 node:sqlite) ─────────────────────────────
let _db = null;
function db() {
  if (_db) return _db;
  const { DatabaseSync } = require('node:sqlite');
  fs.mkdirSync(HOME(), { recursive: true });
  _db = new DatabaseSync(DB_PATH());
  _db.exec(`
    CREATE TABLE IF NOT EXISTS tasks (
      id          TEXT PRIMARY KEY,
      name        TEXT NOT NULL,
      dag_json    TEXT NOT NULL,
      trigger     TEXT NOT NULL DEFAULT 'manual',
      schedule    TEXT NOT NULL DEFAULT '',
      created_at  INTEGER NOT NULL,
      updated_at  INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS runs (
      id          TEXT PRIMARY KEY,
      task_id     TEXT NOT NULL,
      started_at  INTEGER NOT NULL,
      finished_at INTEGER NOT NULL DEFAULT 0,
      status      TEXT NOT NULL DEFAULT 'pending',   -- pending|running|ok|failed|canceled
      result_json TEXT NOT NULL DEFAULT '{}',
      error       TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS runs_task_idx ON runs(task_id, started_at DESC);
  `);
  return _db;
}

// ─── DAG 校验 ───────────────────────────────────────────────────────
function validateDag(dag) {
  if (!dag || typeof dag !== 'object' || Array.isArray(dag)) {
    return 'dag must be object {node_id: {ext, method, params?, needs?}}';
  }
  const ids = Object.keys(dag);
  if (!ids.length) return 'dag empty';
  const idset = new Set(ids);
  for (const id of ids) {
    const n = dag[id];
    if (!n || typeof n !== 'object') return `node ${id} not object`;
    if (!n.ext || typeof n.ext !== 'string') return `node ${id}.ext missing`;
    if (!n.method || typeof n.method !== 'string') return `node ${id}.method missing`;
    const needs = Array.isArray(n.needs) ? n.needs : [];
    for (const dep of needs) {
      if (!idset.has(dep)) return `node ${id}.needs references missing node: ${dep}`;
    }
  }
  // 拓扑排序检环(Kahn)
  const indeg = new Map(ids.map(i => [i, 0]));
  for (const id of ids) {
    for (const dep of (dag[id].needs || [])) indeg.set(id, indeg.get(id) + 1);
  }
  const q = ids.filter(i => indeg.get(i) === 0);
  const order = [];
  while (q.length) {
    const n = q.shift();
    order.push(n);
    for (const id of ids) {
      if ((dag[id].needs || []).includes(n)) {
        indeg.set(id, indeg.get(id) - 1);
        if (indeg.get(id) === 0) q.push(id);
      }
    }
  }
  if (order.length !== ids.length) return 'dag has cycle';
  return null;
}

// ─── DAG 执行引擎 ───────────────────────────────────────────────────
/**
 * 拓扑分层并行执行。返回 { status: 'ok'|'failed', nodes: {id: {status, result|error}}, error? }
 */
async function executeDag(dag, log) {
  const ids = Object.keys(dag);
  const indeg = new Map(ids.map(i => [i, 0]));
  for (const id of ids) for (const dep of (dag[id].needs || [])) indeg.set(id, indeg.get(id) + 1);

  const nodeResults = {};
  let firstError = null;
  let runFailed = false;

  // 分层:indeg==0 入 first wave,跑完一层把下游 indeg -1
  let wave = ids.filter(i => indeg.get(i) === 0);
  while (wave.length) {
    const results = await Promise.allSettled(wave.map(async (nid) => {
      const n = dag[nid];
      log('info', `node ${nid} → ${n.ext}.${n.method}`);
      const t0 = Date.now();
      try {
        const result = await ext.invokeExt(n.ext, n.method, n.params || {}, 30000);
        return { nid, status: 'ok', result, ms: Date.now() - t0 };
      } catch (e) {
        return { nid, status: 'failed', error: e.message || String(e), ms: Date.now() - t0 };
      }
    }));
    for (const r of results) {
      const v = r.value;
      nodeResults[v.nid] = { status: v.status, result: v.result, error: v.error, ms: v.ms };
      if (v.status === 'failed' && !runFailed) {
        firstError = { node: v.nid, error: v.error };
        runFailed = true;
      }
    }
    if (runFailed) break;   // fail-fast
    // 下一层
    const next = [];
    for (const id of ids) {
      if ((dag[id].needs || []).some(d => wave.includes(d))) {
        indeg.set(id, indeg.get(id) - 1);
        if (indeg.get(id) === 0) next.push(id);
      }
    }
    wave = next;
  }

  return {
    status: runFailed ? 'failed' : 'ok',
    nodes: nodeResults,
    error: firstError ? `${firstError.node}: ${firstError.error}` : null,
  };
}

// ─── task CRUD ──────────────────────────────────────────────────────
function rowToTask(row) {
  if (!row) return null;
  let dag = {};
  try { dag = JSON.parse(row.dag_json || '{}'); } catch {}
  return {
    id: row.id,
    name: row.name,
    dag,
    trigger: row.trigger,
    schedule: row.schedule,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

ext.registerCommand('task.upsert', async (args) => {
  const name = String(args.name || '').trim();
  if (!name) return { ok: false, error: 'name required' };
  const err = validateDag(args.dag);
  if (err) return { ok: false, error: 'invalid dag: ' + err };
  const id = String(args.id || ('t_' + Date.now() + Math.random().toString(36).slice(2, 5)));
  const now = Date.now();
  const d = db();
  // 已存在则 update
  const exist = d.prepare('SELECT id FROM tasks WHERE id = ?').get(id);
  if (exist) {
    d.prepare(`UPDATE tasks SET name=?, dag_json=?, trigger=?, schedule=?, updated_at=? WHERE id=?`)
      .run(name, JSON.stringify(args.dag), String(args.trigger || 'manual'),
           String(args.schedule || ''), now, id);
  } else {
    d.prepare(`INSERT INTO tasks (id,name,dag_json,trigger,schedule,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)`)
      .run(id, name, JSON.stringify(args.dag), String(args.trigger || 'manual'),
           String(args.schedule || ''), now, now);
  }
  return { ok: true, id, task: rowToTask(d.prepare('SELECT * FROM tasks WHERE id = ?').get(id)) };
});

ext.registerCommand('task.list', async (args) => {
  const lim = Math.max(1, Math.min(500, Number(args.limit) || 100));
  const rows = db().prepare('SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?').all(lim);
  return { tasks: rows.map(rowToTask), total: rows.length };
});

ext.registerCommand('task.get', async (args) => {
  if (!args.id) return { ok: false, error: 'id required' };
  return { task: rowToTask(db().prepare('SELECT * FROM tasks WHERE id = ?').get(args.id)) };
});

ext.registerCommand('task.delete', async (args) => {
  if (!args.id) return { ok: false, error: 'id required' };
  const r = db().prepare('DELETE FROM tasks WHERE id = ?').run(args.id);
  return { ok: r.changes > 0 };
});

// ─── task.run ───────────────────────────────────────────────────────
let _activeRuns = 0;

async function runTaskOnce(taskId) {
  const task = rowToTask(db().prepare('SELECT * FROM tasks WHERE id = ?').get(taskId));
  if (!task) return { ok: false, error: 'task not found', task_id: taskId };
  const runId = 'r_' + Date.now() + Math.random().toString(36).slice(2, 5);
  const startedAt = Date.now();
  db().prepare(`INSERT INTO runs (id,task_id,started_at,status) VALUES (?,?,?,?)`)
    .run(runId, taskId, startedAt, 'running');
  ext.log('info', `run ${runId} start (task=${taskId} name="${task.name}")`);
  _activeRuns++;
  let exec;
  try {
    exec = await executeDag(task.dag, ext.log.bind(ext));
  } catch (e) {
    exec = { status: 'failed', nodes: {}, error: e.message || String(e) };
  }
  const finishedAt = Date.now();
  const resultStr = JSON.stringify(exec);
  db().prepare(`UPDATE runs SET finished_at=?, status=?, result_json=?, error=? WHERE id=?`)
    .run(finishedAt, exec.status, resultStr, exec.error || '', runId);
  _activeRuns--;
  ext.log('info', `run ${runId} ${exec.status} (${finishedAt - startedAt}ms, ${Object.keys(exec.nodes).length} nodes)`);
  return { ok: exec.status === 'ok', run_id: runId, status: exec.status, nodes: exec.nodes, error: exec.error };
}

ext.registerCommand('task.run', async (args) => {
  if (!args.id) return { ok: false, error: 'id required' };
  if (args.wait === true) {
    return await runTaskOnce(args.id);
  }
  // fire-and-forget
  runTaskOnce(args.id).catch((e) => ext.log('error', `run ${args.id} crashed: ${e.message}`));
  return { ok: true, queued: true, task_id: args.id };
});

ext.registerCommand('task.runs', async (args) => {
  const lim = Math.max(1, Math.min(200, Number(args.limit) || 30));
  const sql = args.task_id
    ? 'SELECT * FROM runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?'
    : 'SELECT * FROM runs ORDER BY started_at DESC LIMIT ?';
  const rows = args.task_id
    ? db().prepare(sql).all(args.task_id, lim)
    : db().prepare(sql).all(lim);
  const runs = rows.map(r => {
    let result = {};
    try { result = JSON.parse(r.result_json || '{}'); } catch {}
    return {
      id: r.id, task_id: r.task_id, started_at: r.started_at,
      finished_at: r.finished_at, status: r.status,
      error: r.error, result,
      ms: r.finished_at ? r.finished_at - r.started_at : (Date.now() - r.started_at),
    };
  });
  return { runs, total: runs.length };
});

// ─── 调度器(cron-like 简化版) ────────────────────────────────────────
let _schedTimer = null;
let _schedInterval = 30;     // 秒
let _schedNextTick = 0;

function shouldRunNow(schedule, lastRunAt) {
  const s = String(schedule || '').trim().toLowerCase();
  if (!s || s === 'never') return false;
  if (s === 'onstart') return lastRunAt === 0;
  const m = s.match(/^every\s+(\d+)\s*m$/);
  if (m) {
    const minutes = Number(m[1]);
    return Date.now() - lastRunAt >= minutes * 60 * 1000;
  }
  const dm = s.match(/^daily\s+(\d{1,2}):(\d{2})$/);
  if (dm) {
    const want = Number(dm[1]) * 60 + Number(dm[2]);
    const now = new Date();
    const cur = now.getHours() * 60 + now.getMinutes();
    const lastDate = lastRunAt ? new Date(lastRunAt) : null;
    const lastMin = lastDate ? lastDate.getHours() * 60 + lastDate.getMinutes() : -1;
    const lastDay = lastDate ? lastDate.toDateString() : '';
    return cur >= want && !(lastDay === now.toDateString() && lastMin >= want);
  }
  return false;
}

function schedTick() {
  _schedNextTick = Date.now() + _schedInterval * 1000;
  const tasks = db().prepare(`SELECT * FROM tasks WHERE trigger='schedule' AND schedule != ''`).all();
  for (const row of tasks) {
    const lastRun = db().prepare(`SELECT MAX(started_at) AS last FROM runs WHERE task_id = ? AND status != 'pending'`)
      .get(row.id);
    const last = lastRun && lastRun.last ? Number(lastRun.last) : 0;
    if (shouldRunNow(row.schedule, last)) {
      ext.log('info', `scheduler firing: ${row.name} (${row.schedule})`);
      runTaskOnce(row.id).catch((e) => ext.log('error', `sched run ${row.id} crashed: ${e.message}`));
    }
  }
}

ext.registerCommand('task.schedule.start', async (args) => {
  if (_schedTimer) clearInterval(_schedTimer);
  _schedInterval = Math.max(5, Number(args.interval_sec) || 30);
  _schedTimer = setInterval(schedTick, _schedInterval * 1000);
  if (_schedTimer.unref) _schedTimer.unref();
  _schedNextTick = Date.now() + _schedInterval * 1000;
  return { ok: true, running: true, interval_sec: _schedInterval };
});

ext.registerCommand('task.schedule.stop', async () => {
  if (_schedTimer) { clearInterval(_schedTimer); _schedTimer = null; }
  _schedNextTick = 0;
  return { ok: true, running: false };
});

ext.registerCommand('task.schedule.status', async () => ({
  running: !!_schedTimer,
  interval_sec: _schedInterval,
  next_tick_at: _schedNextTick,
  active_runs: _activeRuns,
}));

// ─── 启动 ──────────────────────────────────────────────────────────
ext.log('info', 'task-runner starting (node:sqlite = ' + (() => {
  try { require('node:sqlite'); return 'ok'; } catch (e) { return 'missing'; }
})() + ')');

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
