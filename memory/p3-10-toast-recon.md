# p3-10 — 桌面弹卡 toast + PrisirAI 整合 调研

日期: 2026-10-04
范围: A. 桌面弹卡 toast 选型 + C. PrisirAI 集成路径
**取消: B. 音乐识别浮泡 — 本期不做** (原因见下)

---

## B. 音乐识别 — 本期不做

候选引擎 (AudD / acrcloud / ShazamKit / midomi) 公共 API 都需 API key。AudD 仅 Firefox 扩展包内置 token,Python 集成无 keyless 路径;acrcloud/ShazamKit 同需注册付费 key,且 ShazamKit 仅 Apple 平台。叠加隐私顾虑 (需持续录音 + 上传云端) 与当前用户基盘的成本结构,暂缓。

**结论: 音乐识别浮泡功能本期不做。留待 P3.x+ 在浏览器原生 MediaSession API + 用户主动触发路径成熟时再开。**

---

## A. 桌面弹卡 toast 选型

### A.1 候选方案对比

| 方案 | 依赖 | 平台覆盖 | 交互能力 | 自定义 UI | 推荐度 |
|------|------|----------|----------|----------|--------|
| Electron `new Notification()` | 0 | Win10+/macOS/Linux | 系统动作按钮 (Win10+/macOS) | 仅 title/body/icon | 主轨 |
| node-notifier | npm | Win/macOS/Linux | 弱 (回退到系统) | 同上 | 备选 |
| 自绘 Frameless BrowserWindow | 0 (复用 Vue) | 全平台一致 | 任意 (HTML/JS) | 100% | 辅轨 |
| 应用内 toast 组件库 | npm | 仅应用窗口内 | 任意 | 100% | 仅辅轨 |
| Tauri 通知插件 | — | — | — | — | 不适用 (本项目 Electron) |

### A.2 推荐方案: 系统通知 + 自绘 BrowserWindow 双轨

**主轨 — Electron Notification (零依赖)**

```js
// prisiragent-shell/main.js (主进程)
const { Notification } = require('electron')

function showToast({ title, body, icon, level = 'info', actions, onAction }) {
  const n = new Notification({
    title,
    body,
    icon: icon || path.join(__dirname, 'assets/prisir-toast-256.png'),
    silent: level === 'info',
    urgency: level === 'error' ? 'critical' : 'normal',
    timeoutType: 'default',
  })
  if (Array.isArray(actions)) n.actions = actions  // [{type:'button', text:'打开'}]
  n.on('action', (_, idx) => onAction?.(idx))
  n.on('click', () => onAction?.(-1))
  n.show()
}
```

优势: 零 npm 依赖;走 OS 原生通知中心 (Windows Action Center / macOS Notification Center / Linux libnotify);不抢焦点;锁屏可见。

**辅轨 — Frameless BrowserWindow (富交互用)**

```js
// prisiragent-shell/main.js
const { screen, BrowserWindow } = require('electron')

function showRichToast({ url, width = 360, height = 120, duration = 4000 }) {
  const display = screen.getPrimaryDisplay()
  const { width: sw, height: sh } = display.workAreaSize
  const win = new BrowserWindow({
    width, height, frame: false,
    transparent: true, alwaysOnTop: true, skipTaskbar: true,
    resizable: false, movable: false, focusable: false,
    hasShadow: true,
    x: sw - width - 20, y: sh - height - 20,
    webPreferences: {
      preload: path.join(__dirname, 'preload-toast.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  })
  win.loadURL(url)
  setTimeout(() => !win.isDestroyed() && win.close(), duration)
}
```

适用场景: 歌词预览 / 播放控件 / "加入歌单" 按钮 / 进度条等多控件富交互 (歌词浮泡直接复用 LyricOnlyView 组件)。

### A.3 关键坑点

1. **Windows 限制**: `actions` 按钮 + 自定义声音仅 Win10+。Win7/8 退化为基础通知;启动期检测 `process.getSystemVersion()` 决定是否启用 actions。
2. **macOS 权限**: 必须在 `app.whenReady()` 后调 `app.setAppUserModelId('com.prisir.ai')`,否则系统不显示。`Info.plist` 需 `NSUserNotificationAlertStyle = alert`。
3. **Linux libnotify**: Alpine / 极简发行版可能无 `notify-send`,fallback 到 BrowserWindow。检测 `which notify-send` 失败时禁用系统通知。
4. **托盘干扰**: 已有 tray 图标 (per `p2513-shell-tray-companion-music-calendar.md`),toast 触发时不应抢焦点或修改 tray menu。
5. **节流/合并**: 短时间内多次 toast 需排队 (FIFO,最大并发 3,L1 info 节流 5s/类,L2 error 立即);参考 `p2-5-23-hotfix-playback-prev-favorite.md` 的限流模式。
6. **图标**: 通知中心会缩到 48-128px,原窗口图标 (per `prisIrAI-window-icon-flame.md` 256×256+) 过大,需新增专用 256×256 toast 图标 (建议 `prisir-toast-256.png`)。
7. **NTFS case-folding**: 新建 `preload-toast.js` 时用全小写文件名 (per `ntfs-case-folding-git-add-fail.md`),否则 git add 静默失败。
8. **macOS 锁屏**: app 未在前台运行过则锁屏下不显示;首次启动需引导用户解锁一次。
9. **多显示器**: `screen.getPrimaryDisplay()` 在副屏不显示;应取触发 toast 的源显示器 (`BrowserWindow.fromWebContents(evt.sender).getBounds()` 推断)。
10. **DPI**: Frameless 窗口在 125% / 150% DPI 下需设 `webPreferences.zoomFactor = 1` 防 UI 错位。

### A.4 测试覆盖

参考 `p2-5-23-hotfix-playback-prev-favorite.md` 40/40 测试模式:
- 单元: Notification payload 构造、actions 数组校验、节流队列、回收超时
- 集成: 主进程 IPC handler → preload 桥 → 渲染进程 invoke 回路
- E2E: tray 右键菜单加 "发送测试 toast" (开发期入口)
- 视觉: Frameless 窗口在不同 DPI / 多显示器下截图
- 跨平台: Win/macOS/Linux 三平台 CI smoke (用 `xvfb-run` 跑 Linux)

---

## C. PrisirAI 集成路径 (仅 toast)

### C.1 现有架构 (回顾)

```
prisIragent_web.py (Python, 业务/AI 主对话)
  ↕ WebSocket / HTTP (端口 18826 主, 18803 日历独立)
prisiragent-shell/main.js (Electron 主进程)
  ↕ contextBridge
prisiragent-shell/preload.js (渲染进程 IPC 桥)
  ↕
companion/static/music-vue/ (Vue 3 渲染进程)
```

(拓扑参考 `p2513-shell-tray-companion-music-calendar.md` + `p2-5-19-tauri-subwindows-shipped.md`)

### C.2 toast 集成改动清单

**1. `main.js` 新增 IPC 处理器**
```js
ipcMain.handle('shell:show-toast', async (_evt, payload) => {
  return showToast(payload)  // 调 A.2 主轨函数
})
ipcMain.handle('shell:show-rich-toast', async (_evt, payload) => {
  return showRichToast(payload)  // 调 A.2 辅轨函数
})
```

**2. `preload.js` 暴露桥**
```js
contextBridge.exposeInMainWorld('prisIrShell', {
  ...existing,
  showToast: (payload) => ipcRenderer.invoke('shell:show-toast', payload),
  showRichToast: (payload) => ipcRenderer.invoke('shell:show-rich-toast', payload),
})
```

**3. Python 后端发指令**
`prisIragent_web.py` 已用 WebSocket 推 shell 事件 (日历/番茄钟)。新增消息类型:
```python
await ws.send_json({
  "type": "shell.toast",
  "payload": {"title": "...", "body": "...", "level": "info", "actions": [...]}
})
```
`main.js` 收到后转发到渲染进程 → 调 `window.prisIrShell.showToast()`。

**4. 主对话 LLM 新增 `notify_user` 工具**
复用 `prisIr-agent-main-chat-hook.md` 的 EXEC 工具模式,新 schema:
```json
{
  "name": "notify_user",
  "description": "弹一个桌面 toast (用户不在主窗口时也能看到)",
  "input_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "body": {"type": "string"},
      "level": {"enum": ["info", "success", "warning", "error"], "default": "info"}
    },
    "required": ["title"]
  }
}
```
LLM 自动调用场景:
- 番茄钟完成 (per `priSIR-p258-schedule-extractor-shipped.md`)
- 日历提醒触发 (per `p2514-calendar-independent-port-shipped.md`)
- 视频/音频转码完成 (per `prisir-publisher-module.md` 后置通知)
- Agent 长任务完成 (per `phase-b1-task-runner-shipped.md`)

**5. 音乐模块联动 (P3.10 主驱动)**
扩展 `companion/music/` 模块,新增事件:
- `track.changed` → 主轨 toast "正在播放: <title> - <artist>"
- `queue.empty` → 主轨 toast "歌单已播完"
- 上下首 / 收藏 toggle 反馈 (per `p2-5-23-hotfix-playback-prev-favorite.md`)
- `lyric.line` (可选) → 辅轨 BrowserWindow toast 显示当前歌词行

### C.3 复用现成能力

| 已有能力 | 复用方式 |
|---------|---------|
| Tray 子菜单 (`p2-5-16-p2-5-17-subwindows-and-tray-shipped.md`) | 增加 "通知偏好" 子菜单 (开/关/仅错误) |
| 独立日历端口 18803 (`p2514-calendar-independent-port-shipped.md`) | toast 触发源之一,无需新端口 |
| Skills 工作台 (`prisIr-skills-workbench-phase-2.md`) | 新增 skill "send-desktop-toast" 暴露给 Agent |
| config.yaml (`p2-5-15-config-yaml-shipped.md`) | 加 `toast:` 段 (enabled, max_queue, default_timeout, throttle_ms) |
| LyricOnlyView (`companion/static/music-vue/src/views/LyricOnlyView.vue`) | 歌词富 toast 直接复用其组件 |

### C.4 P3.10 实施步骤

| 步骤 | 改动 | 验证 |
|------|------|------|
| S1 | main.js 加 `showToast()` + IPC handler | 单元 5/5 |
| S2 | preload.js 暴露 `prisIrShell.showToast` | 集成 3/3 |
| S3 | Python ws 消息类型 `shell.toast` 透传 | 集成 4/4 |
| S4 | 主对话 LLM 加 `notify_user` 工具 | 测 6/6 |
| S5 | music 模块事件接 toast (`track.changed`/`queue.empty`) | E2E 5/5 |
| S6 | 歌词富 toast (辅轨 BrowserWindow,复用 LyricOnlyView) | E2E 4/4 |
| S7 | config.yaml `toast:` 段 + tray "通知偏好" 开关 | 单元 3/3 |
| **合计** | **7 步** | **30/30 测试绿** |

预计 commit: `feat(shell): desktop toast 集成 + music track.changed/queue.empty 触发 (P3.10)`

### C.5 风险与不做项

| 风险 | 缓解 |
|------|------|
| 用户被频繁 toast 打扰 | config.yaml 默认 5s 间隔节流;L1 info 节流,L2 error 立即;用户可在 tray 关闭 |
| 锁屏下 macOS 不显示 | 首次启动后引导用户解锁一次;之后常态显示 |
| 歌词浮泡与 LyricOnlyView 同时开 | 富 toast 仅当 LyricOnlyView 未开时显示 (per `dc3b83a` 已 ship 进度条) |
| 现有扩展 Tab 不知情 | toast 是 shell 层,扩展不感知;扩展若也想触发,经由 Skills 工作台路由 |
| Win7/8 兼容性 | 检测系统版本后退化为基础通知 (无 actions),UI 上提示用户升级 |

---

## 待拍板

1. **图标**: 用现有 `prisIrAI-window-icon-flame.md` 的 prisir-flame.png 还是新增专用 256×256 toast 图标?
2. **默认开/关**: 首次启动默认 enabled,还是引导用户确认?
3. **歌词富 toast (辅轨)**: P3.10 一并 ship,还是先只做系统通知?
4. **托盘菜单"通知偏好"**: 放在 tray 主菜单还是子菜单 (per `p2-5-16-p2-5-17-subwindows-and-tray-shipped.md`)?

## 参考

- 架构: `memory/p2513-shell-tray-companion-music-calendar.md`, `p2-5-19-tauri-subwindows-shipped.md`
- 主对话 EXEC 工具: `memory/prisIr-agent-main-chat-hook.md`
- 队列/限流模式: `memory/p2-5-23-hotfix-playback-prev-favorite.md`
- NTFS 大小写坑: `memory/ntfs-case-folding-git-add-fail.md`
- 窗口图标: `memory/prisIrAI-window-icon-flame.md`
- Skills 工作台: `memory/prisIr-skills-workbench-phase-2.md`
- config.yaml 化: `memory/p2-5-15-config-yaml-shipped.md`
- 歌词窗已 ship 进度条: commit `dc3b83a` (`memory` 未单独立条)
