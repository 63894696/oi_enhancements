---
name: p3-4-music-lyric-lines-toggle-shipped
description: P3.4(2026-10-04)歌词窗单/双行 toggle — ☝ 单行 / ☟ 双行,默认单行紧凑
metadata:
  type: project
---

# P3.4 — music N4 歌词窗单/双行 toggle ship(2026-10-04)

> 11 项主流播放器优化的第 4 项:歌词窗单/双行 toggle。
> 紧接 P3.1+P3.2(进度条 + 视觉调档,dc3b83a)、P3.9(song_pool v2,6a00d52)、P3.3(♡/♥ 长按菜单,982c327)。

## 拍板决定

- **双行语义** = 单行=仅 active,双行=active + 下一行(预览)。落雪/Spotify 流派,不取 AIMP/网易云的「上/下小字」上下流派。
- **入口** = 设置面板 #settings-panel 末尾新增「☝ 单行 / ☟ 双行」seg-btn 双按钮组(复用 P3.2 hover 露面板)+ 托盘子菜单 2 个 radio(☝/☟,group:lyricLines,Electron Menu 互斥单选对)。
- **持久化** = 复用 P2.5+26 已 ship 的 `_lyric_state` JSON(opacity/scale 已有),新增 `lines: 1 | 2` 字段,默认 1。
- **重启行为** = 保留上次选择。
- **P2.5+26 经验** = checkbox/radio toggle 后必 `rebuildTrayMenu()`,否则旧态永显。

## 改动清单

### 后端
1. `prisiragent-shell/main.js`(~30 行)
   - `_lyric_state_load` 加 `lines: (Number(obj.lines) === 2) ? 2 : 1`(容错越界兜底 1)
   - defaults 段加 `lines: 1`
   - `shell:getLyricState` 返回值加 `lines: _lyric_state.lines`(向后兼容,老渲染层读到 undefined 容错)
   - 新增 `_setLyricLines(value)` helper:容错 → 写盘 → `_notifyLyricWindow` 推 → `rebuildTrayMenu` 同步 radio
   - 新增 `ipcMain.handle("shell:setLyricLines", ...)` 返 `{ok:true, lines}`
   - 托盘子菜单 multiWindowSubmenu(line 928 区域)+ buildTrayItems(line 1015 区域)双模板双改:☝ 单行 / ☟ 双行 radio + group:"lyricLines"

2. `prisiragent-shell/preload.js`(+2 行)
   - 加 `setLyricLines: (v) => ipcRenderer.invoke("shell:setLyricLines", v)`
   - `onLyricStateChanged` payload 加 `lines` 字段(无需改 preload 暴露,老订阅自动透传)

### 前端(Vue 3)
3. `companion/static/music-vue/src/components/LyricLinesToggle.vue`(新,75 行)
   - props `value: 1 | 2` + emits `change`
   - 2 button(active 绑 value===1/2,国画红 #c14d3a)
   - 调 `prisIragent.setLyricLines(v)` IPC,失败降级本地 emit
   - 错误显示 `lastErr`

4. `companion/static/music-vue/src/views/LyricOnlyView.vue`(~20 行)
   - import LyricLinesToggle
   - `const lines = ref<1 | 2>(1)`
   - bootstrap getLyricState 读 `s.lines`(=== 2/=== 1 三元容错)
   - onLyricStateChanged 订阅 `payload.lines`
   - 根 `:class` 双绑 `'lyric-lines-1': lines === 1, 'lyric-lines-2': lines === 2`
   - `#settings-panel` 末尾加 `<LyricLinesToggle :value="lines" />`

5. `companion/static/music-vue/src/styles/lyric.css`(~15 行)
   ```css
   .lyric-only-view.lyric-lines-1 #lyrics-stage .line.next { display: none; }
   .lyric-only-view.lyric-lines-2 #lyrics-stage .line.next {
     display: block; opacity: 0.55; font-weight: 400;
     background: none; color: var(--lyrics-color);
     -webkit-text-fill-color: var(--lyrics-color);
     -webkit-text-stroke: 0.5px var(--lyrics-stroke);
     transform: scale(1.0);
   }
   ```
   关键:双行模式覆盖 .line.next 默认 `opacity: 0.55` 之外的所有 active 视觉(无 scale、无国画红渐变、无 text-fill-color:transparent),保持「下一句」小字低调。

### 测试
6. `tests/test_music_lyric_p34.py`(新,290 行,33 测试 9 类)
   - TestLyricStateLinesField(4)— _lyric_state_load 加 lines 字段 + 默认 1 + accept 2 + 越界兜底 1
   - TestLyricSetLinesHelper(5)— helper 存在 + 容错到 1|2 + 调 _lyric_state_save + _notifyLyricWindow + rebuildTrayMenu
   - TestLyricSetLinesIPC(2)— handler 注册 + 返 `{ok:true, lines}`
   - TestGetLyricStateReturnsLines(1)— getLyricState 返回值加 lines
   - TestTrayLyricLinesRadio(4)— 单行/双行 radio + 双模板双改 + 用 helper
   - TestPreloadSetLyricLines(1)— preload 暴露 setLyricLines
   - TestLyricLinesToggleComponent(8)— 文件存在 + props + 2 buttons + active class + IPC + emit + dev 降级 + defineProps/defineEmits
   - TestLyricOnlyViewLinesClass(6)— import + ref + bootstrap + 订阅 + 根 class 双绑 + 挂载组件
   - TestLyricCssLinesMode(2)— 单行 display:none + 双行 display:block

## E2E 验证(puppeteer + vite dist 静态预览)

| 模式 | 根 class | .line.next display | opacity | offsetHeight > 0 |
|---|---|---|---|---|
| 单行 | `lyric-only-view lyric-locked lyric-lines-1` | `none` | 0.55 | **false** |
| 双行 | `lyric-only-view lyric-locked lyric-lines-2` | `block` | 0.55 | **true** |

CSS 切换行为完全符合设计。

## 复用既有实现(无重新发明)

| 复用项 | 文件 | 用法 |
|---|---|---|
| `_lyric_state` JSON IO | main.js:352-403 | P3.4 lines 字段直接 add,与 P3.2 opacity/scale 同构 |
| `_notifyLyricWindow` helper | main.js 现有 | 推 shell:lyricStateChanged 加 lines 字段 |
| `shell:getLyricState` IPC | main.js:1077 区域 | 返回值加 `lines` 字段 |
| `onLyricStateChanged` 订阅 | preload.js:39 | payload 加 lines 字段,无需改 preload 暴露 |
| `rebuildTrayMenu` | main.js:967 | P2.5+26 经验,radio toggle 后必调 |
| `#settings-panel` 容器 | LyricOnlyView.vue:156-167 | 复用 P3.2 已 ship 的 hover 露面板 |
| `.setting-row` 样式 | LyricOnlyView.vue:208-216 | label 字号 10px + mono font,新组件遵循 |
| `lyric.css` 的 `.line.next` | lyric.css:120 | 已有 `.line.next {opacity:0.55}`,双行时复用 |

## 风险登记

1. **CSS class hot reload** — vue-tsc 0 error + vite build 必跑 ✅
2. **Electron Menu radio group** — radio 类型必须有 group 字段才互斥 ✅(已加 `group: "lyricLines"`)
3. **托盘 rebuild race** — P2.5+26 模式已 ship(`rebuildTrayMenu` 句柄 + try/catch)✅
4. **`getLyricState` 返回字段加 lines** — P3.1+P3.2 测试断言具体字段集 ✅(P3.4 测试通过)
5. **末 1 行无下一行** — `.line.next` 若无 element 即不显示,P3.4 不变,优雅降级 ✅

## 测试统计

- **P3.4 单测**:33/33 绿(0.52s)
- **全栈回归**(P3.4 + P3.3 + P3.9 + P2.5+26 + P3.1 + P3.2 + favorite menu):189/189 绿(1.60s)
- **vite build**:vue-tsc 0 error + 961ms
- **lyric.js**:8.66 KB(+1.13 KB LyricLinesToggle)
- **lyric.css**:5.38 KB(+1.10 KB lines mode)

## Rollback

1 commit 回滚即可:
- `_lyric_state` 多 1 字段 → 旧 lyric_state.json 无 lines 字段,`_lyric_state_load` 容错返 1,无副作用
- LyricLinesToggle.vue 删 → LyricOnlyView 不挂载,根 class 永远 `lyric-lines-1`(因 lines=1 默认),显示等价 ship 前
- lyric.css 双行模式不进 → 单行行为不变
- 托盘菜单 radio 不加 → 旧 4 项子菜单保留

## 与既有 ship 的关系

```
2026-10-03  P2.5+25  桌面歌词独立窗 ship
2026-10-03  P2.5+26  alwaysOnTop/lockDrag + 状态持久化
2026-10-03  P3.1+P3.2 歌词窗进度条拖动 + 视觉调档 ship (dc3b83a)
2026-10-03  P3.9 song_pool v2 接入 ship (6a00d52)
2026-10-03  P3.3 ♡/♥ 长按 4 项菜单 ship (982c327)
2026-10-04  本 ship   P3.4 歌词窗单/双行 toggle ship      ← 本次
```

后续(本 ship 外):
- **P3.5** — N5 EQ(4h)
- **P3.6** — N8 下载完成 toast(3h)
- **P3.7** — N3 chip + N2 search(5h)
- **P3.8** — N10 频谱(4h)

## 关键 CSS 经验(下次做类似 toggle 时复用)

**模式 = 根 :class 双绑 + 后代选择器控制**:
```vue
<div :class="{ 'lyric-lines-1': lines===1, 'lyric-lines-2': lines===2 }">
```
```css
.mode-a .child { display: none; }
.mode-b .child { display: block; }
```
好处:
- 一个 ref 控多个后代,无需每个后代单独 :class
- CSS 可读性高(后代选择器一眼看懂)
- 不破坏 Vue 组件 props 透传(根元素继续接 @dblclick 等)

**Why**:「下一行」在 lyric 场景有「隐藏 / 显示 + 小字预览」两种语义,直接 `:class="next: ..."` 在 v-for 内每个 .line 都加会污染 DOM 树并增加 Vue 重渲染开销。根 toggle 一次到位。

**How to apply**:下次 P3.5 EQ(可视化频段 vs 隐藏频段)、P3.7 歌曲卡片 chip(search 模式 + chip 模式)都可套同一范式。
