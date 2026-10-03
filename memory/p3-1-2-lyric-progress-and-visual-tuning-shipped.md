---
name: p3-1-2-lyric-progress-and-visual-tuning-shipped
description: P3.1 + P3.2 — 桌面歌词窗进度条拖动跳转 + 透明度/字号缩放视觉调档
metadata:
  type: project
---

P3.1 + P3.2 是 P2.5+26(歌词窗 alwaysOnTop/lockDrag)之后的两个歌词窗体验 ship。两项合并 commit,因为都改同一视图组件 + 同一 IPC namespace。

## 决策背景

P2.5+25 ship 桌面歌词独立窗后,用户提了 11 项主流播放器优化。Phase 1 全接 4 项:
- N6 歌词进度条可拖动(0.5h) → P3.1
- N11 歌词窗可锁定尺寸/透明度(2h) → P3.2
- N7 收藏按钮 ♥/♡ 改进(2h) → 后续
- N4 歌词单行/双行/翻译行 toggle(2h) → 后续

P3.2 简化:不做尺寸,只做透明度 + 字号缩放(主进度条拖动行更短 + 视觉对比更直接)。

## P3.1 歌词进度条拖动跳转

### 实现
- **lyric store**(`stores/lyric.ts`)加 `seek(offsetSec)` + `seekPct(p)` actions:
  - 走 `POST /api/cmd {action:'seek', offset:秒}`(后端 player.py 已支持 seek 命令)
  - 节流:`seekInFlight` flag 防高频
  - 乐观更新:`progress.value = target` + `--progress` CSS var 立即生效,不等后端
  - clamp:`Math.max(0, Math.min(duration, target))`
- **新组件**`LyricProgressBar.vue`(独立组件,不绑 player store):
  - 接受 props `current/duration`,emit `'seek'`(offset 秒)
  - 借鉴 Vue-mmPlayer 三层:lp-bg / lp-active / lp-thumb
  - dragging 模式:down 即时视觉,up 才 emit seek
  - 2px 紧凑,hover 4px;`-webkit-app-region: no-drag` 不抢顶部拖动条
- **LyricOnlyView** 集成:`#progress-zone` fixed bottom:22px(在元数据条之上)

### 复用旧 ProgressBar.vue 的经验
- 同样的 props/emit pattern
- 同样的三层布局
- 同样的 dragging 模式(mousedown + document mousemove/up)
- 区别:不绑 store,歌词窗独立控制(lyric store 不知道 player store)

## P3.2 视觉调档(透明度 + 字号缩放)

### 状态机扩展
`_lyric_state` 加 2 字段(落盘 userData/lyric-window-state.json):
```json
{
  "alwaysOnTop": true,
  "lockDrag": false,
  "bounds": { "x": null, "y": null, "w": 720, "h": 360 },
  "opacity": 0.85,    // P3.1 新增:0.3-1.0
  "scale": 1.0         // P3.1 新增:0.7-1.6
}
```

### 主进程
- `_clampNumber(v, lo, hi, dflt)` helper:
  - `const n = Number(v); if (!Number.isFinite(n)) return dflt; return Math.max(lo, Math.min(hi, n))`
  - 容错:NaN/Infinity/字符串/null → dflt
- `_lyric_state_load` 加载时用 `_clampNumber` 兜底
- 新 IPC:
  - `shell:setLyricOpacity(value)` → clamp [0.3, 1.0] + save + 推 `shell:lyricStateChanged` payload
  - `shell:setLyricScale(value)` → clamp [0.7, 1.6] + save + 推 `shell:lyricStateChanged` payload
- `shell:getLyricState` 返回值加 `opacity` + `scale` 字段(bootstrap 拿初始值)

### preload.js
- `prisIragent.setLyricOpacity(v)` + `setLyricScale(v)` invoke

### LyricOnlyView
- 加 `opacity = ref(0.85)` + `scale = ref(1.0)`
- bootstrap `getLyricState` 读 s.opacity/s.scale → 赋 ref
- `onLyricStateChanged` 回调处理 payload.opacity/payload.scale
- `#settings-panel`(顶部居中,默认 opacity:0.25 半透明,hover 露 1.0):
  - 2 个 `setting-row`(opacity 30-100,scale 70-160,×100 是百分比显示)
  - `@input` 即时改 ref + 调 IPC setLyricOpacity/Scale

### lyric.css
- `:root` 加 `--lyric-window-opacity: 0.85` + `--lyric-window-scale: 1.0`
- `html, body` 应用 `opacity: var(--lyric-window-opacity)` + `transform: scale(var(--lyric-window-scale))` + `transform-origin: center center`
- body 加 `transition: opacity 180ms ease`(滑杆瞬变刺眼)

### 设计权衡
- 不做尺寸锁定(P3.2 最初范围)— 跟 P2.5+26 bounds 冲突(用户可拖动 / 滑块缩窗双轨)
- 不做 click-through(P2.5+28 待办)— 与 opacity/scale 是不同设计空间
- 透明度只对 html/body 整体(opacity),不对 settings-panel/settings-row(setting-row 始终可见)

## 测试覆盖

### test_music_lyric_p31.py(19 tests, 3 classes)
- TestLyricStoreSeek:8 tests(seek 函数 / clamp / 节流 / optimistic update / seekPct / 导出)
- TestLyricProgressBar:7 tests(文件 / defineProps / defineEmits / 三层 / dragging 状态 / no-drag / 不绑 store)
- TestLyricOnlyViewProgressBar:4 tests(import / onSeek / #progress-zone / no-drag)

### test_music_lyric_p32.py(36 tests, 6 classes)
- TestLyricStateSchema:4 tests(_lyric_state_load 兜底字段)
- TestLyricStateClamp:4 tests(_clampNumber 边界)
- TestLyricIPCOpacityScale:9 tests(2 handler 存在 / clamp / 持久化 / 推 / 返回)
- TestLyricPreloadP32:2 tests(2 preload 暴露)
- TestLyricOnlyViewSettings:9 tests(opacity/scale ref / bootstrap / push handler / 2 滑杆 UI / min-max)
- TestLyricCssWindowVars:7 tests(:root vars / 默认值 / body 应用 / transition)

### 总计
- P2.5+26:31 tests
- P3.1:19 tests
- P3.2:36 tests
- 合计 86/86 全绿(vite build OK,lyric chunk +0.27KB)

## E2E 验证步骤

1. **起 Electron 壳**:npm start 或装包启动
2. **托盘右键 → 🎤 桌面歌词 → 打开歌词窗口**
3. **播放歌曲**(music 子窗选歌 + Play)
4. **进度条**:
   - 歌词窗底部 2px 进度条可见
   - 鼠标 hover 进度条变 4px + 显示 thumb
   - 点击 → 跳到对应时间
   - 拖动 → 实时显示,松手才发 seek
5. **视觉调档**:
   - 鼠标 hover 歌词窗 → 顶部出现 settings-panel(透明度 30-100% + 字号 70-160%)
   - 拖透明度滑杆 → 歌词窗整窗透明度实时变 + userData 落盘
   - 拖字号滑杆 → 歌词字缩放(注意:active line scale 1.06 内部叠加)
   - 关闭窗 → 重启壳 → 重开 → 状态还原

## 与既有 ship 的关系

```
2026-10-03  P2.5+25  桌面歌词独立窗 ship(commit c0085a2)
2026-10-03  P2.5+26  alwaysOnTop/lockDrag + 状态持久化(commit 5a4d9ab)
2026-10-03  P3.1     进度条拖动跳转  ←  本次 ship 部分 1
2026-10-03  P3.2     透明度+字号缩放  ←  本次 ship 部分 2
```

后续(本 ship 外):
- **P3.3** N7 收藏长按菜单(2h)
- **P3.4** N4 歌词单行/双行/翻译 toggle(2h)
- **P3.5** N5 EQ(4h)— BiquadFilter
- **P3.6** N8 下载完成 toast(3h)
- **P3.7** N3 chip + N2 search(5h)
- **P3.8** N10 频谱(4h)— AnalyserNode + Canvas

## 关键文件

| 文件 | 行数 | 改动 |
|------|------|------|
| `companion/static/music-vue/src/stores/lyric.ts` | +30 | seek/seekPct actions |
| `companion/static/music-vue/src/components/LyricProgressBar.vue` | NEW 129 | 进度条独立组件 |
| `companion/static/music-vue/src/views/LyricOnlyView.vue` | +90 | 集成 progress + opacity/scale refs + settings panel |
| `companion/static/music-vue/src/styles/lyric.css` | +20 | --lyric-window-* vars + body 应用 |
| `prisiragent-shell/main.js` | +35 | _clampNumber + setLyricOpacity/Scale IPC + _lyric_state 兜底 |
| `prisiragent-shell/preload.js` | +2 | setLyricOpacity/Scale 暴露 |
| `tests/test_music_lyric_p31.py` | NEW 194 | P3.1 测试 |
| `tests/test_music_lyric_p32.py` | NEW 360 | P3.2 测试 |

总计 6 改 2 新,合并 1 commit)。