---
name: n9-music-ai-recommend-shipped
description: N9(2026-10-04)music AI 歌单推荐 ship — recommender.py state builder + /api/recommend + RecommendPanel + MusicView 集成;31 + 404 = 435 全绿
metadata:
  type: project
---

# N9 — music AI 歌单推荐 ship(2026-10-04)

> 11 项主流优化已 11/11 ship 完成。本期 N9 是独立 backlog(非第 12 项主流优化),把 PoC(`companion/music/recommend_poc.py`,2026-10-03 P2.5+25)真正接入 MusicView UI。**纯本地启发式,无 AI/ML/上传**(沿用 P3.10b「0 上传/外传」红线 — 见 [[p3-10-bubble-cancelled-privacy]])。规则 + 计数,~5ms/次。

## Context

### 用户原始诉求
> 「请继续 N9 AI 歌单推荐」(P3.8 实时频谱 ship 后的下一项)

### 用户拍板 4 项
1. **state 来源** = SongPoolCatalog(all_songs + is_favorite + last_played_iso)
2. **推荐触发** = 自动(onMounted 拉 1 次)+ 手动(🔄 刷新按钮,seed 增量)
3. **显示位置** = MusicView 顶栏下方独立 `.recommend` 区(顶栏 + 推荐 + tags + 歌单 + 底部)
4. **冷启动** = 14 tag 均匀分布 + random.shuffle(seed)

## 文件改动清单(1 新 4 改 1 删 + 1 新测试)

### 后端
- **新** `companion/music/recommender.py`(203 行)
  - `build_user_state(catalog, days=30)` — 聚合 favorites / play_history_recent / favorite_tags (Counter) / favorite_artists (Counter) / is_cold_start
  - `_recent_iso_to_set(catalog, days)` — last_played_iso 在 N 天内的 song_id set(datetime.fromisoformat 容错,future dates ignore)
  - `_catalog_to_pool(catalog)` — SongPoolCatalog → recommend_poc 期望的 List[Dict] 格式
  - `_cold_start(catalog, k, seed)` — 14 tag × ceil(k/14) 首 + random.Random(seed).shuffle,reason `冷启动均匀分布 ${tag}`
  - `recommend_from_catalog(catalog, k=20, seed=None)` — 主入口;冷启动 vs 偏好分支

- **改** `companion/music/recommend_poc.py`(2 行)
  - `_POOL: Path | None = None`(原指向已删除的 `song_pool_template.csv`)
  - `load_pool()` raise RuntimeError 提示用 `recommender.recommend_from_catalog`

- **改** `companion/prisIragent-music-web.py`(+33 行)
  - 新 import `from music.recommender import recommend_from_catalog`
  - `async def api_recommend(req)` — 读 `k` / `seed` query;调 `recommend_from_catalog(APP.song_pool, k, seed)`;返 `{ok, count, k, seed, is_cold_start, items}`
  - `is_cold_start` 推断:items[0].reason.startswith("冷启动")
  - 注册 `app.router.add_get("/api/recommend", api_recommend)`

### 前端
- **新** `companion/static/music-vue/src/components/RecommendPanel.vue`(195 行)
  - props: `{ items: Array<{id,title,artist,tag,score,reason}>, loading: Boolean }`
  - emits: `'play'`(推荐行 click) + `'refresh'`(🔄 按钮)
  - 纯渲染组件,无 IPC / fetch / WS(沿用 P3.10b 红线)
  - 顶栏:`✨ AI 推荐 · N 首` + 冷启动徽章(`reason.startsWith('冷启动')`)+ `🔄 刷新` 按钮
  - 单行 CSS grid:`24px 1fr 90px 70px 1fr 28px`(rank · title · artist · tag · reason · ▶)
  - 空态:`暂无推荐(收藏几首歌后会变得更精准)`
  - 加载态:`加载推荐中…`
  - max-height 240px + scroll

- **改** `companion/static/music-vue/src/views/MusicView.vue`(+30 行)
  - import `RecommendPanel`
  - state: `recommendItems`、`recommendLoading`、`_recSeedOffset`
  - `async loadRecommendations(seed?)` fetch `/api/recommend?k=20[&seed=N]`
  - `async onPlayRecommend(item)` → `player.playById(item.id)` + pushToast
  - `onRefreshRecommend()` seed += 1 → 换一批
  - onMounted 加 `void loadRecommendations()`
  - 模板:`<RecommendPanel>` 嵌在 `.topbar` 与 `.tags` 之间

### 测试
- **新** `tests/test_recommend_n9.py`(31 测试,7 类)
  - TestRecommenderBuildUserState(4):必备字段 / is_favorite 聚合 / last_played_iso 聚合 / cold_start 判定
  - TestRecommenderRecommendFromCatalog(4):signature / 复用 PoC / catalog 空防御 / 30% 多样性封顶
  - TestRecommenderColdStart(3):random.Random(seed) / per_tag 均匀 / reason 前缀
  - TestApiRecommendEndpoint(5):handler / 注册 / k+seed query / 用 recommend_from_catalog / 返 items+count
  - TestRecommendPanelComponent(5):script setup / emits / 空态文案 / 冷启动徽章 / 无 window.prisIragent
  - TestMusicViewRecommendationIntegration(7):import / state / loadRecommendations / onPlayRecommend / onRefreshRecommend / onMounted 自动 / 模板位置
  - TestPrivacyNoUpload(3):recommender / recommend_poc / api_recommend 无外部 fetch

### 删除
- `companion/music/song_pool_template.csv`(P3.9 重命名为 song_pool.csv 后残留)

## 关键设计点

1. **state builder 在 recommender.py**(从 SongPoolCatalog 实例聚合 favorites/play_history)— PoC 算法原样不动,只重写入口
2. **favorites 不调 /api/favorites**:直接读 `catalog.all_songs[].is_favorite == 1`(避免 player.Track join 反查 tag)
3. **play_history 静态读**:`last_played_iso` 在 30 天内(不强求实时增量,沿用 P2.5+23 范式)
4. **冷启动检测**:`is_cold_start = fav_count == 0 AND len(recent) == 0`,API 端额外检查 `items[0].reason.startswith("冷启动")` 兜底
5. **seed 增量换一批**:`_recSeedOffset += 1` → 后端 `random.Random(seed)` 给出新洗牌(seed 不暴露给用户,纯前端 state)
6. **推荐不存盘 / 不 cache**:每次请求实时计算,840 首打分 + 多样性封顶 ~5ms(本地,无压力)
7. **reason 文案扩展**:`你收藏过 N 首 ${tag}` / `你喜欢 ${artist} 的 N 首歌` / `命中你偏好的 ${tag}` / `你听过 ${artist} 的歌` / `探索新风格 ${tag}` / `随机推荐` / `冷启动均匀分布 ${tag}`
8. **前端嵌入 MusicView 顶栏下方**(.topbar + .recommend + .tags + .songs + .bottom)— 不替换现有歌单,纯增量
9. **纯渲染 RecommendPanel**:fetch 逻辑在 MusicView,panel 只 props + emits(易于测试 / 易于复用)
10. **0 上传/外传**:无 `requests`/`urllib3`/`ClientSession`/无 `openai`/`anthropic`/`LLM` 关键字(测试断言)

## 复用既有实现

| 复用项 | 来源 |
|---|---|
| `recommend_poc.recommend()` 算法 | `recommend_poc.py:72-132`(原样不动) |
| `recommend_poc._reason_for()` | `recommend_poc.py:56-69` |
| `SongPoolCatalog.all_songs` | `song_pool.py:148-150` |
| `SongMeta.is_favorite / last_played_iso` | `song_pool.py:54-57`(P3.9 v2 CSV 已有) |
| `APP.song_pool` | `prisIragent-music-web.py:95` |
| `_ok` helper | `prisIragent-music-web.py:75-90` |
| `player.playById()` | `services/player.ts`(P3.7 范式) |
| `.tags` 上方嵌入 | `views/MusicView.vue:265-272` |
| 主题色 gh-gold / gh-red | 沿用全项目 |
| P3.10b 0 上传红线 | [[p3-10-bubble-cancelled-privacy]] |

## 借鉴

- **Spotify Discover Weekly / Daily Mix**:list + 行内 ▶ + refresh → N9 沿用
- **网易云「每日推荐」**:徽章 + 列表(冷启动徽章设计)
- **落雪 lx-music-desktop**:reason 文案沿用风格
- **YesPlayMusic / QQ 音乐「猜你喜欢」**:30 首 + 启发式打分

## E2E 验证

### 1. 单元 + 集成(31/31)
```bash
python -m pytest tests/test_recommend_n9.py -v
# 31 passed in 0.40s
```

### 2. 全栈 music 回归(404/404)
```bash
python -m pytest tests/test_recommend_n9.py tests/test_spectrum_p38.py \
  tests/test_chip_search_p37.py tests/test_song_pool_and_favorite.py \
  tests/test_download_toast_p36.py tests/test_toast_p310a.py \
  tests/test_eq_p35.py tests/test_music_lyric_*.py \
  tests/test_music_favorite_menu.py -q
# 404 passed in 2.67s
```

### 3. vite build
```bash
cd companion/static/music-vue && npx vite build
# ✓ 92 modules transformed,built in 1.07s,0 error
```

### 4. runtime E2E(模拟 backend 真跑)
- 840 首 / 14 tag catalog → cold_start=True → 冷启动分支触发
- seed=42 vs seed=43 顺序不同(random.Random 生效)
- 模拟收藏 5 首(流行 4 + 古风 1)→ 推荐里周杰伦 6 首(score=0.8,reason「你收藏过 4 首流行」)
- tag 分布:流行 6 / 古风 6 / 粤语 6 / 民谣 1 / 影视 OST 1(30% 封顶生效)

## 风险登记

1. **PoC 算法精度有限** — 启发式打分不是 ML;Spotify / 网易云用 ML 模型,N9 规则权重 0.5/0.3/0.1/0.1 是拍板假设
2. **CSV 静态读 favorites/play_history** — 不实时增量(没写回文件);用户收藏后下次刷新才生效;若失败可后续加增量写
3. **冷启动 14 tag 均匀** — 假设 tag 分布合理;实际 v2 CSV 14 tag 各数量不一,per_tag=ceil(20/14)=2,循环取模
4. **推荐不存盘 / 不 cache** — 每次请求实时计算,840 首打分 + 多样性封顶 ~5ms(本地,无压力)
5. **不跨 BrowserWindow** — MusicView 主窗专用;MiniBar / LyricOnlyView / EqWindowView 不显示推荐
6. **AI 文案** — 用户拍板假设「纯本地规则」;若用户后续要求 LLM 介入,需新增 LLM 路由(违背 0 上传红线,放弃)
7. **reason 准确性** — PoC 规则简单(只看 count 阈值);新用户冷启动 reason 简化为「冷启动均匀分布 ${tag}」
8. **推荐项重复** — 30 天内 play_history 强降分,但不彻底排除
9. **前端响应式** — 推荐 list 单行 6 列在 1280px 完整可见;< 1200px 时 overflow-y: auto

## 后续

- N9 暂未做的优化(等用户拍板):
  - **LLM 增强 reason 文案**:违背 0 上传红线,默认不做
  - **实时增量写 last_played_iso**:当前 CSV 静态读,需要写回文件 IO
  - **协同过滤 / 隐式语义推荐**:算法复杂度升级,需 ML 框架
  - **跨 BrowserWindow 推荐**:LyricOnlyView / MiniBar 显示「最近推荐 5 首」

## 与既有 ship 的关系

```
2026-10-04  P3.5 10 段 EQ ship (c9073a7)
2026-10-04  P3.10a desktop toast ship (4f16c3c)
2026-10-04  P3.6 N8 下载完成 toast ship (9b1ec51)
2026-10-04  P3.7 N3+N2 chip 多选 + search ship (3f5e84e)
2026-10-04  P3.8 N10 实时频谱 ship (22328af)
2026-10-04  本计划   N9 AI 歌单推荐 ship              ← 本次 ship
```

11 项主流优化进度:**11/11 已 ship**(N9 是独立 backlog,非主流优化第 12 项)

## 关联

- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线(N9 继承)
- [[P3.9 song_pool v2 CSV]] — is_favorite / last_played_iso 数据源
- [[P3.7 chip+search]] — onPlaySong 范式复用
