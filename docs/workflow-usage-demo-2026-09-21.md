# 工作流(Workflow)使用演示 — P2.5+B-3 (2026-09-21)

> 这份文档讲:怎么打开工作流编辑器,怎么搭一个能跑的 DAG,怎么让它真干活,中途怎么取消,运行历史怎么清理。
> 配套版本:B-3 commit `d22067b` 之后 + 本 hotfix(节点双击编辑 + 运行历史清理)。

---

## 1. 五分钟上手(完整链路)

### 1.1 打开工作流面板
顶栏点 **🔀 工作流** → wfmodal 全屏弹出。布局:
```
┌───────────────────────────────────────────────────────────────┐
│ 🔀 工作流                                       [+ 新建][📋 模板]...│
├──────────────────┬──────────────────────────────────────────────┤
│ 任务列表         │ 工具条: [+ 节点] [拖到画布添加节点]            │
│  ▶ 我的日报      ├──────────────────────────────────────────────┤
│  ⏰ daily_summary│           [DAG 画布]                          │
│  ...            │     ┌──n1──┐         ┌──n2──┐                  │
│                 │     │todo  │ ───────▶│todo  │                  │
│                 │     │.add  │         │.list │                  │
│                 │     └──────┘         └──────┘                  │
├──────────────────┴──────────────────────────────────────────────┤
│ 运行历史                                  [🧹 清空]             │
│  r_xxx  t_xxx  failed    09-21 02:30  1.2s  ext_not_running     │
└───────────────────────────────────────────────────────────────┘
```

### 1.2 三种创建方式

| 入口 | 适用场景 | 步骤 |
|---|---|---|
| **+ 新建** 顶栏按钮 | 自由搭 | 点按钮 → 弹输入任务名 → 拖节点 + 编辑 |
| **📋 模板** | 套现成 DAG | 选 `simple_echo` / `build_and_test` / `daily_summary` → 自动填好画布 → 微调保存 |
| **任务列表** 点行 | 编辑已有 | 点 `📂` 载入到画布,改完 💾 保存 |

### 1.3 套模板演示
1. 点 **📋 模板**
2. 选 `daily_summary`(每天 09:00)
3. 自动:画布出现 1 个 todo.list 节点,任务名预填
4. 点 **💾 保存** → 任务名变成 task_id(`t_xxx`)
5. 点 **▶ 运行** → 顶部进度条动起来,节点边框变绿或红
6. 底部 **运行历史** 多一行,点行可看节点级详情

---

## 2. 节点四件套

每节点四件配置,**双击节点** 打开配置面板(图例见 `wf-node-modal`):

### 2.1 ID(ext.method 之上的稳定 id)
- 字符串,默认 `n1` / `n2` / `n3` 顺序
- 改名要同步改其他节点 `needs` 引用 — 保存时自动更新引用

### 2.2 ext.method 调用哪个扩什么方法
- **ext** 下拉:today / system-watchdog / process-scan / task-runner / ...
- **method** 文本:跟 ext 注册的命令一致(`todo.add` / `todo.list` / `process-scan.scan.list`)
- 错了运行报 `ext_not_running` 或 `unknown method`

### 2.3 Params(JSON)
- 任意 JSON,运行时变成 ext method 的参数对象
- 例子:`{"title": "我今天的清单", "tags": ["daily"]}`
- 模板占位:`{{input}}` / `{{filter.result}}`(目前占位符是字符串,后续 B-4 接 AI 派单时换更智能替换)

### 2.4 Needs(逗号分隔)
- 依赖哪些节点先跑完(才能跑我)
- 例子 `n1, n2` → 我等 n1 和 n2 都 ok 才跑
- 自动拓扑:不需要你排顺序 — DAG 解析时按 Kahn 排波次

### 2.5 重试(可选)
```
max_retries: 0~5        # 默认 0(只跑一次,失败就 fail)
backoff:      constant | linear | exponential    # 默认 exponential
               constant:   每次 1s
               linear:     第 N 次 N 秒
               exponential:第 N 次 2^(N-1) 秒(1, 2, 4, 8, 16)
timeout_sec:  1~600     # 默认 30
```
**例子**:爬网页经常 timeout,设 `max_retries:3, backoff:exponential, timeout_sec:10` — 失败一次等 1s 再试,两次 2s,三次 4s,累计最多 7+10=17 秒花 10 秒兜底。

---

## 3. 编辑操作全图

### 3.1 双击编辑(本 hotfix 修)
- **坑历史**:B-2 时 `wfStartDrag` mousedown 调 `e.preventDefault()`,浏览器 dblclick 被吞,双击节点打不开配置面板 — 用户只能点右上 ✏️ 图标,体验割裂。
- **修法**:mousedown 不 preventDefault,只在鼠标移动 > 5px 才真进入「拖拽」状态。单击静止 = dblclick 触发;点击 + 拖动 = 拖拽触发。两不冲突。
- **怎么用**:节点上双击 → 配置面板出来。单击 = 选中;点 ✏️ = 同效(双击的兜底入口)。

### 3.2 拖拽节点
- mousedown 后:
  - **不移动**:双击事件正常派发 → 配置面板开
  - **移动 < 5px**:仍算「没拖」,双击能触发
  - **移动 ≥ 5px**:真拖,左上 = 原 mousedown 点的偏移;SVG 箭头实时跟随
- mouseup 释放,箭头重画

### 3.3 + 节点按钮
- 工具条点 **+ 节点** → 拖到画布 → 落点生成 `n1` / `n2` ...
- 落地后**自动**开配置面板(避免空白节点挂在那)

### 3.4 ✕ 删除节点
- 节点右上 ✕ → 弹 confirm → 删
- 其他节点的 `needs` 引用这个 id 的自动清掉

### 3.5 💾 保存
- 把画布(所有节点的 ext.method.params.needs.retry)序列化 → `task.upsert` 到 task-runner
- 首次保存:生成 `task_id`(_ 当前任务的 id 字段填上)
- 后续保存:**覆盖**(同名 task)

### 3.6 ✓ 校验
- 客户端校验三件事:
  - ext / method 字段非空
  - needs 引用的节点都存在
  - DAG 无环(Kahn 拓扑排一遍,排不到 = 有环)
- 报错 alert + 高亮不通过的字段
- **不调 ext**(只本地校验,不快不重)

### 3.7 ▶ 运行
- 默认 fire-and-forget:立刻返 `run_id` + 后台跑
- 顶部进度条跳;节点边框实时变色(running 琥珀 / ok 绿 / failed 红 / canceled 删除线)

---

## 4. 运行监控

### 4.1 顶部进度条
```
┌────────────────────────────────────────────────────────┐
│ 进度  3/5  ████████████░░░░░░░  60%    [⏹ 取消]      │
└────────────────────────────────────────────────────────┘
```
- `3/5` = 已完成节点 / 总节点
- 进度条 = `done/total` 的百分比
- **⏹ 取消** 按钮 = 点 → 立刻调 `task.run.cancel` → 当前节点的 invokeExt 立刻 reject('aborted') → 不再触发重试

### 4.2 节点实时上色
- 节点跑中 → 边框琥珀色 + 轻闪(`@keyframes wf-running-flash`)
- 节点 ok → 边框绿
- 节点 failed → 边框红 + 鼠标悬停看错误
- 节点 canceled → 边框灰 + 删除线

### 4.3 重试可视化
- 节点卡的右上角小字 `retry 2/3` 显示已重试次数
- tooltip 显示最近一次错误

### 4.4 ⏹ 取消
- 后端走 `AbortController.abort()` → 任何还在跑的 invokeExt 立刻被 Promise.race 抢断
- 当前 attempt 完成(可能 ok/failed)→ 写库 status=canceled
- 不重试 → cancel 比 fail 优先级高
- 兜底:`task.run.cancel` 找不到 run → `run_not_active_or_done`(已结束)

---

## 5. 运行历史(底部面板)

### 5.1 列结构
```
r_xxxx   t_xxx    failed    2026-09-21 02:30:12    1.2s   ext_not_running: todo
   ↑      ↑        ↑                  ↑             ↑            ↑
run_id  task_id  status         开始时间          耗时         错误摘要
```

### 5.2 点行看详情
- 点行 → 弹 alert(JSON.stringify(run, null, 2))
- 包含 `nodes: {n1: {status, ms, attempts, error}, ...}` — 节点级中间结果

### 5.3 🧹 清空(本 hotfix 修)
- 运行历史头行右侧 **🧹 清空** 按钮(红色 hover)
- 点 → confirm(`wf_clear_runs_confirm`)→ 调 `task.runs.clear` → 后端删 runs + node_runs 两张表对应行
- 不影响 tasks 表(任务定义保留)
- 支持按 task_id 过滤:`task.runs.clear {task_id: "t_xxx"}`(目前 wfmodal 调 `{}` 全清;B-4 AI 派单后,UI 上加个 task_id 过滤器)
- **🧹 SQL bug 修**(本次):tid 不传时不能 `bind(null)` 到 `?` 上,要分两支 SQL 跑;否则 `column index out of range`。

---

## 6. 调度器(⏰)

### 6.1 开关
顶栏 `⏰ 调度: 开 / 关`:
- **关 → 开**:`task.schedule.start {interval_sec: 30}`(目前固定 30s 轮询间隔)
- **开 → 关**:`task.schedule.stop`

### 6.2 调度规则
每个 task 的 `schedule` 字段(逗号分隔语义,B-3 用简化 cron-like):
| 写法 | 语义 |
|---|---|
| `""` 或不填 | 不调度(只能手动 ▶ 跑) |
| `never` | 同上 |
| `onstart` | 启动时跑一次 |
| `hourly` | 每小时 0 分跑一次 |
| `daily HH:MM` | 每天 HH:MM 跑(`daily 09:00`) |
| `interval N` | 每 N 分钟跑一次 |

模板 `daily_summary` 用 `daily 09:00` —— 每天 9 点自动跑。

---

## 7. 端到端最小演示(10 分钟跑通)

### 7.1 准备
```
服务:python prisIragent_web.py --port 18899
浏览器:http://127.0.0.1:18899/prisiragent/
扩展:确认 task-runner 已启用(顶栏 🧩 抽屉里看)
```
**task-runner 默认 auto_enable**(P2.5+6 ship),首启自动跑。

### 7.2 第一个 workflow:简单 todo 计数
1. 顶栏 🔀 工作流 → wfmodal
2. 模板按钮 → 选 `simple_echo` → 自动填 1 节点
3. 双击节点 → ext=`todo`,method=`todo.add`,params=`{"title": "测试 workflow", "tags": ["demo"]}`,needs=`""`,重试 max_retries=0
4. 💾 保存 → ▶ 运行
5. 进度条跳,节点变绿,运行历史多一行

### 7.3 第二个:两节点串行(测试依赖)
1. + 节点 → 落画布 → 自动生成 `n1` + 配置面板 → 改 ext=`todo`,method=`todo.add`,params=`{"title": "step1"}` → 保存
2. 再 + 节点 → 落画布 → 自动生成 `n2` → 改 ext=`todo`,method=`todo.list`,params=`{"limit": 5}`,needs=`n1` → 保存
3. 💾 保存 → ▶ 运行
4. 看到:先 n1 变绿 → 再 n2 变绿(拓扑顺序生效)
5. SVG 箭头从 n1 指向 n2 → 拖动 n2 → 箭头实时跟随

### 7.4 测试取消
1. 建一个慢节点:ext=`process-scan`,method=`scan.list`(假设没装会立刻 fail,改测 `timeout_sec: 10` 的 sleep 类)
2. 假设装了 `todos` + 一个 sleep 的扩展(或自己用 `task-runner` 的 `nop.sleep {ms: 30000}`)
4. ▶ 运行 → 进度条跳
5. 中途点 ⏹ 取消 → 节点立刻变灰删除线 + 标 canceled
6. 运行历史多一行,status=canceled,error=`aborted`

### 7.5 测试清空
1. 跑几个 demo → 历史累积 5-10 行
2. 点 🧹 清空 → confirm → 一闪,历史变空
3. 重载 wfmodal → 历史仍空(后端真删了,不是只清 UI)

---

## 8. 高级技巧

### 8.1 模板占位符(B-4 接 AI 派单时换语义)
模板里写 `{{input}}`,将来 AI agent 触发工作流时把这个字符串换成真实输入。
- 简单字符串 replace,不做正则(避免注入)
- 嵌套:`{{filter.result}}` → 取 n=`filter` 的 result 字段

### 8.2 节点命名规范
- 用 `n_<目的>` 更好读:`n_scan_processes` / `n_filter_node` / `n_watch_targets`
- 任务大时命名干净比顺序号强

### 8.3 依赖图诊断
- 如果 wfValidate 报 DAG 有环,看哪个节点排不到(Kahn 跑完 cnt < 节点数 = 有环)
- 通常是 A→B 又 B→A,改 needs 去掉一环

### 8.4 调度器抢占
- 调度器是单线程轮询(30s 一轮),多个任务调度不抢占,顺序排队
- 任务跑超过 30s 时,调度器会在后台看到 `running` 状态,跳过本次触发(避免堆积)
- 长任务改用 fire-and-forget ▶ 手动跑,不要塞调度器

### 8.5 节点持久化查询
- `task.runs {task_id: "t_xxx", include_nodes: true}`(目前 `include_nodes` 不支持,但 node_runs 表本身有全量中间结果)
- B-3.5 计划:`task.run.history(task_id, include_nodes=true)` 节点级历史接口

---

## 9. 已知坑

| 现象 | 根因 | 修法 / 绕过 |
|---|---|---|
| 节点双击不弹配置面板 | B-2 老 bug,`wfStartDrag` mousedown 调 `preventDefault()` 吞 dblclick | **本 hotfix 修**(2026-09-21)。拖拽阈值 5px,移动 < 5px 算单击 → 双击生效 |
| 运行历史累积几千行 | 后端没自动清理 | **本 hotfix 加** 🧹 清空按钮 + `task.runs.clear` 命令 |
| `task.runs.clear` `column index out of range` | tid 不传时 bind null 给 `?` | **本 hotfix 修** — 分支 SQL,无 WHERE 子句走全删 |
| `task.list` 返 `unknown method: command.task.list` | SDK 不剥 `command.` 前缀 | B-3 ship 时修了,`_handleRequest` 剥前缀 |
| 拖拽节点时 SVG 箭头错位 | 拖完没重画 edges | 已修:拖完调 `wfRenderEdges()` |
| 节点 method 写错 | 静默运行时报 `unknown method` | 当前只 console 报错,**后续** UI 加绿/红 exetension 列表校验 |

---

## 10. 相关文档
- [[p2-5-b-0-ext-rpc-bridge-shipped]]:后端 RPC bridge 真接通
- [[p2-5-b-2-workflow-ui-shipped]]:wfmodal UI 雏形
- [[phase-b3-task-queue-shipped]]:任务队列升级(取消 + 进度 + 持久化)
- [[tasklist-vs-git-truth-m3-35]]:静态测试 100% 但真命令调不通的教训
- 源码:`prisIragent_web.py`(wfmodal 段 + i18n + /api/workflow/run_progress 端点)
- 源码:`extensions/task-runner/index.js`(task-runner 扩展 + task.runs.clear)
- 源码:`extensions/sdk/index.js`(SDK `_handleRequest` 剥 `command.` 前缀)