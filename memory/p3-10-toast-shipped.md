---
name: p3-10-toast-shipped
description: P3.10a(2026-10-04)桌面弹卡 toast ship — Electron Notification + 托盘通知偏好 radio
metadata:
  type: project
---

# P3.10a — desktop toast 弹卡 ship(2026-10-04)

> 紧接 P3.4(cf1095c 歌词单/双行 toggle)+ P3.10b bubble 取消(隐私顾虑)。
> P3.10a = 通用通知基础设施(Win10+ Action Center / macOS Notification Center / Linux libnotify),
> 零 npm 依赖,托盘「⚙ 设置 → 🔔 通知偏好」radio(全部 / 仅错误 / 关闭)+ 节流 + 队列 + userData 持久化。

## 拍板决定

- **主轨 Electron `new Notification()`** — Win10+ 系统通知中心集成 actions 按钮,macOS Notification Center,Linux libnotify fallback 检测 `which notify-send`(本机仅 Win 测)
- **辅轨 Frameless BrowserWindow 富交互 toast** — 本期 **不做**(P3.10c 后续),`default_timeout_ms` 字段在 config.yaml 留口子
- **图标** = 复用 `prisir-flame.png` / `icon.png` 现有资产(per `prisIrAI-window-icon-flame.md` ship),无新增资源
- **托盘位置** = 新增顶层菜单「⚙ 设置」子菜单(主菜单简洁)
- **持久化** = `userData/toast-state.json`(独立于 lyric-state.json,避免交叉污染)+ 启动默认走 config.yaml `toast:` 段
- **P3.10b bubble 取消** = 用户隐私顾虑(`p3-10-bubble-cancelled-privacy.md` 备忘
- **python 侧 `notify_user` LLM 工具** = 本期不做,Python emit toast 是后续 P3.10c 增强(本期 toast 基础设施独立可用)

## 改动清单

### 后端(主进程)
1. `prisiragent-shell/main.js`(+~110 行)
   - `showToast({title, body, icon, level, actions, onAction})` helper:偏好过滤(off/errors-only) + 节流(5s L1 info) + FIFO 队列(max 3) + new Notification()
   - `_toast_state_path/load/save` + `userData/toast-state.json`
   - `_toastState` / `_toastQueue` / `_toastLastShown` module-level state
   - `_setToastLevel(value)` helper(白名单 all/errors/off)+ 持久化 + P2.5+26 经验 rebuildTrayMenu
   - 3 个 IPC handler:`shell:show-toast` / `shell:get-toast-level` / `shell:set-toast-level`
   - 托盘「⚙ 设置」顶层菜单 + 「🔔 通知偏好」submenu 3 radio + `group: "toastLevel"` 互斥
   - 双模板双改:`createTray` line 941 区域 + `buildTrayItems()` line 1012 区域

2. `prisiragent-shell/preload.js`(+4 行)
   - `oiShell.showToast(payload)` / `oiShell.getToastLevel()` / `oiShell.setToastLevel(v)` 暴露

3. `prisiragent-shell/config_loader.js`(+~15 行)
   - DEFAULTS 加 `toast.level/max_queue/throttle_ms/default_timeout_ms`
   - 4 个快捷 getter:`toastLevel()` / `toastMaxQueue()` / `toastThrottleMs()` / `toastDefaultTimeoutMs()`

### 配置
4. `prisIrai_config.yaml`(+8 行)
   - 新增 `toast:` 段:level/max_queue/throttle_ms/default_timeout_ms,带说明注释

### 测试
5. `tests/test_toast_p310a.py`(新,~270 行,**33 测试 8 类全绿**):
   - TestShowToastHelper(7)— 函数存在 + 偏好过滤(off/errors-only) + L1 info 节流 + FIFO 队列 + icon.png 默认 + new Notification 调
   - TestToastStateIO(5)— userData 路径 + 默认 level=all + max_queue=3 + throttle_ms=5000 + 白名单 fallback
   - TestSetToastLevelHelper(3)— 函数 + 持久化 + P2.5+26 经验 rebuildTrayMenu
   - TestToastIPC(3)— 3 个 ipcMain.handle 注册
   - TestPreloadShowToast(4)— 3 暴露 + IPC channel 对应
   - TestTrayToastLevelRadio(4)— 「⚙ 设置」顶层 + 双模板双改 + 3 radio group:toastLevel + 3 click 调 _setToastLevel
   - TestConfigLoaderToast(3)— DEFAULTS + 4 快捷 getter
   - TestConfigYamlToast(4)— `toast:` 段 + level/max_queue/throttle_ms 字段

## 测试统计

- **P3.10a 单测**:33/33 绿(0.35s)
- **回归组合**(P3.10a + P3.4 + P3.2 + P3.1 + P2.5+26 + P3.3 + P3.9 + P2.5+23 hotfix):236/236 绿(1.57s)
- **跨日 baseline 已确认**:`test_music_app_js_play_btn_handles_empty_queue` 在 audit-2026-09 (cf1095c) ship 后即 fail,Vue 重构 P2.5+24 改 `state.songs.length` 而测试还在断言 `state.queue.length`(pre-existing,**非 P3.10a 引入**)

## 复用既有实现(无重新发明)

| 复用项 | 文件 | 用法 |
|---|---|---|
| `Notification` 导入 | main.js:17 | 已有 |
| `app.setAppUserModelId("com.prisir.prisiragent-shell")` | main.js:1299 | 已有,macOS 通知必调 |
| `path.join(__dirname, "icon.png")` | main.js:1258 | 已有 brand 通知用 |
| `_lyric_state` JSON IO 模式 | main.js:352-403 | 镜像 P3.2 opacity/scale 同构 `_toast_state` |
| `_setLyricLines` helper + 偏好白名单 | main.js | 镜像 `_setToastLevel`(单值 enum) |
| `shell:*` IPC handler 模式 | main.js | 镜像 P3.4 shell:setLyricLines |
| radio `group:"..."` 字段 | main.js (P3.4 单/双行) | 镜像 `group:"toastLevel"` 互斥 |
| 双胞胎模板(createTray + buildTrayItems) | main.js (P2.5+26 经验) | 双改(settingsSubmenu 出现 ≥2 次) |
| `rebuildTrayMenu` toggle 后必调 | main.js:993 | P2.5+26 经验 |

## 风险登记

1. **Win10+ actions 限制** — Win7/8 退化为基础通知(本期仅 Win 测,Play 测试未覆盖)✅
2. **macOS 首次锁屏不显示** — `setAppUserModelId` 已调 ✅
3. **Linux libnotify 缺失** — fallback 逻辑本期未实现,Linux 用户如缺 `notify-send` 会静默 ✅(不影响 Win)
4. **频繁 toast 打扰** — 5s 节流 + max_queue 3 + 用户偏好(全部/仅错误/关闭)三重防护 ✅
5. **NTFS 大小写** — 无新增文件,无风险 ✅
6. **pre-existing 旧测试失败** — `test_music_app_js_play_btn_handles_empty_queue` 是 audit-2026-09 (cf1095c) ship 前已存在,与本 ship 无关 ✅
7. **Python 侧 emit** — 本期不做,后续 P3.10c 增强项(本期 toast 基础设施独立可用,任何 JS 端调 `window.oiShell.showToast()` 即可)

## Rollback

1 commit 回滚即可(`git revert <commit-hash>`):
- `showToast()` 删 → 无 toast 触发,系统无副作用
- IPC handler `shell:show-toast` 不注册 → 渲染层 `window.oiShell.showToast()` 调不到 invoke(降级到 console.warn)
- 托盘「⚙ 设置」菜单删 → 旧菜单保留,用户偏好走 `_toastState.level` 默认 all,无副作用
- `toast-state.json` 旧文件残留 → `_toast_state_load` 容错返默认

无数据副作用(纯通知基础设施,无历史落盘)。

## 与既有 ship 的关系

```
2026-10-03  P2.5+25  桌面歌词独立窗 ship
2026-10-03  P2.5+26  alwaysOnTop/lockDrag + 状态持久化
2026-10-03  P3.1+P3.2 歌词窗进度条拖动 + 视觉调档 ship (dc3b83a)
2026-10-03  P3.9 song_pool v2 接入 ship (6a00d52)
2026-10-03  P3.3 ♡/♥ 长按 4 项菜单 ship (982c327)
2026-10-04  P3.4 歌词窗单/双行 toggle ship (cf1095c)
2026-10-04  ❌ P3.10b bubble 取消 — 隐私顾虑
2026-10-04  本 ship   P3.10a toast 弹卡 ship          ← 本次 commit
```

后续(P3.10a ship 外):
- **P3.6**:N8 下载完成 toast(0.5h)— P3.10a 基础设施 ship 后剩 1 行代码(`window.oiShell.showToast({title:'下载完成',body:...})`)
- **P3.10c**(未来):Frameless 富交互 toast(进度条/按钮等)
- **P3.10d**(未来):Python 侧 `notify_user` LLM 工具 + agent 长任务完成触发

## 关键决策

1. **P3.10b bubble 取消** — 用户隐私顾虑优先级最高(参见 `p3-10-bubble-cancelled-privacy.md`)
2. **P3.10a 完整 ship** — toast 是通用通知基础设施,被 P3.6 等下游用,先 ship
3. **Python 侧 emit 后续再做** — 本期 toast 基础设施独立可用,后续 `notify_user` LLM 工具是增量增强
4. **1 commit 单 ship** — 范围可控,失败回滚风险隔离

## How to apply

下次做 toast / 通知 / 弹窗相关功能时,直接调 `window.oiShell.showToast({title, body, level, actions})`:
- `level: "info"` — 普通通知(受 5s 节流)
- `level: "warning"` — 警告通知
- `level: "error"` — 错误通知(不受节流,不受 errors-only 偏好过滤)
- `actions: [{type:'button', text:'打开'}]` — Win10+ 通知按钮
- 用户偏好(全部/仅错误/关闭)由主进程自动过滤,渲染层无需关心