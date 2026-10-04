---
name: p3-6-music-download-toast-shipped
description: P3.6(2026-10-04)music N8 下载完成桌面通知 toast ship — 后端 publish download_done → ws 广播 → useDownloadToast 桥接 → prisIragent.showToast
metadata:
  type: project
---

# P3.6 music N8 下载完成桌面通知 toast ship(2026-10-04)

## Context

**用户原始诉求**:「继续做P3.6 N8 下载 toast」(P3.5 ship c9073a7 之后的下一项)
**位置**:11 项主流播放器优化的第 8 项 N8「下载完成桌面通知」
**前置 ship**:P3.10a desktop toast 弹卡基础设施(4f16c3c)
**调研**:落雪 lx-music-desktop「下载完成桌面通知」/ Spotify「下载完成通知」/ 网易云桌面「下载完成 toast」 — 都是 ws/event 推 → OS Notification

## 用户拍板(3 项)
- **Toast 范围** = 所有打开的 music 窗(MusicView + LyricOnlyView + 独立 EQ 窗),借 ws_state 广播
- **下载入口** = MusicView 歌单行右键 PopupMenu(复制/播放/收藏/⬇ 下载/取消)+ LyricOnlyView settings-panel 「⬇ 下载当前歌曲」按钮
- **错误处理** = 成功 level='info'(5s 节流 P3.10a ship),失败 level='error'(critical urgency + 不节流)

## 改动文件清单

**后端 2 改**:
- `companion/music/player.py:_cmd_download` (+12 行)— 7 处 publish:missing-track / not-found / mkdir-fail / aiohttp-unavailable / upstream-error / source-missing / exception / success;每个 ok/err 分支都 `_publish('download_done', payload)`
- `companion/prisIragent-music-web.py:api_cmd` (+10 行)— action=='download' 时 `_publish_state('download_done', payload)` 透传到 web 层 ws_state_subs

**前端 4 改 1 新**:
- 新 `companion/static/music-vue/src/composables/useDownloadToast.ts`(~80 行)— onMounted 订阅 svc 'download' 事件 + onBeforeUnmount 退订 + window.prisIragent.showToast 桥接 + ok 选 level + formatSize helper + 防御性 console.warn(missing IPC)
- 改 `companion/static/music-vue/src/stores/player.ts`(+38 行)— onWsStateMsg 加 download_done 分支 → svc.emit('download', msg);新 downloadSong(trackId) action 调 /api/cmd;return 块加 downloadSong
- 改 `companion/static/music-vue/src/views/MusicView.vue`(+62 行)— useDownloadToast() 顶层;歌单行 @contextmenu.prevent + openCtx(s, $event);ctxItemsFor 5 项(复制/播放/收藏/⬇ 下载/取消);onCtxSelect 调 player.downloadSong(s.id);PopupMenu 挂模板
- 改 `companion/static/music-vue/src/views/LyricOnlyView.vue`(+30 行)— useDownloadToast() 顶层;onDownloadCurrent() 调 player.downloadSong(lyric.track?.id);#settings-panel 末尾 btn-download 红边按钮;btn-download:disabled 半透明

**测试 1 新**:
- `tests/test_download_toast_p36.py`(19 测试)— 6 类:TestCmdDownloadPublish(3)+ TestApiCmdDownloadPublish(3)+ TestPlayerStoreDownloadSong(3)+ TestUseDownloadToast(4)+ TestMusicViewDownloadEntry(4)+ TestLyricOnlyViewDownloadEntry(2)

## 关键设计点

### 1. 双层 publish 双保险
```
player._cmd_download
    ↓ self._publish('download_done', payload)        ← 内部 asyncio.Queue
    ↓ player._subscribers (内部 subs)
prisIragent-music-web.py api_cmd
    ↓ _publish_state('download_done', payload)       ← web 层 ws_state_subs
    ↓ 所有 /ws/state 客户端收到
```
两条通道**不重复**:player._subscribers 与 web.ws_state_subs 是两套独立队列;renderer 是 web 层 ws 客户端,只走第二条。web 层 api_cmd 末尾必须显式 publish_state,否则 renderer 收不到。

### 2. ws → PlayerService 单例 → composable 三跳
```
wsState message(type=download_done)
    ↓ stores/player.ts onWsStateMsg
    ↓ svc.emit('download', msg)
PlayerService 单例('download' 事件)
    ↓ useDownloadToast composable(顶层 onMounted 订阅)
    ↓ window.prisIragent.showToast({title, body, level})
P3.10a showToast IPC
    ↓ main.js showToast (queue + throttle + 偏好)
    ↓ Electron Notification API
```
- ws 广播 → 所有 ws 客户端(MusicView + LyricOnlyView + 独立 EQ 窗)都收 → 但 PlayerService 单例只 emit 一次 → 每个 renderer 实例的 useDownloadToast 都订阅同单例 → 每个 renderer 都弹一次(独立 OS 通知,不是组件级重复)
- 用户拍板 #1 满足:多个 music 窗都同步弹

### 3. level 选 'info' or 'error'
- ok=true → level='info'(P3.10a 5s 节流,连续下载不 spam)
- ok=false → level='error'(critical urgency + 不节流,即使用户偏好「仅错误」仍弹)

### 4. 不直接调 store 返回值弹 toast
避免 ws 到达前的「已下载」误报。downloadSong 返回 HTTP 响应(可能仍是「已发送请求」),真正完成等 ws download_done 推回来。LyricOnlyView 的 onDownloadCurrent 失败兜底 toast(直接调 showToast 不走 composable,因为已经知道 ok=false)。

### 5. 复用 P3.3 ♡/♥ PopupMenu
不修改 PopupMenu 组件本体(4 项不变),MusicView 自行传 5 项 items(含 download key),onCtxSelect case 'download' 调 player.downloadSong。沿用 @contextmenu.prevent + openCtx(x, y) + 模板挂 PopupMenu 范式。

### 6. LyricOnlyView 按钮位置
#settings-panel 末尾 EqInline 之后,歌词窗 hover settings-panel 时露出,平时 opacity 0.25。`lyric.track?.id` 为空时 disabled,避免无曲请求发送。

### 7. 防双弹
- useDownloadToast 顶层 onMounted 一次订阅
- onBeforeUnmount 退订
- 单一事实来源 = PlayerService 单例 'download' 事件 → 多个 useDownloadToast 实例订阅不会重复 emit,各自独立弹系统通知(独立 BrowserWindow 才有 OS 通知,主窗内也只弹一次)

## E2E 验证

- **pytest**: `python -m pytest tests/test_download_toast_p36.py` → **19/19 绿**(~0.5s)
- **全栈回归**: `pytest test_download_toast_p36 + test_toast_p310a + test_eq_p35 + test_music_lyric_p34 + test_music_lyric_vue_p26 + test_music_lyric_p31 + test_music_lyric_p32 + test_music_favorite_menu + test_song_pool_and_favorite` → **309/309 绿**(~1.93s)(P3.6 19 + 之前 290)
- **vite build**: `cd companion/static/music-vue && npx vite build` → 0 error,build 989ms;useDownloadToast-q4xr7uwG.js 12.54KB chunk;main 15.81KB;3 entry(index/lyric/eq)

## 借鉴 / 复用

| 复用项 | 文件 | 用法 |
|---|---|---|
| `_publish` 异步广播 | `companion/music/player.py:390` | 7 处 download 失败/成功 publish |
| `_publish_state` 通用广播 | `companion/prisIragent-music-web.py:122` | api_cmd download 透传 |
| `showToast({title, body, level})` + IPC | `prisiragent-shell/main.js:1475` | renderer 调 prisIragent.showToast |
| `prisIragent.showToast` preload 暴露 | `prisiragent-shell/preload.js` | composable 直接调 |
| PlayerService 单例事件总线 | `companion/static/music-vue/src/services/player.ts` | emit('download', payload) |
| PopupMenu 4 项 + select/close emit | `companion/static/music-vue/src/components/PopupMenu.vue` | P3.3 ship,MusicView 自传 5 项 |
| @contextmenu.prevent + openCtx | `companion/static/music-vue/src/components/MiniBar.vue` | 同模式,复制到 .song-row |
| ws message handler | `companion/static/music-vue/src/stores/player.ts bootstrap` | 加 download_done 分支 |
| #settings-panel 行模式 | `companion/static/music-vue/src/views/LyricOnlyView.vue:170` | P3.6 加「下载当前歌曲」行 |
| ws 透传 type+ts 模式 | `_publish_state` 既有 | type='download_done' 同模式 |

## 风险登记(已规避)

1. ✅ **P3.3 PopupMenu 加项不破现有 4 项** — 不改 PopupMenu 组件本体,MusicView 自传 items。MiniBar 仍用 4 项。
2. ✅ **ws publish 双弹** — player._publish 与 web._publish_state 是两套队列,renderer 只走 ws。web 层 api_cmd 必须显式 publish,否则 renderer 收不到。
3. ✅ **download 失败但 player 进程崩** — `_cmd_download` 内 try/except 包裹完整,任何路径失败都 publish download_done(ok:False)→ renderer 弹 error toast。
4. ✅ **P3.10a level='error' 偏好过滤** — 「仅错误」时只弹 error,success 不弹;「全部」都弹;「关闭」全 skip;与用户偏好一致。
5. ✅ **歌词窗设置面板空间** — 加一行 14px 按钮,空间余量够(P3.4 加 LyricLinesToggle 时已有余量)。
7. ✅ **NTFS 大小写** — `useDownloadToast.ts` 驼峰命名 + 路径小写,沿用 composables 目录约定。
8. ✅ **python asyncio.QueueFull** — ws_state subscriber 满会丢弃并清理,与现有 music_state 透传同模式。
9. ✅ **旧 music UI 不影响** — P3.6 只动 music-vue(Vue 3 新 UI),旧 `companion/static/music/app.js` 已废。
10. ✅ **P3.10a level='info' 节流** — 5s L1 节流,连续下载不 spam。
11. ✅ **多 renderer 实例双弹** — 每个 renderer 独立 BrowserWindow,系统通知独立;PlayerService 单例只 emit 一次,不重复 emit。

## 后续(本 ship 外)

- **P3.7**:N3 chip + N2 search(5h)
- **P3.8**:N10 频谱(4h)— 复用 EqEngine.AudioContext + AnalyserNode,10 段 spectrum bar
- **backlog**:下载历史侧栏(已下载歌曲列表),便于断网回听
- **backlog**:下载进度条(目前只能等完成通知,无进度反馈)

## 与既有 ship 的关系

```
2026-10-04  P3.5 10 段 EQ ship (c9073a7)
2026-10-04  P3.10a desktop toast ship (4f16c3c)
2026-10-04  本计划   P3.6 N8 下载完成 toast ship          ← 本次 ship
```

11 项主流优化进度:**9/11 已 ship**(剩 N3+N2 chip+search / N10 频谱 / N9 AI 歌单推荐)

## 关键 commit 信息

```
feat(music): 下载完成桌面通知 toast (P3.6 N8)

- 后端 player.py: _cmd_download 7 处 publish download_done (ok/err 全覆盖)
- 后端 prisIragent-music-web.py: api_cmd download action → _publish_state 透传 ws
- 前端 stores/player.ts: downloadSong action + onWsStateMsg 加 download_done 分支
- 前端 composables/useDownloadToast.ts: 桥 svc 'download' → prisIragent.showToast
- 前端 views/MusicView.vue: 歌单行 @contextmenu + PopupMenu 5 项 (下载)
- 前端 views/LyricOnlyView.vue: #settings-panel 末尾 btn-download + useDownloadToast
- 19 pytest 全绿 / 309 回归全绿 / vite build 0 error
```

## 用户隐私顾虑(沿用 P3.10b 教训)

P3.6 全程本地,无任何上传/外传:
- 下载走用户已配置的 LX 本地源
- 缓存写本地 companion/music/cache/
- 通知走 OS 通知中心,无网络
- showToast 不发任何网络请求

符合 [p3-10-bubble-cancelled-privacy.md](../p3-10-bubble-cancelled-privacy.md) 中用户原话「0 上传/外传」红线。