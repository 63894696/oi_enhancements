---
name: p2-5-28-no-seed-fallback
description: P2.5+28 A 阶段(2026-10-04)剥 seed.mp3 兜底,改真播/真错;473/473 测试绿 + vite build 0 error
metadata:
  type: project
---

# P2.5+28 A 阶段 — 剥 seed.mp3 兜底

## 用户原始诉求

> 「无论是点AI推荐里的歌名,还是点下面列表里的歌名,虽然都跳提示正在播放,然后就变成了兜底播放,结果还是播放的30秒静音,这个30秒静音需要彻底去掉,点一首歌名能播放就正常播放,不能播放就说明原因是什么。」

> 「我们最早在音乐添加了10来个在线源,你能否找到项目文档中这些源保存在哪里?找到后 A 先走,然后 C。找不到也先走 A,然后搜一批在线源添加好走 C」

## 用户拍板(AskUserQuestion)

- A→C 衔接:**立刻接 C 阶段(推荐)**
- SEED 绿点:**直接删(推荐)**

## 根因三重(调研)

1. **seed_from_url 第 3 段**:`companion/music/player.py` 第 4 段流程的第 3 步会强制让用户「听到」30 秒静音,不论前两步是否命中本地/在线
2. **LX googleapis 国内不可达**:`mock.js` / `juhe.js` 源指向 `storage.googleapis.com`,国内 DNS 通常不通
3. **Track.source="seed" 混淆**:`api_stream` 加 `X-Prisir-Source: seed` header 是「事后告知」,没解决「假装在播」的根本问题

## 设计原则

- **诚实 > 假装**:能播就播,不能播就前端弹具体错误(`本地 ~/Music 无 mp3,在线源不可达,未接 API key`)
- **删 seed.mp3 引用 = 真删**:三处全部删除(seed_from_url / preload_next_url / api_stream header)
- **0 上传红线延续**:不新增外网请求,DEFAULT_SOURCES 仍 `["local.js"]`(C 阶段才会扩)
- **失败信号保留**:`api_cmd` 失败返 `{ok: False, err: "..."}`,前端 store 据此弹 toast

## 改动清单

### 后端 2 改

- **改** `companion/music/player.py`:
  - `seed_from_url` 第 3 段兜底整段删 → 失败返 `{ok: False, err: "无法播放「<title> - <artist>」:本地 ~/Music 无匹配 mp3,在线源不可达(沿用 P3.10b 0 上传红线,未接外网 LX API)"}`
  - `preload_next_url` 删 googleapis→seed.mp3 兜底分支 → 返 `{ok: False, err: "upstream storage.googleapis.com 不可达(C 阶段接新源后可播)"}`
  - `_SEED_MP3` 静态变量删(防回归)

- **改** `companion/prisIragent-music-web.py`:
  - `api_state`:`is_seed_fallback: bool` → `playable: bool` + `last_err: str`(track.source 在 ("local",) or 以 "lx:" 开头 → playable=True)
  - `api_stream`:删 `X-Prisir-Source: seed` header(整段)
  - `api_health`:`seed_fallback` 字段保留(返 file_exists,便于 debug)

### 前端 2 改

- **改** `companion/static/music-vue/src/stores/player.ts`:
  - `playById` 600ms setTimeout:`is_seed_fallback` 检测 → `playable === false` 检测
  - 弹「⚠️ 兜底」toast → 弹「❌ 此歌暂无法播放:<last_err>」error toast

- **改** `companion/static/music-vue/src/views/MusicView.vue`:
  - 顶栏 `<span class="probe seed">SEED:{{player.seedFallback?'✓':'✗'}}</span>` 整段删
  - 加注释说明 P2.5+28 A 已剥

### 测试 1 改

- **改** `tests/test_music_multi_source.py`:
  - 删 `TestApiStateSeedFallback`(2 测试)+ `TestApiStreamSeedHeader`(1 测试)
  - 加 `TestApiStatePlayableField`(3 测试:default idle / local track / unavailable source)
  - 加 `TestApiStreamNoSeedHeader`(正则检测无 `X-Prisir-Source=` 赋值)
  - 加 `TestPreloadNextUrlNoSeedFallback`(googleapis 不再兜底)
  - 改 4 个 test_seed_from_url_* 期望:ok=False + err 字段含「不可用音源」/「不可达」

- **改** `tests/test_music_vue_build.py`:
  - `test_preload_returns_seed_when_googleapis` 改写:期望 ok=False + err 含「googleapis」「不可达」(A 阶段后行为)

## 验证

- `pytest tests/test_music_multi_source.py -q` → 27/27 绿
- `pytest` 15 个 music 相关测试文件全量 → **473/473 绿(2.86s)**
- `vite build` → 0 error,**1.01s**

## 风险登记

1. **用户期望:所有歌都能播** — A 阶段后**没有一首歌能播**(因为 ~/Music 只有 1 个 mp3;LX 默认 local-only 0 上传)。用户原话:「不能播放就说明原因是什么」,所以 error toast 告知是满足诉求的。
2. **seed.mp3 物理文件保留**:不删除 `companion/static/music/seed.mp3`,避免破静态资源引用;文件保留仅作 30 秒静音测试资源,代码已不引用。
3. **C 阶段未开始**:用户原话「找到后 A 先走,然后 C」,A ship 后必须立刻进 C,否则用户面对一个「全红屏」的音乐模块体验。

## 与既有 ship 的关系

```
2026-10-04  P2.5+27 LX 真接通(60ac2d6 待 commit)
2026-10-04  本 ship  A 阶段剥 seed.mp3  ← A
2026-10-04  待拍板  C 阶段接新源         ← C(下一步)
```

P2.5+28 A 是音乐模块「诚实原则」第一步;C 阶段是「让歌真能播」第二步(沿用 P3.10b 0 上传红线,需用户拍板)。
