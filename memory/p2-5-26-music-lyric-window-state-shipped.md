---
name: P2.5+26 music 桌面歌词窗 alwaysOnTop/lockDrag 状态 ship
description: 2026-10-03 桌面歌词窗 alwaysOnTop 开关 + 拖动锁/解锁 + bounds 持久化 + 主进程 ↔ 渲染层 state 同步
metadata:
  type: project
---

# P2.5+26 — 桌面歌词窗 alwaysOnTop / lockDrag / bounds 状态 ship(2026-10-03)

## 一句话

P2.5+25 ship 桌面歌词独立窗之后用户提的 3 项可关:alwaysOnTop 太强 / 拖动易误触 / 重启状态不保持 → 托盘 🎤 桌面歌词改 submenu 5 项(打开 + 2 checkbox + 锁定位置 + 关闭)+ 主进程 `userData/lyric-window-state.json` 落盘 + 4 新 IPC + preload 暴露 + LyricOnlyView bootstrap getLyricState + 订阅 onLyricStateChanged webContents.send 推。

## 关键决策

- **菜单位置** = 托盘子 menu「更多 × 子项」(用户拍板)
- **状态存储** = 单一 JSON `userData/lyric-window-state.json`(alwaysOnTop + lockDrag + bounds x/y/w/h)
- **alwaysOnTop 实现** = `w.setAlwaysOnTop(bool, 'floating'|'normal')`,**不重建 BrowserWindow**(保留位置 + lock 状态)
- **lockDrag 实现** = LyricOnlyView 根 `:class="{ 'lyric-locked': lock }"` + `lyric.css .lyric-locked #drag-bar { -webkit-app-region: no-drag }`,**不阻塞 webContents**(窗仍可缩放/双击关)
- **state 同步** = 主进程 toggle 完 → `webContents.send('shell:lyricStateChanged', {...})` → preload `onLyricStateChanged(cb)` 订阅 → LyricOnlyView apply lock ref。**不依赖 ws**(主进程直推,低延迟 0 异步竞态)
- **bounds 持久化** = `w.on('move'/'resize')` debounce 250ms → `_lyric_state_save` JSON(高频事件降 IO);📌锁定位置菜单项立刻 flush 强制写盘
- **0 后端改动** = lyric_state 完全壳侧主控,后端 `_cfg_to_lyric_dict` 不动

## 文件清单

**新增 2 个**:
1. [tests/test_music_lyric_vue_p26.py](tests/test_music_lyric_vue_p26.py) — 31 测试(7 类:StateIO/StateApply/IPC/TraySubmenu/CssLocked/ViewBootstrap/PreloadP26)
2. [memory/p2-5-26-music-lyric-window-state-shipped.md](memory/p2-5-26-music-lyric-window-state-shipped.md) — 本备忘

**修改 5 个**:
1. [prisiragent-shell/main.js](prisiragent-shell/main.js)(+~150 行):
   - `_lyric_state_path/load/save` + 全局 `let _lyric_state = _lyric_state_load()`(模块加载即初始化)
   - `_lyric_apply_state(w)`:`setAlwaysOnTop(state.alwaysOnTop, 'floating'|'normal')` + `setBounds(...)`
   - `_createChildWindow` lyric-only 分支:`ready-to-show` + 3.5s fallback 双触发 apply;move/resize 250ms debounce → `_lyric_state_save`
   - `_toggleLyricAlwaysOnTop / _toggleLyricLockDrag / _lockLyricCurrentBounds / _closeLyricWindow` 4 helpers(被托盘 + IPC 共用)
   - `_notifyLyricWindow('shell:lyricStateChanged', payload)` helper:webContents.send push(主进程主动推到渲染层)
   - 托盘 `multiWindowSubmenu` 「🎤 桌面歌词」 改 submenu 5 项 + 2 checkbox 状态绑 `_lyric_state`
   - 抽出 `buildTrayItems()` 函数 + `rebuildTrayMenu` let(toggle 后重建菜单因 Electron Menu.checkbox 是构造期属性)
   - 4 新 IPC handler:`shell:toggleLyricAlwaysOnTop` / `shell:toggleLyricLockDrag` / `shell:getLyricState` / `shell:setLyricBounds`(后 2 个 setter 含 bounds 字段校验 w>=480 h>=240)
2. [prisiragent-shell/preload.js](prisiragent-shell/preload.js)(+~12 行):
   - `prisIragent.toggleLyricAlwaysOnTop / toggleLyricLockDrag / getLyricState / setLyricBounds` 4 invoke
   - `prisIragent.onLyricStateChanged(cb)` 返回 unsubscribe(渲染层 onBeforeUnmount 调)
3. [companion/static/music-vue/src/views/LyricOnlyView.vue](companion/static/music-vue/src/views/LyricOnlyView.vue)(+~30 行):
   - `const lock = ref(false)` + `let unsubscribeState = null`
   - `onMounted`:IPC `getLyricState` 拉初始态 → `lock.value = s.lockDrag` + 订阅 `onLyricStateChanged(payload)` → `lock.value = payload.lockDrag`
   - `onBeforeUnmount`:调 `unsubscribeState()` 清理
   - 根 div `:class="{ 'lyric-locked': lock }"`
4. [companion/static/music-vue/src/styles/lyric.css](companion/static/music-vue/src/styles/lyric.css)(+~12 行):
   - `.lyric-only-view.lyric-locked #drag-bar { -webkit-app-region: no-drag; cursor: not-allowed; opacity: 0.4; background: rgba(193,77,58,0.18); }` + `:hover { opacity: 0.85 }`
   - 原 `#drag-bar` drag 段不动(向后兼容)
5. [memory/MEMORY.md](memory/MEMORY.md) — +1 行索引

## 菜单设计(托盘「🎤 桌面歌词」submenu)

```
🎤 桌面歌词 ▶
   ├─ 打开歌词窗口           click: openLyricWindow
   ├─ ☑ 始终在上             type: 'checkbox', checked: _lyric_state.alwaysOnTop, click: _toggleLyricAlwaysOnTop
   ├─ ☐ 拖动已锁定           type: 'checkbox', checked: _lyric_state.lockDrag,    click: _toggleLyricLockDrag
   ├─ 📌 锁定当前位置         click: _lockLyricCurrentBounds
   └─ 🚪 关闭歌词窗口         click: _closeLyricWindow
```

> **关键**:`type: 'checkbox'` Electron Menu 原生支持。`checked` 是构造期属性,toggle 完必须 `rebuildTrayMenu()` 重建菜单才能更新勾选状态 — 这是 ship memory 必须强调的点,否则 UI 永远显旧态。

## 状态同步流程

```
[托盘 checkbox 勾选] 或 [IPC shell:toggleLyricAlwaysOnTop/LockDrag]
   ↓
主进程 _toggleLyricAlwaysOnTop() / _toggleLyricLockDrag()
   ↓
1. _lyric_state.{alwaysOnTop|lockDrag} = next
2. _lyric_state_save() → userData/lyric-window-state.json
3. w.setAlwaysOnTop(next, level) 若窗存在
4. rebuildTrayMenu() → tray.setContextMenu(new Menu)
5. _notifyLyricWindow('shell:lyricStateChanged', payload)
   ↓
lyric 子窗 webContents 收到 shell:lyricStateChanged
   ↓
preload onLyricStateChanged(cb) 回调 → cb(payload)
   ↓
LyricOnlyView lock.value = payload.lockDrag → CSS .lyric-locked toggle
```

## 测试结果

```
pytest tests/test_music_lyric_vue_p26.py -v
  → 31/31 绿

pytest tests/test_music_lyric_vue.py tests/test_music_lyric_vue_p26.py \
         tests/test_music_vue_build.py tests/test_music_multi_source.py \
         tests/test_song_pool_and_favorite.py tests/test_electron_subwindows.py
  → 132/133 绿(1 个 pre-existing 失败 = `test_music_app_js_play_btn_handles_empty_queue`,
    grep `state.queue.length > 0` 在已 commit `c5889c5` P2.5+24 ship 替换为 Vue 工程的旧 app.js,
    与本 ship 无关)
```

## E2E 验证

- **node --check** main.js + preload.js → 0 syntax error
- **vite build** vue-tsc 0 type error + 多入口构建成功:
  - `lyric.js`: 4.02 → **4.46 KB**(+0.44KB,lock ref + onMounted + 订阅)
  - `lyric.css`: 1.99 → **2.20 KB**(+0.21KB,locked 段)
  - `main.js` 19.60 KB + vue 共享 70.10 KB 不变
- **built CSS grep**:`lyric-locked` + `no-drag` 出现在 dist/assets/lyric-*.css
- **curl lyric.html**:200 + 包含「桌面歌词」title + `/music-vue/assets/lyric-` chunk 引用
- **Puppeteer E2E**(dev mode / 无 IPC):
  - 4 关键 DOM(drag-bar/stage/meta/conn-tag)全 render
  - `lyricLockedClass: false`(lock 默认 false,根无 .lyric-locked class)
  - body background `rgba(0,0,0,0)`(透明窗关键)
  - conn-tag `🟢`(ws 已连)
  - onMounted 在 dev/Puppeteer 环境无 `prisIragent` IPC,静默 no-op,不抛错

## 复用 / 借鉴点

| 复用 | 来源 | 用途 |
|---|---|---|
| userData JSON 落盘 | `main.js:866 brand-notify-seen.json` | `_lyric_state_path/load/save` 完全镜像 `_brandLoadSeen/_brandSaveSeen` 模式 |
| JSON 损坏容错 + 备份 | 同上 | 坏 JSON → renameSync `.corrupt-<Date.now()>` + 兜底默认 + logWarn |
| Electron Menu checkbox | Electron 官方 API | `type: 'checkbox'` + checked 构造期属性 + rebuild menu |
| webContents.send 主进程推渲染层 | Electron 官方 IPC | `_notifyLyricWindow` helper,toggle 后自动 push |
| onMounted + onBeforeUnmount | Vue 3 Composition API | bootstrap 拉初始态 + 组件卸载 unsubscribe 清理 |

## 风险登记(已规避)

1. **Electron Menu checkbox 状态与 _lyric_state 不同步** — rebuild 没调或 rebuild 失败
   - 对策:`rebuildTrayMenu()` 在每次 toggle 后必调 + try/catch 包住 + logWarn
2. **`w.setAlwaysOnTop(bool)` 在 Win 上有时不生效** — 已知 Win API 限制
   - 对策:第二参数必须传 `level('floating'/'normal')`,Win 上才能稳定切换
3. **`w.getBounds()` 高频 IO** — move/resize 事件每秒数十次
   - 对策:`setTimeout 250ms` debounce + `_lockLyricCurrentBounds` 菜单项主动 flush
4. **lyric-window-state.json 损坏** — 用户手动编辑失败
   - 对策:load 时 try/catch,坏则 rename 备份 + 返默认 + logWarn「first run」
5. **托盘 submenu checkbox 用户视角反直觉** — Win 用户看到子层菜单
   - 对策:子菜单项标签描述清晰(始终在上 / 拖动已锁定),emoji 1-1 配
6. **lyric 子窗不存在时 toggle 仍落盘** — 用户在关歌词窗时勾选菜单
   - 对策:toggle helper 检查 `childWindows.get('lyric')` 可选,只 setAlwaysOnTop 不存在时跳过,落盘仍执行(下次开窗 apply)
7. **`shell:setLyricBounds` 渲染层伪造 bounds** — 渲染层可传 w=10
   - 对策:严格校验 w>=480 h>=240 + x/y finite

## 与既有 ship 的关系

```
2026-09-19  M3.29.3     桌面歌词 HTML/CSS/JS + /lyrics 路由
2026-10-03  P2.5+24     music 子窗 Vue 3 + Pinia 重写(commit c5889c5)
2026-10-03  P2.5+25     桌面歌词独立窗 ship(commit c0085a2)— Vue + Electron 透明窗 + 双击关闭
2026-10-03  本 ship      P2.5+26 alwaysOnTop 开关 + lock/unlock 拖动 + 状态持久化   ← 本次 ship
```

## 下一步

- **P2.5+27**(可选):歌词窗 CSS 主题切换(故宫/国色/水墨)
- **P2.5+28**(可选):歌词窗 click-through 模式(歌词行不抢鼠标焦点)
- **Phase 2 cover art**:iTunes Search API 异步封面(用户已拍板,排在 P2.5+27 后)
- 后续 LX 真实源接入与本 ship 无关,独立排期

**How to apply:** 桌面歌词独立窗所有用户可调状态(alwaysOnTop/lockDrag/位置)集中到托盘子菜单 + 单 JSON 落盘 + 主进程↔渲染层 webContents.send 同步,后续任何 desktop 子窗状态调整都应走此模式(centralize 决策 + 集中落盘 + 主动推送),不再散落到各子窗菜单。
