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
    -- P2.5+B-3(2026-09-21)节点级结果:中间状态可查、可回放、cancel 标 canceled
    CREATE TABLE IF NOT EXISTS node_runs (
      run_id      TEXT NOT NULL,
      node_id     TEXT NOT NULL,
      status      TEXT NOT NULL,                     -- running|ok|failed|canceled
      attempts    INTEGER NOT NULL DEFAULT 1,
      ms          INTEGER NOT NULL DEFAULT 0,
      error       TEXT NOT NULL DEFAULT '',
      started_at  INTEGER NOT NULL DEFAULT 0,
      finished_at INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (run_id, node_id)
    );
    CREATE INDEX IF NOT EXISTS node_runs_run_idx ON node_runs(run_id);
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
 * P2.5+B-2(2026-09-21)重试/超时/backoff:
 *   每个 node 可带 retry = { max_retries, backoff, timeout_sec }
 *   缺省 = { max_retries: 0, backoff: 'exponential', timeout_sec: 30 }
 *   backoff: constant 1s / linear attempt×1s / exponential 2^(attempt-1)×1s
 *   timeout 用 Promise.race(handler, timeoutPromise);失败 → catch 重试(非 timeout 也重试)
 * 返回 { status: 'ok'|'failed', nodes: {id: {status, result|error, attempts}}, error? }
 */
function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function _backoffDelay(backoff, attempt) {
  const b = String(backoff || 'exponential').toLowerCase();
  if (b === 'constant') return 1000;
  if (b === 'linear') return attempt * 1000;
  // exponential: attempt = 1..5 → 1s, 2s, 4s, 8s, 16s
  return Math.pow(2, attempt - 1) * 1000;
}

// P2.5+B-3(2026-09-21)进度通知:每 node 开始 + 完成各推 1 条;cancel/abort 也推
//   payload = { run_id, node_id, status, attempts, ms, active, total, event, error? }
//   event: 'start' | 'end' | 'abort'
//   active/total 给前端 progress bar 用
function _notifyProgress(runId, payload) {
  try { ext.notify('task.run.progress', Object.assign({run_id: runId}, payload)); }
  catch (e) { /* notifier 不可用也别让 run 挂 */ }
}

// P2.5+B-3(2026-09-21)优雅取消:executeNodeWithRetry 注入 AbortController,
//   cancel → ac.abort() → 当前 invokeExt 立刻 reject('aborted') → 标 canceled 不重试
async function executeNodeWithRetry(nid, n, log, runId, totalNodes) {
  const retry = n.retry || {};
  const maxRetries = Math.max(0, Math.min(5, Number(retry.max_retries) || 0));
  const backoff = retry.backoff || 'exponential';
  const timeoutSec = Math.max(1, Math.min(600, Number(retry.timeout_sec) || 30));
  const ac = _activeRuns.get(runId)?.abort;
  let attempts = 0;
  let lastErr = null;
  const nodeStart = Date.now();
  let attemptStart = nodeStart;
  _notifyProgress(runId, {node_id: nid, status: 'running', attempts: 0,
                          active: 1, total: totalNodes, event: 'start'});
  while (attempts <= maxRetries) {
    attempts++;
    if (ac?.signal.aborted) {
      lastErr = 'aborted';
      _notifyProgress(runId, {node_id: nid, status: 'canceled', attempts,
                              active: 0, total: totalNodes, event: 'abort', error: lastErr});
      break;
    }
    attemptStart = Date.now();
    log('info', `node ${nid} → ${n.ext}.${n.method} (attempt ${attempts}/${maxRetries + 1})`);
    try {
      const result = await Promise.race([
        ext.invokeExt(n.ext, n.method, n.params || {}, timeoutSec * 1000),
        new Promise((_, rej) => {
          const t = setTimeout(
            () => rej(new Error(`timeout: ${n.ext}.${n.method} after ${timeoutSec}s`)),
            timeoutSec * 1000);
          ac?.signal.addEventListener('abort', () => {
            clearTimeout(t);
            rej(new Error('aborted'));
          }, {once: true});
        }),
      ]);
      const ms = Date.now() - attemptStart;
      db().prepare(`INSERT OR REPLACE INTO node_runs
                    (run_id,node_id,status,attempts,ms,started_at,finished_at)
                    VALUES (?,?,?,?,?,?,?)`)
        .run(runId, nid, 'ok', attempts, ms, attemptStart, Date.now());
      _notifyProgress(runId, {node_id: nid, status: 'ok', attempts, ms,
                              active: 0, total: totalNodes, event: 'end'});
      return { nid, status: 'ok', result, ms, attempts };
    } catch (e) {
      lastErr = e.message || String(e);
      log('warn', `node ${nid} attempt ${attempts} failed: ${lastErr}`);
      if (lastErr === 'aborted' || attempts > maxRetries) break;
      await _sleep(_backoffDelay(backoff, attempts));
    }
  }
  const ms = Date.now() - nodeStart;
  const finalStatus = (lastErr === 'aborted') ? 'canceled' : 'failed';
  db().prepare(`INSERT OR REPLACE INTO node_runs
                (run_id,node_id,status,attempts,ms,error,started_at,finished_at)
                VALUES (?,?,?,?,?,?,?,?)`)
    .run(runId, nid, finalStatus, attempts, ms, lastErr, nodeStart, Date.now());
  _notifyProgress(runId, {node_id: nid, status: finalStatus, attempts, ms, error: lastErr,
                          active: 0, total: totalNodes, event: finalStatus === 'canceled' ? 'abort' : 'end'});
  return { nid, status: finalStatus, error: lastErr, ms, attempts };
}

async function executeDag(dag, log, runId, totalNodes) {
  const ids = Object.keys(dag);
  const indeg = new Map(ids.map(i => [i, 0]));
  for (const id of ids) for (const dep of (dag[id].needs || [])) indeg.set(id, indeg.get(id) + 1);

  const nodeResults = {};
  let firstError = null;
  let runStatus = 'ok';
  const ac = _activeRuns.get(runId)?.abort;

  let wave = ids.filter(i => indeg.get(i) === 0);
  while (wave.length) {
    if (ac?.signal.aborted) {
      runStatus = 'canceled';
      break;
    }
    const results = await Promise.allSettled(
      wave.map(nid => executeNodeWithRetry(nid, dag[nid], log, runId, totalNodes))
    );
    for (const r of results) {
      const v = r.value;
      nodeResults[v.nid] = { status: v.status, result: v.result, error: v.error,
                             ms: v.ms, attempts: v.attempts };
      if (v.status === 'failed' && runStatus === 'ok') {
        firstError = { node: v.nid, error: v.error };
        runStatus = 'failed';
      } else if (v.status === 'canceled' && runStatus === 'ok') {
        firstError = { node: v.nid, error: v.error || 'aborted' };
        runStatus = 'canceled';
      }
    }
    if (runStatus !== 'ok') break;   // fail-fast / canceled-fast
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
    status: runStatus,
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
// P2.5+B-3(2026-09-21)activeRuns Map:run_id → {abort: AbortController, task_id, total}。
//   取代旧的 _activeRuns 计数器(只数并发数不记是谁);现在每个 run 独立 Ac,
//   cancel 真能 abort 当前 invokeExt 不影响其他 run。
const _activeRuns = new Map();

async function runTaskOnce(taskId) {
  const task = rowToTask(db().prepare('SELECT * FROM tasks WHERE id = ?').get(taskId));
  if (!task) return { ok: false, error: 'task not found', task_id: taskId };
  const runId = 'r_' + Date.now() + Math.random().toString(36).slice(2, 5);
  const ac = new AbortController();
  const total = Object.keys(task.dag || {}).length;
  _activeRuns.set(runId, {abort: ac, task_id: taskId, total});
  const startedAt = Date.now();
  db().prepare(`INSERT INTO runs (id,task_id,started_at,status) VALUES (?,?,?,?)`)
    .run(runId, taskId, startedAt, 'running');
  ext.log('info', `run ${runId} start (task=${taskId} total=${total})`);
  let exec;
  try {
    exec = await executeDag(task.dag, ext.log.bind(ext), runId, total);
  } catch (e) {
    exec = { status: 'failed', nodes: {}, error: e.message || String(e) };
  }
  const finishedAt = Date.now();
  const resultStr = JSON.stringify(exec);
  db().prepare(`UPDATE runs SET finished_at=?, status=?, result_json=?, error=? WHERE id=?`)
    .run(finishedAt, exec.status, resultStr, exec.error || '', runId);
  _activeRuns.delete(runId);
  ext.log('info', `run ${runId} ${exec.status} (${finishedAt - startedAt}ms, ${Object.keys(exec.nodes).length} nodes)`);
  return { ok: exec.status === 'ok', run_id: runId, status: exec.status, nodes: exec.nodes, error: exec.error };
}

ext.registerCommand('task.run', async (args) => {
  if (!args.id) return { ok: false, error: 'id required' };
  if (args.wait === true) {
    return await runTaskOnce(args.id);
  }
  // fire-and-forget 但要返 run_id,这样前端能立刻拿 run_id 起 progress polling。
  // 做法:同步从 tasks 表拿 runId 字段(若有)/新建占位 run 拿到 id 后立刻返,
  // 真正的 executeDag 异步跑。run_id 现在等于入参 'id' 不行,因为同一 task 可能重跑。
  // 简化:让 runTaskOnce 在 _activeRuns.set 之后、executeDag 之前通过 Promise.resolve
  // 暴露 run_id —— 直接重写:fire 模式单独一条路径。
  const taskRow = db().prepare('SELECT * FROM tasks WHERE id = ?').get(args.id);
  if (!taskRow) return { ok: false, error: 'task not found', task_id: args.id };
  const task = rowToTask(taskRow);
  const runId = 'r_' + Date.now() + Math.random().toString(36).slice(2, 5);
  const ac = new AbortController();
  const total = Object.keys(task.dag || {}).length;
  _activeRuns.set(runId, {abort: ac, task_id: args.id, total});
  const startedAt = Date.now();
  db().prepare(`INSERT INTO runs (id,task_id,started_at,status) VALUES (?,?,?,?)`)
    .run(runId, args.id, startedAt, 'running');
  ext.log('info', `run ${runId} start fire (task=${args.id} total=${total})`);
  // 异步跑,出错了不抛(避免污染 RPC 返参)
  (async () => {
    let exec;
    try {
      exec = await executeDag(task.dag, ext.log.bind(ext), runId, total);
    } catch (e) {
      exec = { status: 'failed', nodes: {}, error: e.message || String(e) };
    }
    const finishedAt = Date.now();
    db().prepare(`UPDATE runs SET finished_at=?, status=?, result_json=?, error=? WHERE id=?`)
      .run(finishedAt, exec.status, JSON.stringify(exec), exec.error || '', runId);
    _activeRuns.delete(runId);
    ext.log('info', `run ${runId} ${exec.status} (${finishedAt - startedAt}ms, ${Object.keys(exec.nodes).length} nodes)`);
  })();
  return { ok: true, run_id: runId, status: 'running', task_id: args.id, queued: true };
});

// P2.5+B-3(2026-09-21)真取消:走 AbortController,优雅不杀子进程。
//   行为:activeRuns.get(run_id).abort() → executeNodeWithRetry 里 Promise.race 立刻 reject('aborted')
//   → 落 node_runs status=canceled + 推 progress canceled → run 主表 status=canceled
ext.registerCommand('task.run.cancel', async (args) => {
  if (!args.run_id) return { ok: false, error: 'run_id required' };
  const r = _activeRuns.get(args.run_id);
  if (!r) {
    // 不在 active 集合 → 已结束(查 runs 表兜底)
    const row = db().prepare('SELECT status FROM runs WHERE id = ?').get(args.run_id);
    if (row) return { ok: false, error: 'run_not_active', current_status: row.status };
    return { ok: false, error: 'run_not_found' };
  }
  r.abort.abort();
  return { ok: true, canceled: args.run_id, task_id: r.task_id, at: Date.now() };
});

// P2.5+B-3(2026-09-21)活跃 run 列表(task-runner 进程内)。供 wfmodal 显示"正在跑"。
ext.registerCommand('task.run.active', async () => {
  const list = [];
  for (const [rid, v] of _activeRuns.entries()) {
    list.push({run_id: rid, task_id: v.task_id, total_nodes: v.total, started_at: 0});
  }
  return {active: list, count: list.length};
});

// P2.5+B-2(2026-09-21)内置模板列表(避免前端散落写模板 JSON)。
// 真套用在前端做:wfTemplates → wfApplyTpl → 直接拿来当 _wfCurrentTask.dag。
ext.registerCommand('task.template.list', async () => {
  return {
    templates: [
      { id: 'simple_echo', name: '简单回声(单节点)',
        description: '调 todo.add 加一条带 demo tag 的任务,演示单节点工作流',
        dag: { n1: { ext: 'todo', method: 'todo.add',
                     params: { title: 'echo: {{input}}', tags: ['demo'] } } } },
      { id: 'daily_summary', name: '每日摘要(每天 09:00)',
        description: '调度器模板,每天 09:00 拉取 tag=summary 的 todo 列表',
        dag: { n1: { ext: 'todo', method: 'todo.list', params: { limit: 20, tag: 'summary' } } },
        trigger: 'schedule', schedule: 'daily 09:00' },
      { id: 'three_step_demo', name: '三节点串行(演示依赖)',
        description: 'n1 → n2 → n3,n2 依赖 n1,n3 依赖 n2。展示 needs 跨节点连线',
        dag: {
          n1: { ext: 'todo', method: 'todo.add', params: { title: 'step 1' } },
          n2: { ext: 'todo', method: 'todo.add', params: { title: 'step 2' }, needs: ['n1'] },
          n3: { ext: 'todo', method: 'todo.add', params: { title: 'step 3' }, needs: ['n2'] },
        } },
    ],
  };
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
  active_runs: _activeRuns.size,
}));

// ─── 启动 ──────────────────────────────────────────────────────────
ext.log('info', 'task-runner starting (node:sqlite = ' + (() => {
  try { require('node:sqlite'); return 'ok'; } catch (e) { return 'missing'; }
})() + ')');

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
