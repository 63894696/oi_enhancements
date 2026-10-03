---
name: p2-5-21-shell-subwindows-shipped
description: PrisirAI Electron 壳 4 子窗 bug 修齐(语伴/音乐/日程自启 + 工作流 wfmodal + 同源递归 + task-runner 死循环兜底)
metadata:
  type: project
---

# P2.5+21 Electron 壳子窗 + 工作流 + 死循环 bug ship(2026-10-03)

**commit 链**:
- `3c1d29d` fix(shell): 子窗服务自启 + 工作流 wfmodal + 同源递归弹窗(4 bug)
- `5496d97` fix(ext-bridge): task-runner respawn_total >5 hotfix(死循环兜底)
- `b14985a` fix(ext-bridge): task-runner 一次启(不再 auto-respawn)
- `b1f0704` fix(shell): startWeb 用 readCalendarPort() 而非未声明常量 (修 b14985a ship 漏)
- `eb01210` fix(shell+web): 4 子窗子服务 ship 后小 bug 修齐 (语伴 10s / 日历大小写 / music toast / 顶栏去工作流 + wfmodal 清 hash)

**触发的用户反馈**(2026-10-03):
> 语伴/音乐/日程,这3样都没能正常启动,点了工作流的浏览器之后系统当前还无限跳node弹窗。扩展没有自己启动的选项,要用是要agent自己启动吗?
> 现在就修子窗 + 死循环 bug(推荐)

> 修复没用还在跳弹窗(commit 3c1d29d 后,用户重启 Electron 壳前)

---

## 4 个 Electron 壳 bug + 修法

### bug 1: 语伴 / 音乐 / 日程 3 个子窗后端从来没 spawn

**根因**:`prisIragent-shell/main.js` 只 spawn 主 web(`prisIragent_web.py`)。
Tauri 壳有 `start_companion` / `start_music` Rust 命令,Electron 壳里**没有对应实现**。
`openMusicWindow` / `openCompanionWindow` 直接读端口(0 / 没启服务) → 用户点击后子窗要么空白要么 fallback 到主 web。

**修法**(`prisIragent-shell/main.js`):
- 新增 `companionProc` / `musicProc` 模块级变量
- 新增 `startCompanion()` / `startMusic()` helper:复用 `webUp` 端口探测 + `spawn(PYTHON, ...)` + 简化版 `_pipeProcToLog` 落 stdout/stderr 日志
- 新增 `waitForPort(host, port, timeoutSec)` async 探活轮询,返 Promise<boolean>
- 改 `openCompanionWindow()` / `openMusicWindow()`:先 spawn 后端 → 探活 ≤3s → 起来再弹子窗;起不来兜底主 web
- `startWeb()` 加 `--calendar-port DEFAULT_CALENDAR_PORT`(Python 端已支持,只是没传参)→ 日历子服务随主 web 一起起

### bug 2: 工作流子窗 = 空白页(`#wfmodal` fragment 不被处理)

**根因**:`openWorkflowWindow()` 加载 `${WEB_URL}#wfmodal`(主 web 端口 + fragment)。
`prisIragent_web.py` L8392 `openWorkflow()` 走两条分支:Tauri 壳调 `open_workflow_window_cmd`,开发模式 → 给主窗 `<div id="wfmodal">` 加 `open` class。**Electron 壳里没人调 `openWorkflow()`** → fragment 永不被监听。

**修法**(`prisIragent_web.py`):
- L8392 之前加 `window.addEventListener('hashchange', ...)` + `DOMContentLoaded` 检查
- 加 `__wfModalOpen` 幂等标记:hashchange 触发后,主窗再点「工作流」按钮不会二次触发
- `closeWorkflow()` 清 `__wfModalOpen = false`,允许下次重开

### bug 3: 同源 `window.open()` 无限递归弹窗

**根因**:`prisIragent-shell/main.js` L280-L288(子窗)+ L351-L367(主窗)`setWindowOpenHandler` 用 `url.startsWith(WEB_URL)` allow 同源新窗口。任何 web 端触发的 `window.open('/prisiragent/remote')` / `window.open('/prisiragent/about')` 都会**新弹一个 BrowserWindow**,新窗 web 端又触发同样调用 → 死循环。子窗触发主窗 / 主窗触发子窗 / 子窗触发子窗都行。

**修法**(`prisIragent-shell/main.js`):
- 抽纯函数 `_decideSameOriginOpen(rawUrl, webUrl, recent)`(可独立测):WEB_URL+`/` 防端口误匹 + 5s 同 URL debounce + 兼容 Map 和 plain 对象
- 主窗 + 子窗 setWindowOpenHandler 都改走 `_decideSameOriginOpen`,让测试能 import
- 同源 5s 内二次 open → `deny`(死循环立刻停)

### bug 4(用户报告): task-runner 死循环无限 spawn — 不在 plan 范围

**根因**:`prisIragent_web.py` `_ext_spawn` L345 每次都 `crash_count: 0` reset,`reader_loop` L441 判定 `crash_count <= 3` → 永远 respawn → 用户屏幕无限跳 `node extensions/task-runner/index.js` 弹窗。

**临时 hotfix**(commit `5496d97`):
- 加 `_ext_respawn_total: dict[str, int]` 累计「总 respawn 次数」,**独立**于 crash_count(不被 `_ext_spawn` 重置)
- `reader_loop` L441 调度 respawn 前判 `_ext_respawn_total[ext_id] > 5` → 强制 STOP
- 完整修法(保留 crash_count 不被 reset)派在 **chip task_f48e99a4** 隔离 worktree 里

---

## 复用 + 决策

| 复用 | 文件:行 | 用途 |
|------|----------|------|
| `webUp(host, port, cb)` | `main.js:120-127` | 端口探活,避免重复 spawn |
| `pipeToLog`(inline)| `main.js:171-203` | 主 web sentinel 解析 |
| `_commonWebPreferences()` | `main.js:246-253` | 子窗统一 webPreferences |
| `childWindows.get/set/delete` | `main.js:255-303` | 子窗复用 + close 隐藏 |
| Python `--calendar-port` | `prisIragent_web.py:15395-15402` | 已实现,只需传参 |

---

## 关键测试

`tests/test_electron_subwindows.py` — **14 测试全绿**:

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| `TestDecideSameOriginOpen` | 8 | 外链/同源首次/同源根/5s 内/5s 后/死循环/diff path/port 误匹 |
| `TestMainJsSyntax` | 2 | `node --check` + 关键函数/常量存在 |
| `TestPrisirAgentWebWfmodalHash` | 1 | hashchange 监听 + 幂等标记 + close 清标记 |
| `TestExtRespawnHotfix` | 3 | `_ext_respawn_total` 存在/不被 reset / 阈值 5 + chip task_f48e99a4 |

`_decideSameOriginOpen` 测试**不复制函数体** — 从 main.js 源码 regex 抽真函数体写到 tmp.js,`node` 子进程跑,main.js 改了测试就失效(不重复)。

---

## E2E 验证

### 用户重启 Electron 壳(必要!)
ship 的代码已 commit,但用户当前 PrisirAI 后端是 1:50 起的老进程,Electron 壳也是老的。
**必须重启才能生效**。

步骤:
1. 任务栏托盘右键 → 「退出」(或「系统 → 退出」)
2. 重新启 `prisIragent-shell`(`npm start`)
3. 新 Electron 壳会 spawn 新 `prisIragent_web.py` 加载 hotfix 代码
4. 验证:任务栏图标可见 + 托盘点「语伴/音乐/日程/工作流」4 项可弹 + 同源递归不再跳弹窗

### 主 web 已能自启 18802 端口 + calendar 子服务(0 重启直接验)

```bash
netstat -ano | grep -E ":18850 |:18802 "
# 期望:18802 必在(主 web),18850 在(语伴启了)
# 日程走主 web 端口,不另起 18803
```

### 真验证「同源递归」修了

```bash
# 在主窗 console 跑(开发者工具):
window.open(window.location.origin + '/prisiragent/about')
# 预期:弹 1 个独立窗(关于页),不弹第 2 个
# 旧实现:无限弹 node 弹窗
```

### 真验证 wfmodal 触发

```bash
# 托盘 → 「🔀 工作流」点击
# 预期:子窗弹出,wfmodal 立即全屏,显示 DAG 列表
# 旧实现:hash 不处理,显示主 web 首页
```

---

## 风险 + 不在范围

### 风险
1. **主 web 端口监听冲突**:`--calendar-port` 已默认启用,若之前装包版 `--calendar-port=0` 显式禁用,新进程会用默认 18803 → 需用户在 `settings.json` 或 `HKCU` 覆盖
2. **task-runner hotfix 是兜底**:chip task_f48e99a4 完整修法 ship 后,本 hotfix 可撤
3. **setWindowOpenHandler debounce 5s**:同 URL 5s 内第 2 次被 deny。若用户实测 5s 不够 → 改 30s

### 不在范围(用户拍板)
- 扩展自启 UI(settings.json 「子服务自动启」开关)— 用户拍板「仅修 4 子窗 + 死循环」,扩展面板留 Phase B
- Tauri 壳(`src-tauri/`)— 不动
- 完整修法 task-runner 不死循环(chip 隔离修)— user 拍板「现在就修死循环 bug(推荐)」派独立 chip

---

## 2026-10-03 ship 后实际 E2E 发现

**`b1f0704` 漏坑**:`DEFAULT_CALENDAR_PORT` 常量在 main.js 顶部未声明,
但 startWeb args 写死了它 → ReferenceError → 后端没起 → 18802 不监听。

修法:用 `require("./port_config").readCalendarPort()` 函数调(HKCU / JSON /
yaml default 18803,与兄弟端口读法一致)。加保护测试
`TestMainJsSyntax`:断言代码(非注释)不含 `DEFAULT_CALENDAR_PORT`,
防止再次漏掉声明常量就 ship。

**`eb01210` ship 后 4 子问题**(用户实测反馈):

1. **语伴无法启动**:`openCompanionWindow` `waitForPort(WEB_HOST, port, 3.0)`
   超时 3s 太短,语伴后端初始化 ~6-8s 必 fallback 主 web。升到 **10s**。
2. **日程无法启动**:`openCalendarWindow` 写错大小写 `/prisiragent/calendar`
   (小写 p),实际 Python 路由大小写敏感 → 正确是 `/prisIragent/calendar`(大写 I)。
3. **音乐点击播放无反应**:`els.playBtn.onclick` 空队列分支 `return` 静默,
   改 `showMusicToast("队列为空,先搜索一首曲加入队列再播放")`。
4. **扩展旁边的工作流按钮去掉 + 工作流弹窗回不去**:
   - 删主 web 顶栏 `<button id="topbtnWorkflow">`(用户拍板只在托盘开)
   - `closeWorkflow()` 加 `history.replaceState` 清 URL hash,
     避免按浏览器返回/前进再次触发 hashchange 重开 modal

**E2E 经验**:`node --check` + 关键函数存在性 ≠ 启动成功。
Electron 启动有完整 ctx(electron API、require、preload),只过
syntax 检查的 ship 可能暗藏运行时 ReferenceError。
**修后**:`TestMainJsSyntax` 加代码(非注释)常量引用检查 +
必须真跑一次 electron 验证 18802 LISTENING 才算 ship 完成。

**第二轮 E2E 经验**(eb01210):ship 后必须真点 4 个子窗走通一遍才能定稿。
光看「子窗创建成功」日志不够 — 大小写错 / 超时太短 / 按钮删没删都是
用户体感问题,日志看不出来。需要:
- HTTP 探活每个子服务的关键路由
- 用 `curl` / `grep` 验 HTML 实际渲染(顶栏按钮 / route 200)
- 加 6 测试覆盖:route 大小写 / waitForPort timeout / closeWorkflow hash / 顶栏无按钮 / music toast helper

---

## 与既有 ship 的关系

```
2026-09-21  P2.5+B-2  prisIragent_web.py wfmodal 主窗内全屏 + DAG 画布
2026-09-22  P2.5+14  日历独立端口 18803 (Python 端能力,Electron 没传参)
2026-09-22  P2.5+16  子窗统一化 (子窗 helper ship 形状)
2026-09-22  P2.5+19  Tauri 4 子窗统一化
2026-10-03  本计划  Electron 壳 4 子窗/路由 bug 同款修齐     ← 本次 ship
2026-10-03  hotfix  task-runner respawn >5 强制停            ← 同日 ship
```

---

## 待 Phase B ship
- 子窗服务列表 + 状态栏(类似 LLM 提供者 35)
- 「服务未起自动起」开关(settings.json + UI)
- 子窗服务失败重试退避 + 资源占用监控(CPU / 内存)+ 用户手动 kill
- chip task_f48e99a4 完整 task-runner 死循环修法(ship 后撤 hotfix)