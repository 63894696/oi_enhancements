# Phase B-1:task-runner 扩展 — 设计文档

> **ship 时间**:2026-09-20
> **状态**:v0.1 已 ship,SDK invokeExt + Python 转发层 + task-runner DAG 引擎 + 独立 SQLite 全链路 E2E 通过。

---

## 0. 背景

Phase A ship 了 12 个原子扩展(todo / scheduled-task / app-launcher / process-scan / window-list / 等),每个只能独立调一次命令。

**痛点**:
- 「启动开发环境」= 调 watchdog + 启 vscode + 启 chrome → 现在要 4 次手动 invoke
- 「清理临时文件」= 关 chrome → 删 temp → 启回 chrome → 现在只能手动串
- 「每天 03:00 全链路巡检」→ 跟现有 scheduled-task 不重叠(scheduled-task 只能调 exe)

**task-runner 解决**:把多个扩展的 registerCommand 串成 DAG,支持手动/定时触发。

---

## 1. 架构总览

```
┌────────────────────────────────────────────────────────────────────┐
│                    PrisirAI 主进程 (Python)                          │
│   _ext_proxy_dispatch (Phase B-1 新增, ~70 行)                      │
│      ↓ 处理 ext 发来的 "extension.invoke_request" notification       │
│      ↓ 死循环防护 (_proxy_chain 跟踪 req_id → [ext_id, ...])         │
│      ↓ 调 _ext_rpc_call(target_ext_id, method, params)               │
│      ↓ 推回 "extension.invoke_response" 给源 ext                     │
└────────────────────┬───────────────────────────────────────────────┘
                     │ stdio NDJSON JSON-RPC
        ┌────────────┴────────────┐
        ▼                         ▼
  ┌─────────────┐          ┌──────────────────┐
  │ task-runner │ ────────►│ 目标扩展           │
  │  DAG 引擎   │  invokeExt()                 │
  │  SQLite     │ ◄────────  (任意 enabled ext) │
  │  调度器     │   invoke_response             │
  └─────────────┘          └──────────────────┘
```

---

## 2. SDK 新增能力 — `extensions/sdk/index.js`

### 2.1 `invokeExt(extId, method, params, timeoutMs)`

```js
const result = await ext.invokeExt('system-watchdog', 'watch.status', {}, 10000);
// result = { running: true, rules: {...} }
// 或 throw new Error('invokeExt timeout: system-watchdog.watch.status')
```

**协议**(stdio NDJSON):
```
ext → 主:  {"jsonrpc":"2.0","method":"extension.invoke_request",
            "params":{"req_id":"ext_42_x7y8","target_ext_id":"system-watchdog",
                      "method":"watch.status","params":{}}}
主 → ext:  {"jsonrpc":"2.0","method":"extension.invoke_response",
            "params":{"req_id":"ext_42_x7y8","result":{...}}}
       或: {"params":{"req_id":"ext_42_x7y8","error":"target ext not found"}}
```

`req_id` 由 SDK 生成 `ext_<num>_<rand>`,通过 SDK 内部 `_pending` 通道配对响应。

### 2.2 死循环防护 — Python 端 `_proxy_chain`

```python
# prisIragent_web.py:_ext_proxy_dispatch
chain = _proxy_chain.get(req_id, [])
if target_ext in chain:
    err_msg = f"circular invoke: {' → '.join(chain + [target_ext])}"
    # 拒
_proxy_chain[req_id] = chain + [src_ext]  # 把当前调用方记入链
```

每次转发把 `src_ext` 加入该 `req_id` 的 chain,如果目标 ext 已在 chain 里直接拒绝。

> **设计说明**:每个 `req_id` 独立 chain,默认情况 task-runner 命令都直接读 SQLite 不走 invokeExt,所以 task-runner 内部不会出现 A→B→A 循环。防护主要为「外部扩展主动 invokeExt task-runner 再触发反向调用」这类复杂场景兜底。

---

## 3. task-runner 扩展 — `extensions/task-runner/`

### 3.1 DAG schema

```json
{
  "id": "t_<ts><rand>",
  "name": "启动开发环境",
  "trigger": "manual" | "schedule",
  "schedule": "" | "every 5m" | "onstart" | "daily 03:00",
  "dag": {
    "<node_id>": {
      "ext":     "system-watchdog",     // 目标扩展 id(必须 enabled)
      "method":  "watch.start",          // 目标扩展的命令
      "params":  { "rules": {} },        // 传给命令的参数
      "needs":   ["<other_node_id>"]     // 依赖,可省
    }
  }
}
```

### 3.2 拓扑 + 并行

`executeDag()` 用 Kahn 算法分层:同一深度多个无依赖节点用 `Promise.allSettled` 并发 invokeExt,失败节点 fail-fast(整 run 标 failed)。

### 3.3 SQLite

文件:`~/.prisir/extensions/task-runner/state.db`,表:
- `tasks(id, name, dag_json, trigger, schedule, created_at, updated_at)`
- `runs(id, task_id, started_at, finished_at, status, result_json, error)`

`node:sqlite`(Node 18+ 内置),零外部依赖。

### 3.4 命令

| 命令 | 参数 | 返回 |
|------|------|------|
| `task.upsert` | `{name, dag, trigger?, schedule?}` | `{ok, id, task}` |
| `task.list` | `{limit?}` | `{tasks, total}` |
| `task.get` | `{id}` | `{task}` |
| `task.delete` | `{id}` | `{ok}` |
| `task.run` | `{id, wait?}` | wait=true 同步返结果 / false 立即返 `{queued:true}` |
| `task.runs` | `{task_id?, limit?}` | `{runs, total}` |
| `task.schedule.start` | `{interval_sec?}` | `{ok, running, interval_sec}` |
| `task.schedule.stop` | - | `{ok}` |
| `task.schedule.status` | - | `{running, interval_sec, next_tick_at, active_runs}` |

---

## 4. 真实场景示例

### 4.1 启动开发环境(顺序链)

```bash
# upsert
curl -X POST .../extensions -d '{"op":"invoke","id":"task-runner","method":"task.upsert","params":{
  "name":"启动开发环境",
  "dag":{
    "watch":{"ext":"system-watchdog","method":"watch.start","params":{"rules":{}}},
    "code":{"ext":"app-launcher","method":"app.run","params":{"exe":"code.exe"},"needs":["watch"]},
    "chrome":{"ext":"app-launcher","method":"app.run","params":{"exe":"chrome.exe"},"needs":["code"]}
  }
}}'

# run
curl -X POST .../extensions -d '{"op":"invoke","id":"task-runner","method":"task.run","params":{"id":"t_xxx","wait":true}}'
```

**E2E 实测**(2026-09-20): status=ok,3 nodes 全成功,watch+code+chrome 顺序启动。

### 4.2 并行查询(同时扫 3 个进程)

```json
{
  "A": {"ext":"process-scan","method":"ps.list","params":{"filter":"code"}},
  "B": {"ext":"process-scan","method":"ps.list","params":{"filter":"chrome"}},
  "C": {"ext":"process-scan","method":"ps.list","params":{"filter":"explorer"},"needs":["A","B"]}
}
```

**E2E 实测**:A 和 B 并发(各 ~2800ms),C 等待两者完成后执行。

### 4.3 每日巡检(定时)

```bash
curl -X POST .../extensions -d '{"op":"invoke","id":"task-runner","method":"task.upsert","params":{
  "name":"每日清理",
  "trigger":"schedule","schedule":"daily 03:00",
  "dag":{"scan":{"ext":"process-scan","method":"ps.list","params":{"limit":10}}}
}}'
curl -X POST .../extensions -d '{"op":"invoke","id":"task-runner","method":"task.schedule.start","params":{"interval_sec":30}}'
```

`interval_sec` 默认 30,定时巡检到点就 fire-and-forget run。

---

## 5. 错误处理(实测覆盖)

| 场景 | 表现 |
|------|------|
| 目标 ext 未安装 | `target ext not found: <id>` |
| 目标 ext 未启用 | `target ext not enabled: <id>` |
| 目标方法不存在 | `rpc timeout or failed: <ext>.<method>`(子进程报 unknown method) |
| 死循环(A→B→A 同 req_id) | `circular invoke: A → B → A` |
| invokeExt 超时(默认 30s) | `invokeExt timeout: <ext>.<method>` |

---

## 6. 关键复用 + 不重复造轮子

| 复用 | 位置 |
|------|------|
| Python 同步 RPC `_ext_rpc_call(ext_id, method, params, timeout)` | `prisIragent_web.py:1618` |
| Python 单向通知 `_ext_rpc_notify(ext_id, method, params)` | `prisIragent_web.py:1653` |
| SDK `_notify()` ext → 主通知 | `extensions/sdk/index.js:109` |
| SDK `_pending` 请求/响应通道 | `extensions/sdk/index.js:51` 复用 |
| todo/scheduled-task/app-launcher 命令 | DAG 节点直接调用,不动 |
| Node 18+ 内置 `node:sqlite` | 零外部依赖 |

---

## 7. v0.1 明确不做

- ❌ LLM 生成 DAG(Phase B-4,需接 LLM 路由)
- ❌ 事件触发(session.message / file.change)— v0.2
- ❌ 任务失败告警弹卡 — v0.2
- ❌ 任务链可视化 UI — 留 Phase B-2 workflow
- ❌ on_error 节点(节点失败走另一条补偿分支) — v0.2

---

## 8. 文件清单

**新增**:
- `extensions/task-runner/package.json`
- `extensions/task-runner/index.js` (~290 行)
- `extensions/_store/ext-task-runner-0.1.0.tgz` (5.3KB)
- `extensions/_store/index.json` (新增 1 条,total=22)
- `~/.prisir/store/ext-task-runner-0.1.0.tgz` (装包用)
- `docs/phase-B1-task-runner.md` (本文)

**修改**:
- `extensions/sdk/index.js` — `invokeExt()` + `invoke_response` 处理 (~40 行)
- `prisIragent_web.py` — `_ext_proxy_dispatch()` + `_proxy_chain` 死循环防护 + notification 分发 (~90 行)

**不动**:其他 12 个原子扩展 / 主进程其他逻辑 / oiagent-shell / Tauri

---

## 9. E2E 实测记录(2026-09-20)

| 场景 | 实测结果 |
|------|----------|
| `task.upsert` 创建任务 | ok,id 返回 |
| `task.run` wait=true 单节点调 system-watchdog | status=ok,system-watchdog.watch.status result 真实返回 |
| DAG 拓扑 + 并行(3 节点,C 依赖 A+B) | A=2813ms, B=2813ms(并发), C 在两者完成后执行,全部 ok |
| 错误处理:未知 ext | `target ext not found: nonexistent` |
| 错误处理:未知 method | `rpc timeout or failed: system-watchdog.watch.fakemethod` |
| `task.runs` 查历史 | 返回 runs 列表含 nodes 结果和 ms |
| `task.delete` 清理 | ok |
| `task.schedule.start` + `task.schedule.status` | 后台 setInterval 启动,30s 默认 |
| node:sqlite 可用性 | 主进程日志: `task-runner starting (node:sqlite = ok)` |

---

## 10. 后续

- Task #33 [pending] **Phase B-2:workflow 扩展** — 拖拽 UI + 自然语言 → DAG,可读可视化
- Task #34 [pending] **Phase B-3:Python 后端任务队列升级** — task-runner 任务队列化(支持并发 run + 优先级)
- Task #35 [pending] **Phase B-4:AI agent 任务执行派单** — LLM 生成 DAG + 自动派单
