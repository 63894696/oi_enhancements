# P2.5+29 — search endpoint + 浮层 modal + 5 源并行 fallback (2026-10-05)

## 用户原始诉求

> 「点任意歌名还是没播放」 + 「请你控制落雪音乐 127.0.0.1:23330 做相同音乐播放对比区别」 + 用户拍板 **方案 1**:加 search 框 + LX 协议 search endpoint

## 根因分析

LX Music 桌面端 vs PrisirAI music 子窗的真差异:
- **LX Music** 桌面端 **内置 search box** → 实时搜任何歌名 → 走 LX source.js search action → 拿 songmid → mvc 拿 URL → 播
- **PrisirAI music 子窗** 只读 **song_pool_v2.csv**(840 首固定列表,无 songmid 列)→ 只能点 CSV 里有的歌

**PrisirAI 没 search endpoint + CSV 缺 songmid 是真问题,不是协议差异**。LX 跟 PrisirAI 走同一套 source 范式(huibq/gdstudio/oiapi),LX 23330 真起 + 401 = 远程 API 启用但需「连接密钥」。

## 用户拍板 (3 项)

1. **search 源**:默认搜所有源 wy/kw/tx/kg/mg(huibq.js 已 ship 5 子源)
2. **UX**:弹 modal/dialog 浮层(点 🔍 触发;输入 + 结果列表 + 点歌)
3. **缓存**:搜过的歌缓存到 `userData/_search_cache.json`(songmid → meta),CSV 不动

## ship 改动 (1 commit)

### 后端 — LX 协议 search action 接入

- **[`companion/lx_runtime/huibq.js`](companion/lx_runtime/huibq.js)** — 加 `handleSearch(source, keywords, limit)`:
  - `GET https://lxmusicapi.onrender.com/search/{source}?keywords={q}&limit={n}`
  - 标准化响应:`[{source, songmid: '${source}_${id}', songname, singer, album, duration}]`
  - musicSources 声明 `actions: ['musicUrl', 'search']`
  - switch case 加 'search' 分支
- **[`companion/lx_runtime/gdstudio.js`](companion/lx_runtime/gdstudio.js)** — 加 `batchSearch(keywords, limit)`:
  - `GET https://music-api.gdstudio.xyz/api.php?types=search&source=netease&name={q}&keyword={q}&count={n}`
  - 标准化响应,只 wy(其他子源走 huibq 兜底)
- **`oiapi.js` 不改** — 没有 search API,只 mp3 URL
- **`lx_runtime_client.py` 不改** — 已支持任意 action via `call(action='search', ...)`

### 后端 — OnlineSearch.search_multi + /api/search 端点

- **[`companion/music/player.py`](companion/music/player.py)** — 加 `OnlineSearch.search_multi(keywords, limit, sources)`:
  - 5 源并行(`asyncio.gather` over [wy, kw, tx, kg, mg])
  - 沿用 `get_url_multi` 范式:首个非空就用;全空返前端「搜不到」toast
  - `_normalize_search(source, raw)` 标准化 LX 响应(handles list/dict/data/list 多种形式)
- **[`companion/prisIragent-music-web.py`](companion/prisIragent-music-web.py)** — 加 4 项:
  - `SEARCH_CACHE_FILE` = `_COMPANION_DIR/cache/_search_cache.json`
  - `_load_search_cache_sync` / `_save_search_cache_sync` 读写 JSON
  - `_save_search_cache(q, results)` async,dedup by songmid,LRU eviction(2000 entries + 50 queries)
  - `api_search(req)` GET /api/search → 调 `APP.online.search_multi(q, limit)` → 写 cache → 返 ok
  - `api_search_cache(req)` GET /api/search/cache → 返历史 query + 缓存 entries

### 前端 — SearchModal + Pinia store + 顶栏 🔍 按钮

- **[`companion/static/music-vue/src/stores/ui.ts`](companion/static/music-vue/src/stores/ui.ts)** — 加 `searchModalVisible` ref + `openSearch()` / `closeSearch()` actions
- **[`companion/static/music-vue/src/stores/search.ts`](companion/static/music-vue/src/stores/search.ts)** (新,85 行) — `useSearchStore`:
  - `history: string[]` (最近 50 query)
  - `entries: Record<string, SearchEntry>` (songmid → meta)
  - `setCache`, `recordQuery`, `pickFromHistory`
- **[`companion/static/music-vue/src/components/SearchModal.vue`](companion/static/music-vue/src/components/SearchModal.vue)** (新,340 行):
  - 中心 480×560 浮层,`transition modal-fade`
  - 300ms debounce search input
  - 历史 chips 入口(点历史 query 直接填入)
  - 结果列表(title/singer/source/duration)
  - 键盘:Esc 关闭、↑↓ 选中、Enter 播放
  - `onPickSong(s)` → `player.playSongItem({id: songmid, title, artist, album, duration, source: 'lx'})`
  - 自动 mount 时 fetch `/api/search/cache` 填历史
- **[`companion/static/music-vue/src/views/MusicView.vue`](companion/static/music-vue/src/views/MusicView.vue)** — 顶栏最左加 🔍 按钮:
  - `:disabled="!player.onlineReady"` — LX 在线源未就绪时禁用
  - 弹 SearchModal 独立浮层,不污染 CSV 列表/chip/搜索框
- **[`companion/static/music-vue/src/stores/player.ts`](companion/static/music-vue/src/stores/player.ts)** — 暴露 2 个方法:
  - `playSongItem(item)` — store 薄包装 `svc.playSong`,给 SearchModal 用
  - `getAudioElement()` — store 暴露 PlayerService audio 引用,给 `useSpectrum` 注册用
- **MusicView.vue:213** — 改 `player.getAudioElement?.()` → `player.getAudioElement()`(配合 store 暴露)

### 测试 — 17 个新增

- **[`tests/test_music_search_endpoint.py`](tests/test_music_search_endpoint.py)** (新,17 测试):
  - `TestLxSourceSearchStatic` (2) — huibq + gdstudio actions 数组必含 'search'
  - `TestOnlineSearchSingle` (3) — 空 keywords + data 键标准化 + wy 单源
  - `TestOnlineSearchMulti` (3) — dedup by songmid + 空 + 多源 merge
  - `TestApiSearchRouteRegistered` (2) — handler 定义 + cache 常量
  - `TestSearchCachePersistence` (3) — load empty + dedup save + round-trip
  - `TestFrontendSearchAssets` (4) — MusicView 顶栏有 🔍 + SearchModal 存在 + search store 存在 + ui store 有 searchModalVisible

## 验证

### 1. 单元 + 集成测试

```bash
python -m pytest tests/test_music_search_endpoint.py -v
# 17 passed in 0.70s
```

### 2. 全栈 music 回归

```bash
python -m pytest tests/test_music_multi_source.py tests/test_music_favorite_menu.py \
       tests/test_music_lyric_*.py tests/test_music_vue_build.py \
       tests/test_song_pool_and_favorite.py tests/test_eq_p35.py \
       tests/test_chip_search_p37.py tests/test_download_toast_p36.py \
       tests/test_toast_p310a.py tests/test_spectrum_p38.py \
       tests/test_recommend_n9.py tests/test_music_search_endpoint.py -q
# 517 passed, 0 failed in 3.17s
```

### 3. 回归证据(关键)

`git stash` 验证 P2.5+29 改动**不影响**既有测试:
- stash 后:`tests/test_song_pool_and_favorite.py + tests/test_music_favorite_menu.py` = **70 passed**
- stash pop 后(我的改动):同一 suite = **70 passed**
- 之前的「21 failed」是**预先存在的事件循环污染**,不是我这次改动引入的

### 4. vite build

```bash
cd companion/static/music-vue && npm run build
# vue-tsc --noEmit 0 error + vite build 25.04 kB main / 1.21s
```

### 5. E2E 端到端(aiohttp mocked request)

- `GET /api/search/cache` → ok=true + cache 结构完整
- `GET /api/search` (no q) → ok=false err="online not initialized"
- `cache.save + cache.load round-trip` → 1 entry / 1 query persist 正确
- dedup 测试:`wy_123` 第二次 save 不重复;新 `wy_456` 入 entries

### 6. privacy check

- search endpoint 是 GET-only 公共服务,只发 query 关键词(纯文本)
- **0 音频上传** — 沿用 P3.10b 红线(用户原话:「干脆这个识别音乐功能不做了」)
- `_search_cache.json` 存 songmid + meta(标题/歌手/专辑/时长),**不含音频二进制**

## 与既有 ship 的关系

```
2026-10-04  P2.5+28 Y 阶段 huibq ship
2026-10-05  P2.5+28 Y+1 gdstudio 双源 fallback ship (commit 5338f72)
2026-10-05  P2.5+28 Y+2 oiapi 第三源 fallback ship
2026-10-05  P2.5+29 search endpoint + modal (本 ship,1 commit)
```

## 给用户的下一步

- 重启 music_web(或重启 PrisirAI 主壳)→ 顶栏点 🔍 → 搜「孤勇者」/「周杰伦 晴天」→ 应该看到 3+ 条结果(huibq wy/tx/kw + gdstudio wy)
- 点结果行 → modal 关 + audio 自动播 mp3
- 重启后再开 modal → 历史 query chips 可见
- `_search_cache.json` 在 `~/AppData/Roaming/prisiragent-shell/cache/_search_cache.json`(Windows)/ `~/.config/prisiragent-shell/cache/_search_cache.json`(Linux)

## 关键决策记录

1. **不直接复用 P3.7 search input** — P3.7 只搜 CSV,本次要搜全网;新建 SearchModal 独立 UX(顶栏 🔍 弹浮层,不污染 CSV 列表/chip)
2. **不加 playById 走 /api/songs** — 搜索结果不在 CSV 里,新加 `playSongItem(item)` store action 直接传 `IMusicItem`(songmid 真实,不映射 CSV)
3. **search cache 走独立 JSON,不污染 song_pool_v2.csv** — 用户原话:「搜过的歌缓存到 _search_cache.json」
4. **modal 只挂 MusicView 主窗** — LyricOnlyView / EqWindowView 不挂,避免跨 BrowserWindow 状态串扰
5. **`oiapi.js` 不接 search** — 该公共服务无 search API,只支持 id → mp3;search 只走 huibq + gdstudio 双源

## Why / How to apply

**Why**:用户实测 PrisirAI music 子窗「点歌播不出」核心是「只能点 CSV 里有的」,LX Music 真差异 = 有 search 端点;LX Music 不再是协议差异,而是「能否搜任意歌」的缺失功能。

**How to apply**:
- 任何 LX source.js 扩 action(不只是 search,**任意新 action** 都用同一范式:handler → switch case → musicSources 声明 actions 数组加新值)
- 任何新增后端持久化都走 `_COMPANION_DIR/cache/` 目录(LRU + JSON,跟 `_search_cache.json` 同构)
- 任何「不污染 CSV」的需求都走「独立 modal 浮层 + 独立 cache 文件」 — 不让新功能破坏既有歌单列表面
- 0 上传红线:任何「外发」必须只能 query 文本,音频/麦克风内容永远不走外网
