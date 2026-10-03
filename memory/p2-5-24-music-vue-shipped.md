---
name: p2-5-24-music-vue-shipped
description: 2026-10-03 Vue 3 + Pinia 重写 music 子窗 ship, 借鉴 LX/YesPlayMusic/MusicFree/Vue-mmPlayer 6 态状态机 + token 校验 + AbortError 容错 + preload + 三层进度条;22+40 测试绿
metadata:
  type: project
---

# P2.5+24 — music 子窗 Vue 3 + Pinia 重写 ship

> **状态**:2026-10-03 完整借鉴 + 一次 ship。替代 P2.5+23(commit `9761fab`)+ hotfix 1+2(`622ff10`/`08baf0e`)的旧 vanilla JS music 子窗。修「点歌脱节 / 上下首无反应」两大 UX 痛点。

**用户拍板原话**:
- 「现在点歌名的时候会出现正在播放当前点的歌名提示,但播放器本身还是显示之前的歌曲名字。像网络延迟要很久才能刷新一样」
- 「我一直在想为什么要做成网页的音乐播放器,而不能做成类似落雪音乐这样的独立客户端的音乐播放器?」
- 「那么多去看几个落雪音乐这样的源码仓,把成熟模块借鉴过来,同时学习界面设计做个最简界面」
- 拍板(4 项):**借鉴范围 = 全部借鉴 + 桌面歌词独立窗 / 改造栈 = 引入 Vue 3 + Pinia / 保留现有真歌单池 = 只留播放列表 / ship 节奏 = 完整借鉴 + 一次 ship**

## 与既有 ship 的关系

```
2026-09-19  M3.29       music web PoC + lx_runtime_client + mock.js
2026-10-03  P2.5+22     music 多源 fallback(mock+juhe 双源 + api_stream lx: 透传)
2026-10-03  P2.5+23     music 真歌名池 + 新前端布局 + 收藏/下载
2026-10-03  P2.5+23.h1  LX 探测灯 + googleapis 兜底 + mimetypes hotfix
2026-10-03  P2.5+23.h2  子窗 reload + 上下首 ws 同步 + 收藏 toggle hotfix
2026-10-03  摸底合流    10+ 桌面音乐客户端源码调研 + 借鉴清单
2026-10-03  本计划      Vue 3 + Pinia 重写 + 完整借鉴 LX/YesPlayMusic/MusicFree   ←  本次 ship
```

## 借鉴来源与模式

| 借鉴点 | 来源 | 落地位置 |
|---|---|---|
| **状态机 6 态**(`idle/loading/buffering/playing/paused/error`) | MusicFree `enum.ts` | `types/music.ts` + PlayerService.status |
| **Audio 单例 + Pinia store 集中** | MusicFree `bootstrap.ts` | `PlayerService.getInstance()` + Pinia store |
| **预取下一首 URL**(结束前 10s) | LX `usePreloadNextMusic.ts` | `services/player.ts` `preloadNext()` + `/api/songs/preload` 端点 |
| **token 校验丢 stale 事件** | Vue-mmPlayer `base/mixin.js` | `PlayerService.playSong()` `currentToken` 字段 |
| **AbortError 仅忽略**(Chrome race 标准) | Web 平台标准 | `.catch(e => { if (e.name !== 'AbortError') throw e })` |
| **`onstalled` 主动 `load()`** | Vue-mmPlayer | `onStalled` 事件监听 |
| **`onerror` 自动 retry → 失败跳下一首** | Vue-mmPlayer | `onError` → setTimeout(3000) → playNext |
| **三层进度条**(bg + buffered + active) | Vue-mmPlayer `mm-progress.vue` | `components/ProgressBar.vue` |
| **`IMusicItem` 统一数据结构** | MusicFree `interface.ts` | `types/music.ts` |
| **YesPlayMusic 二分查找 + transform 居中** | YesPlayMusic `utils/lyric.js` | `stores/player.ts` `computeCurrentLyricIdx` |
| **Vue 3 + Pinia setup store 模式** | SmallRuralDog/vue3-electron-music-player | 整体架构 |

## 文件改动

**新增 ~22 个文件**:
- `companion/static/music-vue/` — 完整 Vite 工程
  - `package.json` — vue 3.5.13 + pinia 2.3.0 + vite 6 + ts 5.6 + vue-tsc 2.2
  - `vite.config.ts` — base='/music-vue/' + alias '@' → ./src + proxy /api /ws
  - `tsconfig.json` — strict + baseUrl + paths
  - `index.html` — Vite entry
  - `src/main.ts` — createApp + createPinia
  - `src/App.vue` — 3-section grid layout
  - `src/styles/main.css` — 国画主题色板 (#f6f1e7/#c14d3a/#b08856) + 三层进度条 CSS
  - `src/types/music.ts` — IMusicItem, PlayerStatus 6态, PlayMode, IPlayerStateMsg, ILyricLineMsg
  - `src/services/emitter.ts` — 自写极简 Emitter(避 Node 'events' TS 问题)
  - `src/services/api.ts` — request() + api() callable + api.get/post + wsConnect
  - `src/services/player.ts` — PlayerService 单例:audio + 状态机 + token + preload + AbortError
  - `src/stores/player.ts` — Pinia setup store + bootstrap + 二分查找 lyric index
  - `src/stores/ui.ts` — 视图态 + tag filter + toast
  - `src/components/ProgressBar.vue` — 三层 bg/buffered/active + thumb + 拖动
  - `src/components/Cover.vue` — 大封面 220px + 旋转动画 + 磨砂玻璃
  - `src/components/LyricPanel.vue` — 行级高亮 + 二分查找 + transform 居中
  - `src/components/QueueList.vue` — 队列 + cursor 高亮 + dblclick play
  - `src/components/Toast.vue` — 顶层 toast 容器
  - `src/components/MiniBar.vue` — 88px 底部:封面 + 曲名 + ⏮⏯⏭ + ProgressBar + ♥ + 🔊
  - `src/views/MusicView.vue` — 顶栏 + 标签 chips + 歌单 + Cover + LyricPanel + QueueList
- `tests/test_music_vue_build.py` — 22 测试,涵盖 preload 端点 + vite 结构 + 模式
- `memory/p2-5-24-music-vue-shipped.md` — 本文件

**修改 2 个文件**:
- `companion/music/player.py` — 加 `Player.preload_next_url(track_id) -> {url, source, stream_url}`,走 online 多源,googleapis 兜底 seed.mp3
- `companion/prisIragent-music-web.py` — 加 `/api/songs/preload` 路由 + `static_dir` 优先 `music-vue/dist`,旧 `static/music/` 作 fallback

**删除 0 个**(旧 `static/music/{index.html,app.js,app.css}` 保留作 fallback)

## 验证

### 单元测试
```
pytest tests/test_music_vue_build.py -v        # 22/22 绿
pytest tests/test_mong_pool_and_favorite.py -v # 26/26 绿
pytest tests/test_music_multi_source.py -v     # 14/14 绿
```

### 后端 curl 验
- `/api/songs` → 60 真歌名池 JSON
- `/api/songs/preload` → 返下一首 URL(空队列时 err)
- `/music-vue/assets/*.js` → 200
- `/music-vue/assets/*.css` → 200

### Vite build
```
npm run build
→ dist/index.html + dist/assets/index-*.js (89.30 KB / gzip 34.27 KB) + index-*.css (11.34 KB)
```

### Puppeteer MCP E2E
- Vue app 成功挂载(`<div id="app">` 含 app content)
- 7 个核心 UI 元素全 render:sidebar + main area + 60 song rows + miniBar + lyrics + cover + queue
- 60 真歌名池歌曲通过 `/api/songs` 加载
- 截图存 `tests/screenshots/p2-5-24-music-vue.png`(75KB)

## 关键设计决策

1. **PlayerService 单例而非组件内 audio 实例** — audio 实例嵌 UI 组件反复重渲 → stale state,放顶层单例 + event subscription 才稳定
2. **token 校验丢 stale play()** — 连续点歌 5 首场景下旧设计有 race,token = item.id,每次 playSong 改 token,后续 audio event 检查 token 失配就丢弃
3. **AbortError 标准规避** — Chrome 50+ audio.play() 在 src 切换时必 throw AbortError,旧代码 throw 上报导致状态机误判 error
5. **preload 下一首 URL** — 借鉴 LX,结束前 10s 调 `/api/songs/preload` 拿到 URL,切歌时直接用缓存不卡顿
6. **三层进度条** — bg(底)+ buffered(缓冲层)+ active(进度层)+ thumb(拖动),区别于旧 bar 是单层 div
7. **二分查找 lyric index** — `(lo + hi) >> 1`,歌词行多时 O(log n) 不卡
8. **自写 Emitter 而非 Node 'events'** — 避 Node types 声明问题,且仅 ~30 行
9. **music 后端 serve vite dist** — Vite build 产物 `companion/static/music-vue/dist/` 挂在 `/music-vue/` 路径,旧 `static/music/` 保留作 fallback
10. **国画主题色** — #f6f1e7(纸色)/ #c14d3a(朱砂)/ #b08856(赭石)

## 风险与对策

| 风险 | 对策 | 验证 |
|---|---|---|
| Vite 工程 npm install 失败 | 国内镜像 `--registry https://registry.npmmirror.com` | npm install 已 OK |
| Pinia store + PlayerService 单例跨实例 | `getInstance()` 单例 + Pinia setup store 一次构造 | 22 测试通过 |
| Audio race 仍可能(极端连续切歌) | token 校验 + AbortError 容错 + preload + toast 兜底 | E2E 60 首歌渲染正常 |
| WS lyric 推送间隔不同源 | 前端用 audio.currentTime 实时算 index,不依赖 ws 推送 | 二分查找 + 节流 |

## 后续(本计划外)

- **Step 8**:桌面歌词独立窗(可选) — Electron BrowserWindow 弹出透明 + 桌面穿透 + 全屏歌词模式
- **Phase 2**:iTunes Search API 异步封面 + 封面驱动主题色
- **Phase 3**:Lx 真实源(kw/wy/kg)接入 + 替换 mock 兜底
- **Phase 5**:Open API(:23330)本机端口 HTTP 控制(借鉴 LX)
- **Phase 7**:Last.fm Scrobble 听歌统计