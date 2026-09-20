# ext RPC bridge v0 (P2.5+B-0, 2026-09-21)

Python 主进程 ↔ Node ext 子进程的桥。Phase B-1 commit msg 写的「主进程转发层 ship」实际是空头支票——SDK + ext 注册表 ship 了,Python ↔ Node 桥根本没写。本次落真桥,解锁 Phase B-2/4。

## 进程模型

- **一进程一 ext**:`extensions/<id>/index.js` 用 `subprocess.Popen([node, entry], stdin=PIPE, stdout=PIPE, stderr=PIPE, env={...os.environ, "PRISIR_EXT_HOME": home})` 启
- **隔离 HOME**:`~/.prisir/ext/<ext_id>/`,Node 端 `process.env.PRISIR_EXT_HOME` 读到(`extensions/task-runner/index.js:48` 已用)
- **生命周期**:
  - `_ext_spawn(ext_id)` 主动启用
  - `_ext_kill(ext_id)` 禁用,SIGTERM → 5s grace → SIGKILL
  - reader 线程 EOF 触发 `_threading.Timer(delay, _ext_spawn)` 自动 respawn,delay = `min(2^n, 30)`,crash_count ≤ 3 时重试
- **node 解析**:`PRISIR_NODE_EXEC` env → sys.executable sibling → `shutil.which("node")` → `"node"` 兜底

## 协议(JSON-RPC 2.0 over stdin/stdout NDJSON,双向)

**Node ext → Python**(`stdout` 一行一个 JSON):
```json
{"jsonrpc":"2.0","id":"<req_id>","result":{...}}
{"jsonrpc":"2.0","id":"<req_id>","error":{"code":-1,"message":"..."}}
{"jsonrpc":"2.0","method":"log","params":{"level":"info","msg":"..."}}
{"jsonrpc":"2.0","method":"ui.inject","params":{...}}
{"jsonrpc":"2.0","method":"extension.invoke_request","params":{"req_id":"ext_3_abc","target_ext_id":"todo","method":"todo.add","params":{...}}}
{"jsonrpc":"2.0","method":"extension.invoke_response","params":{"req_id":"ext_3_abc","result":{...}}}
```

**Python → Node ext**(`stdin` 一行一个 JSON):
```json
{"jsonrpc":"2.0","id":"<py_req_id>","method":"command.<registered_method>","params":{...}}
{"jsonrpc":"2.0","method":"session.message","params":{...}}
```

Node 端 SDK 在 `extensions/sdk/index.js` 已实现同协议,主进程这次补齐。

## Python API

```python
from prisIragent_web import _ext_rpc_call, _ext_spawn, _ext_kill

# 同步调用
r = _ext_rpc_call("todo", "todo.add", {"title": "买牛奶"}, timeout=5)
if r.get("error"):
    print("RPC failed:", r["error"])
else:
    print("ok:", r["result"])

# 启用/禁用钩子(供 UI 接入)
_ext_on_enabled("task-runner")   # → spawn
_ext_on_disabled("task-runner")  # → kill
```

`_ext_rpc_call` 返回结构:
- `{"result": <dict>}` 成功(Node 子进程 handler 返的 result)
- `{"error": "ext_not_running: <ext_id>"}` 没启动
- `{"error": "ext_stdin_broken: ..."}` stdin 写失败
- `{"error": "timeout: <ext_id>.<method> after <N>s"}` 超时
- `{"error": "<node handler 抛的 message>"}` Node 端 handler 抛异常

## 跨扩展调用

Node ext A 想调 Node ext B 的命令:
1. A 调 SDK `invokeExt(target_ext_id="B", method="...", params={...})`
2. SDK 写 `extension.invoke_request` notification 到 stdout
3. Python reader_loop 收到 → 调 `_ext_proxy_dispatch(A, params)`
4. `_ext_proxy_dispatch` 用 `_ext_rpc_call(B, method, params, timeout=4.0)` 同步等结果
5. Python 把结果写 `extension.invoke_response` notification 到 A 的 stdin
6. A 的 SDK reader 收到,按 `req_id` 配对,resolve Promise

防死循环: SDK 自带同 `req_id` 转发链检测,出现重复 ext 直接 reject。

## 调试端点 `/api/ext/rpc`

POST JSON body:
```json
{"ext_id": "task-runner", "method": "task.list", "params": {"limit": 5}, "timeout": 5}
```

返回:
```json
{"ok": true, "result": {"tasks": [...], "total": N}, "ext_id": "...", "method": "..."}
```

或失败:
```json
{"ok": false, "error": "ext_not_running: task-runner", "ext_id": "...", "method": "..."}
```

如 ext 未 spawn,端点自动尝试 spawn 再调用。

## 启动钩子

`main()` 末尾 `srv.serve_forever()` 之前调 `_ext_autostart_from_installed()`,读 `~/.prisir/installed.json`:
```json
{"extensions": [{"id": "task-runner", "enabled": true, "version": "0.1.0"}, ...]}
```

把 `enabled=true` 的 ext spawn 起来。文件缺失/坏/为空 → 静默跳过(用户没装扩展是正常路径)。

## 现有调用点改造(契约变更)

`_ext_rpc_call` 从"返任意 dict"改成"返 `{result: ...} | {error: ...}`",已迁移:
- `prisIragent_web.py:2524` `_schedule_extractor_clear_ai` → todo.list + todo.remove
- `schedule_writer.py:write_to_todo` → todo.add
- 新增 `/api/ext/rpc` 端点直接调用

未来迁移注意:任何旧调用 `if r.get("ok") or r.get("item"):` 这种「非契约 dict 字段访问」都改成 `if not r.get("error"): n += 1`,然后从 `r["result"]` 拿具体数据。

## 已知边界(留给后续)

- **Phase B-2**:前端 DAG 编辑器 + 模板库(task-runner 用户故事)
- **Phase B-3**:Python 后端任务队列升级(与 ext bridge 独立,可并行)
- **Phase B-4**:AI agent 任务派单(走 ext bridge)
- **ext 子进程 sandbox**:目前信任模型,未做 uid/chroot 隔离
- **ext 热重载**:改 ext 源码后需手动重启主进程(或重新启用 ext)
- **ext UI 日志面板**:目前日志只进主进程 logging,UI 不展示

## 教训(给将来)

**[[tasklist-vs-git-truth-m3-35]] 再次发生**:Phase B-1 commit msg 说「主进程转发层 ship」,git log 看 commit 真有,但 grep 主进程代码 `_ext_rpc_call` / `_ext_proxy_dispatch` / `extensions/task-runner` 全 0 命中。**ship 检查必须 grep 真符号,不能信 commit msg**。本次 P2.5+B-0 静态扫测试 52/52 是基于真符号锚点,不会假阳性。

## 相关

- [[p2-5-b-0-ext-rpc-bridge-shipped]] 本任务记忆
- [[tasklist-vs-git-truth-m3-35]] TaskList 假阳性教训
- `extensions/sdk/index.js` Node 端 SDK 实现
- `extensions/task-runner/index.js` 第一个真用桥的扩展