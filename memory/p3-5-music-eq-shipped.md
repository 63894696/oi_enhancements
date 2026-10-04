---
name: p3-5-music-eq-shipped
description: P3.5(2026-10-04)music 10 段 EQ 均衡器 ship — Web Audio BiquadFilterNode × 10 + 6 预设 + 主开关 + 三入口同步
metadata:
  type: project
---

# P3.5 music 10 段 EQ 均衡器 ship(2026-10-04)

## Context

**用户原始诉求**:「继续 P3.x 流水线,做 P3.5 N5 EQ」
**位置**:11 项主流播放器优化的第 5 项 N5「EQ 均衡器」
**已 ship 前置**:P3.1+P3.2 歌词进度条+视觉调档、P3.9 song_pool v2、P3.3 ♡/♥ 长按菜单、P3.4 歌词单双行 toggle、P3.10a toast 弹卡
**调研**:落雪 lx-music-desktop ★53.8k / YesPlayMusic / Spotify 桌面 / foobar2k / 网易云桌面 / QQ 桌面 — 主流 10 段对数均布(31.25Hz-16kHz)±12 dB

## 用户拍板(3 项)
- **段数** = 10 段(主流同款)
- **频段** = 31.25 / 62.5 / 125 / 250 / 500 / 1k / 2k / 4k / 8k / 16k Hz
- **每段** = ±12 dB,step=0.5,Q=1.0
- **入口** = MusicView 主窗「🎚 EQ」抽屉 + LyricOnlyView #settings-panel 末尾 EqInline + 托盘「🎚 桌面 EQ」独立 BrowserWindow(360×420 透明)
- **默认关闭**(BiquadFilterNode.gain=0 等价无效果,首装用户不被默认声音打扰)
- **持久化** = 主进程 `userData/eq-state.json`(enabled/preset/gains 三字段)
- **6 预置**:平直/平直/vocal/bass/treble/rock/electronic(改任意段 → custom)

## 改动文件清单

**后端 2 改**:
- `prisiragent-shell/main.js` — `_eq_state_path/load/save` + EQ_PRESETS 字典 + `_setEqGain/_setEqPreset/_setEqEnabled/_resetEq` + `_notifyEqWindows` 广播 + 6 IPC handlers + `openEqWindow` 360×420 transparent BrowserWindow + 托盘双胞胎模板双改「🎚 桌面 EQ」submenu
- `prisiragent-shell/preload.js` — 暴露 6 EQ IPC + `onEqStateChanged` 订阅

**前端 8 新 3 改**:
- 新 `companion/static/music-vue/src/services/eq.ts`(~140 行)— EqEngine 单例(AudioContext + 10 BiquadFilterNode peaking + masterGain + bound flag 守护)
- 新 `companion/static/music-vue/src/stores/eq.ts`(~110 行)— Pinia 镜像 + bootstrap/applyRemote/setGain/applyPreset/setEnabled/reset/attachBroadcast
- 新 `companion/static/music-vue/src/components/EQPanel.vue`(~170 行)— 10 段 vertical slider + 6 预设 + 主开关 + reset
- 新 `companion/static/music-vue/src/components/EqInline.vue`(~140 行)— 歌词窗紧凑模式(mini slider 14×50px)
- 新 `companion/static/music-vue/src/views/EqWindowView.vue`(~70 行)— 独立 EQ BrowserWindow 视图 + 关闭按钮
- 新 `companion/static/music-vue/eq.html` + `companion/static/music-vue/src/eq.ts`(vite 多入口)
- 改 `companion/static/music-vue/src/services/player.ts`(+8 行)— `getAudioElement()` getter + onCanPlay 触发 `eqEngine.bind(this.audio) + resume()`
- 改 `companion/static/music-vue/src/views/MusicView.vue`(+25 行)—「🎚 EQ」按钮 + 抽屉容器(右下 360×320)
- 改 `companion/static/music-vue/src/views/LyricOnlyView.vue`(+3 行)— #settings-panel 末尾 `<EqInline />`
- 改 `companion/static/music-vue/src/stores/ui.ts`(+5 行)— `showEqPanel` + `toggleEqPanel`
- 改 `companion/static/music-vue/vite.config.ts`(+3 行)— eq entry
- 改 `companion/static/music-vue/src/types/music.ts`(+5 行)— `IEqState` 接口

**测试 1 新**:
- `tests/test_eq_p35.py`(68 测试)— 后端 state IO/preset/gain IPC/托盘双改 + 前端 EqEngine/Pinia 镜像/PlayerService bind + 组件 EQPanel/EqInline/EqWindowView + vite entry + MusicView 入口 + LyricOnlyView 集成 + 类型定义

## 关键设计点

### 1. Web Audio 链
```
audio ──source──bq[0..9]──masterGain──destination
                         31.25-16k Hz
                         peaking filter, Q=1.0
masterGain=enabled? 0.5 : 1.0    (防削顶 -6 dB)
```

### 2. MediaElementAudioSourceNode 单例约束
同一 HTMLAudioElement 只能 `createMediaElementSource()` 一次,二次会抛 InvalidStateError → EqEngine 单例 + `bound` flag 守护;PlayerService 通过 `getAudioElement()` 暴露 audio 引用,onCanPlay 时 lazy bind。

### 3. 多窗口状态同步
- 主进程 `_eq_state` 模块单例(userData JSON 持久化)
- `_notifyEqWindows(channel, payload)` 广播所有 BrowserWindow
- preload `onEqStateChanged` 订阅 → renderer store `attachBroadcast()` → 镜像到本地 + EqEngine.apply(实时音频)

### 4. AudioContext 用户手势限制
Chromium `new AudioContext()` 需 user gesture → PlayerService.onCanPlay 触发 `eqEngine.resume()`(用户已点歌 → 自然视为 gesture)。

### 5. 三入口统一
- **MusicView 抽屉** — 会话级 toggle,`ui.showEqPanel` Pinia ref(不持久化)
- **LyricOnlyView EqInline** — 嵌入 #settings-panel 末尾,compact 模式
- **托盘独立窗** — 持久化 BrowserWindow,360×420 transparent,可拖动

### 6. 双胞胎模板(P2.5+26 沿用)
`createTray` line ~941 + `buildTrayItems` line ~1240 都加「🎚 桌面 EQ」submenu + 「EQ 开启」checkbox(状态同步到 `_eq_state.enabled`)。

### 7. preset → custom 自动转换
落雪范式:用户拖任意段 → `_setEqGain` 把 `preset` 改 `"custom"`,store 同步,UI select 显示「自定义」。

## E2E 验证

- **pytest**:`python -m pytest tests/test_eq_p35.py` → **68/68 绿**(~0.5s)
- **回归**:`pytest test_eq_p35 test_toast_p310a test_music_lyric_p34 test_music_lyric_vue_p26 test_music_lyric_p31 test_music_lyric_p32 test_music_favorite_menu test_song_pool_and_favorite` → **290/290 绿**(~1.76s)
- **vite build**:`npx vite build` → 0 error,build 968ms;main bundle 25.49 KB(↑5KB,符合 EQPanel+EqInline+services/eq+stores/eq);3 entry(index/lyric/eq)
- **手动 E2E 计划**:托盘「🎚 桌面 EQ」→ 独立窗弹出 → 默认「⚫ EQ 关闭」+ 平直 0dB;主开关 → 10 段 slider 拖动;MusicView 抽屉同步;LyricOnlyView EqInline 嵌入;重启壳 → eq-state.json 保留;3 处任一改 → 另两处 mirror(主进程广播)

## 借鉴 / 复用

| 复用项 | 文件 | 用法 |
|---|---|---|
| `_lyric_state.json` IO | `main.js:352-407` | `_eq_state.json` 镜像 |
| `_notifyLyricWindow()` | `main.js:823` | `_notifyEqWindows(channel, payload)` 通用化 |
| `_clampNumber()` | `main.js:391` | EQ gain [-12,12] clamp |
| `ipcMain.handle` pattern | `main.js` P3.10a | 6 IPC handlers |
| 双胞胎模板 | `main.js` P2.5+26 | 托盘「🎚 桌面 EQ」双改 |
| `oiShell.showToast` | `preload.js` | EQ preset 切换提示(后续可选) |
| `rebuildTrayMenu` toggle | `main.js:993` | 「EQ 开启」checkbox 双改 |
| PlayerService 单例 | `services/player.ts` | getAudioElement 桥接 |
| #settings-panel | `LyricOnlyView.vue:169` | EqInline 嵌入末尾 |
| ui.pushToast | `stores/ui.ts:34` | EQ 改完反馈(后续可选) |

## 风险登记(已规避)

1. ✅ AudioContext 用户手势限制 → onCanPlay 触发 resume
2. ✅ MediaElementAudioSourceNode 单例 → bound flag 守护
3. ✅ masterGain 直通防削顶 → enabled=true 时 0.5,默认 preset 保守
4. ✅ 多窗口状态同步 → 主进程广播 + renderer subscribe
5. ✅ NTFS case-folding(双胞胎模板)→ `replace_all: true` 一次性双改
6. ✅ 重启保留 → userData/eq-state.json 落盘 + 启动时 _eq_state_load
7. ✅ preset → custom 自动转换(避免「我改了一档但 UI 还显示平直」的疑惑)
8. ✅ Vite 多入口 → eq entry 加进 rollupOptions.input
9. ⚠️ 暂无 AnalyserNode(频谱)→ P3.8 ship 时复用 EqEngine.ctx 加 analyser
10. ⚠️ 暂无 zipper noise 平滑(setTargetAtTime)→ P3.5 范围外,记入 backlog

## 后续(本 ship 外)

- **P3.6**:N8 下载完成 toast(0.5h)— P3.10a 基础设施 ship 后剩 1 行代码
- **P3.7**:N3 chip + N2 search(5h)
- **P3.8**:N10 频谱(4h)— 复用 EqEngine.AudioContext 加 AnalyserNode,10 段 spectrum bar
- **backlog**:preset 切换时 `biquads[i].gain.setTargetAtTime(target, ctx.currentTime, 0.02)` 平滑过渡消 zipper noise

## 与既有 ship 的关系

```
2026-10-04  P3.4 歌词窗单/双行 toggle ship (cf1095c)
2026-10-04  P3.10a desktop toast 弹卡 ship (4f16c3c)
2026-10-04  本计划   P3.5 10 段 EQ ship                    ← 本次 ship
```

11 项主流优化进度:**8/11 已 ship**(N6/N11/N5 全部 + N7 + v2 pool + N4),剩 N8/N3+N2/N10/N9(AI 歌单推荐)— P3.6 / P3.7 / P3.8 / 后续 P3.9。

## 关键 commit 信息

```
feat(music): 10 段 EQ 均衡器 + 6 预设 + Web Audio (P3.5)

- 后端 main.js: EQ_PRESETS + _eq_state IO + 5 helper + 6 IPC + openEqWindow(360×420) + 托盘「🎚 桌面 EQ」submenu 双改
- preload.js: 6 IPC 暴露 + onEqStateChanged 订阅
- services/eq.ts: EqEngine 单例 + AudioContext + 10 BiquadFilterNode peaking + masterGain 防削顶 + bound flag 守护
- stores/eq.ts: Pinia 镜像 + bootstrap/setGain/applyPreset/setEnabled/reset + attachBroadcast
- components/EQPanel.vue + EqInline.vue: 主面板 + 歌词窗紧凑版(10 vertical slider + 6 preset + 主开关 + reset)
- views/EqWindowView.vue + eq.html + src/eq.ts: 独立 EQ BrowserWindow 入口(vite 多入口)
- views/MusicView.vue:「🎚 EQ」按钮 + 右下抽屉容器
- views/LyricOnlyView.vue: #settings-panel 末尾嵌入 EqInline
- types/music.ts: IEqState 接口
- stores/ui.ts: showEqPanel + toggleEqPanel
- vite.config.ts: 加 eq entry
- 68 pytest 全绿 / 290 回归全绿 / vite build 0 error
```
