---
name: P2.5+25 music 桌面歌词独立窗 ship
description: 2026-10-03 桌面歌词独立窗(Vue 3 + Pinia LyricOnlyView + Electron 透明窗 + 双击关闭 + 托盘+MiniBar 双入口)
metadata:
  type: project
---

# P2.5+25 — 桌面歌词独立窗 ship(2026-10-03)

## 一句话
music 子窗 Vue 3 重写(P2.5+24)后,用户拍板「桌面歌词独立窗」+「托盘+MiniBar 双入口」+「双击关闭」,新写 Vue `LyricOnlyView` + Vite 多入口构建 + Electron 透明 BrowserWindow + preload `prisIragent.openLyric/closeLyric` IPC。

## 关键决策
- **前端**:新写 Vue `LyricOnlyView`(不复用旧 HTML/CSS/JS),与 music-vue 栈统一
- **触发**:托盘 `🎤 桌面歌词` + music MiniBar 🎤 按钮,两个都给(共享 `shell:openLyric` IPC)
- **关闭**:双击歌词窗 → LyricOnlyView `@dblclick` → `shell:closeLyric` IPC → `childWindows.get("lyric").close()`

## 文件清单

**新增 7 个**:
1. [lyric.html](companion/static/music-vue/lyric.html) — Vite 入口(~10 行,挂 `#app` + `/src/lyric.ts`)
2. [src/lyric.ts](companion/static/music-vue/src/lyric.ts) — 独立 Vue entry(mount `LyricOnlyView` + Pinia + bootstrap)
3. [src/views/LyricOnlyView.vue](companion/static/music-vue/src/views/LyricOnlyView.vue) — 主视图(顶部 6px drag-bar + `#lyrics-stage` 居中 + 双击关闭 + conn tag)
4. [src/stores/lyric.ts](companion/static/music-vue/src/stores/lyric.ts) — 独立 Pinia store(bootstrap + ws /ws/lyrics + 二分查找兜底 + cfg → CSS var)
5. [src/styles/lyric.css](companion/static/music-vue/src/styles/lyric.css) — 透明主题(`body { background: transparent !important }` + 国画色 #c14d3a 渐变 active)
6. [tests/test_music_lyric_vue.py](tests/test_music_lyric_vue.py) — 20 测试(structure + patterns + vite multi-entry + Electron spec + preload expose)
7. [memory/p2-5-25-music-lyric-vue-shipped.md](memory/p2-5-25-music-lyric-vue-shipped.md) — 本备忘

**修改 5 个**:
1. [vite.config.ts](companion/static/music-vue/vite.config.ts) — `rollupOptions.input` 加 `lyric:` 入口(多入口构建)
2. [src/components/MiniBar.vue](components/MiniBar.vue) — 加 🎤 按钮 + `onOpenLyric` handler(dev 模式优雅 toast 降级)
3. [prisiragent-shell/main.js](prisiragent-shell/main.js):
   - `_createChildWindow(spec)` 扩展 5 字段(`transparent`/`frame`/`alwaysOnTop`/`resizable`/`skipTaskbar`,默认 false / true 保持 4 子窗不变)
   - `_CHILD_SPEC.lyric` 新增(720×360 + transparent:true + frame:false + alwaysOnTop:true + skipTaskbar:true)
   - `openLyricWindow()` helper(镜像 `openMusicWindow` 端口轮询)
   - 托盘 `🎤 桌面歌词` 菜单项(在 📞 语伴 + 🎵 音乐之后,📅 日程之前)
   - IPC `shell:openLyric` + `shell:closeLyric` 白名单
4. [prisiragent-shell/preload.js](prisiragent-shell/preload.js) — `contextBridge.exposeInMainWorld("prisIragent", {openLyric, closeLyric})` 新前缀(独立 `oiShell` 主 web 前缀)
5. [memory/MEMORY.md](memory/MEMORY.md) — +1 行索引

## Vite 多入口构建产物

```
dist/index.html               0.68 kB
dist/lyric.html               1.00 kB
dist/assets/main-*.css       11.34 kB │ gzip:  2.76 kB
dist/assets/lyric-*.css       1.99 kB │ gzip:  0.85 kB
dist/assets/main-*.js        19.60 kB │ gzip:  6.83 kB
dist/assets/lyric-*.js        4.02 kB │ gzip:  1.85 kB
dist/assets/_plugin-vue_export-helper-*.js   70.10 kB │ gzip: 27.97 kB(共享)
```

`lyric` chunk 只 4 KB(独立 Pinia store + 歌词二分查找 + 4 行 view 模板);vue runtime 70 KB 共享。

## 测试结果

- `pytest tests/test_music_lyric_vue.py -v` → **20/20 绿**
- `pytest tests/test_music_vue_build.py tests/test_music_lyric_vue.py tests/test_music_multi_source.py tests/test_song_pool_and_favorite.py tests/test_electron_subwindows.py` → **101/102 绿**(1 个旧 music 静态资源死路测试是 commit `c5889c5` 之前的 pre-existing 失败,本 ship 不引入回归)

## E2E 验证(Puppeteer + curl)

```
curl http://127.0.0.1:14671/music-vue/lyric.html  → 200, title "PrisirAI 桌面歌词"
curl http://127.0.0.1:14671/api/agent/cfg/list     → 含 11 个 lyrics.* (color/font_size/mode/delay_ms/opacity/window_visible/window_x/y/w/h/font_family)
Puppeteer DOM check:
  - hasDragBar: true (6px 顶部拖动条)
  - hasStage: true (#lyrics-stage)
  - hasMeta: true (底部 meta)
  - hasConn: true (🟢/⌛ 连接状态)
  - bgBody: rgba(0,0,0,0) ← body transparent !important (Electron 透明窗关键)
  - empty 态: "(等待播放...)"
Puppeteer dblclick dispatch → "no error" (IPC 在 Electron 才有,Puppeteer 是普通浏览器,优雅无副作用)
```

截图存档:[p2-5-25-lyric-window.png](tests/screenshots/p2-5-25-lyric-window.png)

## 复用 / 借鉴点

| 复用 | 来源 | 用途 |
|---|---|---|
| `wsConnect('/ws/lyrics')` | music-vue `services/api.ts:50` | 新 lyric store 直接调,无需新客户端 |
| `_createChildWindow(spec)` 扩展 | `prisiragent-shell/main.js:357` | 加 5 字段即可,向后兼容 |
| `openMusicWindow` 端口轮询模式 | `prisiragent-shell/main.js:677` | `openLyricWindow` 完全镜像 |
| `ILyricLineMsg` / `IMusicItem` | `music-vue/src/types/music.ts` | lyric store 直接 import,无新类型 |
| 歌词二分查找 | `player.ts:107` | 复制到 lyric store(独立 store,不 import 跨 store) |
| CSS 变量 `--lyrics-color/font-size/opacity` | `companion/static/music/lyrics.css:7`(M3.29.3) | 旧设计验证过,直接移植 |
| `app.router.add_static("/music-vue/", ...)` | `prisIragent-music-web.py:787-789` | 自动 serve vite build,0 后端改动 |
| YesPlayMusic 行 active 高亮 + transform | `utils/lyric.js` | .line.active scale 1.06 + 渐变 |

## 与既有 ship 的关系

```
2026-09-19  M3.29.3     桌面歌词 HTML/CSS/JS + /lyrics 路由(为 Tauri 透明窗)
2026-10-03  P2.5+24     music 子窗 Vue 3 + Pinia 重写(commit c5889c5)
2026-10-03  本 ship      桌面歌词独立窗 — Vue LyricOnlyView + Electron 透明窗 + 双击关闭
```

## 风险登记(已规避)

1. **Vite 多入口构建失败** — `rollupOptions.input` 配置后 `npm run build` 必跑 ✓ 通过(839ms)
2. **Electron `transparent: true` 在 Win 性能差** — 720×360 透明窗 GPU 合成开销轻微,可接受
3. **`-webkit-app-region: drag` 在 Electron transparent 失效** — 用 CSS 原生属性,Win 实测可拖
4. **ws /ws/lyrics 推送延迟** — 前端 `computeCurrentIdxByTime` 二分查找兜底(`player.ts:107` 移植)
5. **`alwaysOnTop: true` 遮挡浏览器等应用** — 默认开(歌词场景用户期望),后续 P2.5+26 加可关菜单项
6. **MiniBar 🎤 在 dev 模式无效** — try/catch + toast 优雅降级

## 下一步

- **P2.5+26**(可选):歌词窗 alwaysOnTop/lockout 菜单(用户可选关 alwaysOnTop,锁/解锁拖动)
- **P2.5+27**(可选):歌词窗 CSS 主题切换(故宫/国色/水墨)
- **P2.5+28**(可选):click-through 模式(歌词行不抢鼠标焦点,适合鼠标操作密集时)
- 后续 Phase 2 iTunes 封面、Phase 3 Lx 真实源接入与本 ship 无关,独立排期

**How to apply:** 桌面歌词独立窗是音乐栈全栈 Vue 化的最后一块;之后任何 music 子窗新功能(如可视化 / 频谱 / MV 模式)都应走 Vue 新栈,不再扩旧 HTML/CSS/JS。