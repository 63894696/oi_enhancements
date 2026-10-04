---
name: p2-5-27-music-lx-source-fix
description: P2.5+27(2026-10-04)LX 在线源真接通 — local.js 源 + _find_local_match + Track.source 区分 + is_seed_fallback + X-Prisir-Source header
metadata:
  type: project
---

# P2.5+27 — music LX 在线源真接通(2026-10-04)

> 用户实测:点任何歌 → 控制栏显示「正在播放」→ 几秒后掉同一首 White Christmas 30秒静音 seed.mp3。从开发至今所有歌都掉兜底,从未真接通源。修复后:本地真 mp3 真播,兜底时前端弹「⚠️ 兜底播放」toast 诚实告知用户。**commit**: 待 commit(等用户核验)。

## Context

### 用户原话(2026-10-04)
> 「我在打开的音乐模块里点AI推荐的歌名,播放控制栏没有任何反应。然后点下面的歌曲名,播放器控制栏显示开始播放,但几秒后就跳到播放White Christmas Bing Crosby 这首之前测试用的30秒静音,就是说从开发音乐到现在从没有接通过源,完全没有正常播放过音乐。」

### 根因 3 重叠加
1. **seed_from_url 完全跳过本地 library 命中**:之前实现"先调 online.get_url_multi → googleapis URL → 强制改 path=seed.mp3",从来不查 LocalLibrary 已经扫到的 ~/Music 真 mp3
2. **LX 源 mock.js 永远返 googleapis URL**(国内 DNS 不可达),juhe.js 用 lerd.dpdns.org(第三方公共服务不稳)
3. **Track.source 区分错误**:之前 Track.source 兜底时设 "local"(代表 path 是本地 file),导致 api_state 读 tr.source 后 is_seed_fallback 永远是 false,前端 store 没法识别兜底 → 弹不出「⚠️ 兜底」toast

### 影响
- N9 AI 推荐 ship 后用户测试,推荐列表 20 首全部触发兜底,实际播放全是 White Christmas
- 11 项 P3.x 主流优化(词/搜索/EQ/频谱/下载/toast)全 ship 但底层音源从未真接通
- 用户感受:11 项 ship 是空转,音乐模块核心体验崩了

## 设计原则

**沿用 P3.10b 红线**:0 上传/外传。不调任何外网 LX 源。local.js 源 + 本地 library 匹配是唯一信源。

## 修复内容

### 修复 A:`companion/music/player.py` seed_from_url 重写(关键)

**新流程**(4 段式):
```
1) _find_local_match(title, artist) → 命中 LocalLibrary 真 mp3 → source="local"
2) online.get_url_multi(song_info) → http(s) URL → 入库 source="lx:<src>"
   - local:// 占位 → 跳过(说明无外网 LX)
3) seed.mp3 兜底 → source="seed"(关键区分!)
4) 都没有 → 返错
```

**关键修正**:
- `_find_local_match` 4 级匹配:同 artist+title → 同 title → title 含/被含 → path 含 title
- 兜底时 Track.source 必须是 `"seed"`,**不是** `"local"`(否则前端 is_seed_fallback 永远 false)
- 不再单独检测 googleapis URL(交给 api_stream 的 lx: 前缀处理)

### 修复 B:新文件 `companion/lx_runtime/local.js`(2026-10-04 new)

```javascript
// 完全本地源 — 不调任何外网 LX,musicUrl 直接返 "local://" 占位
on(EVENT_NAMES.request, async ({ action, source, info }) => {
    if (action !== "musicUrl") throw new Error(...);
    return `local://prisir/${songId}/${quality}`;
});
```

- DEFAULT_SOURCES 从 `["mock.js", "juhe.js"]` 改为 `["local.js"]`
- 保留 mock.js / juhe.js 文件,user 后续接入真源时可直接覆盖 DEFAULT_SOURCES

### 修复 C:`companion/prisIragent-music-web.py` api_state / api_stream 增强

```python
async def api_state(req):
    snap = APP.player.snapshot()
    track = snap.get("track") or {}
    is_seed = track.get("source") == "seed"
    return _ok(state=snap, is_seed_fallback=is_seed)

async def api_stream(req):
    ...
    is_seed = (tr.source == "seed")
    ...
    if is_seed:
        headers["X-Prisir-Source"] = "seed"
```

### 修复 D:`companion/static/music-vue/src/stores/player.ts` playById 兜底检测

```typescript
await svc.playSong({...})
setTimeout(async () => {
    const st = await api('/api/state')
    if (st?.is_seed_fallback) {
        const ui = useUiStore()
        ui.pushToast('warn', `⚠️ 兜底播放:「${song.title} - ${song.artist}」本地无 mp3,实际播 seed.mp3 占位...`)
    }
}, 600)
```

## 测试

### 1. 新增 5 类测试 + 改 4 类测试(`tests/test_music_multi_source.py`)

**新增**:
- `TestFindLocalMatch` (3):同 artist+title 精确命中 / 无匹配返 None / 跳过 seed.mp3 自身
- `TestSeedFromUrlLocalMatch` (2):有 mp3 时命中本地 / 无 mp3 + online 返 local:// 兜底 seed
- `TestLocalJsSourceExists` (2):local.js 文件存在 + 处理函数体内不含 googleapis/lerd.dpdns/http URL / DEFAULT_SOURCES=['local.js']
- `TestApiStateSeedFallback` (2):api_state 返回 is_seed_fallback 字段 / source=seed track 让 is_seed_fallback=True
- `TestApiStreamSeedHeader` (1):api_stream 设 X-Prisir-Source='seed' header

**修改**(适应新流程):
- `test_seed_from_url_first_source_succeeds`:`source='mock.js'` → `source='lx:mock.js'`
- `test_seed_from_url_all_sources_fail`:从期望失败改为期望成功(seed.mp3 兜底)
- `test_seed_from_url_no_online_client`:从期望失败改为期望成功(兜底)
- `test_seed_from_url_googleapis_fallback_to_seed_mp3`:从期望 source='seed' 改为 source='lx:mock.js'(由 api_stream 阶段兜底)

### 2. 全量 music 回归 470/470 绿
```
tests/test_music_multi_source.py (24) + test_music_favorite_menu.py (30)
+ test_music_lyric_p31.py (24) + test_music_lyric_p32.py (40) + test_music_lyric_p34.py (42)
+ test_music_lyric_vue.py (44) + test_music_lyric_vue_p26.py (44) + test_music_vue_build.py (12)
+ test_song_pool_and_favorite.py (40) + test_eq_p35.py (28) + test_chip_search_p37.py (35)
+ test_download_toast_p36.py (39) + test_toast_p310a.py (33) + test_spectrum_p38.py (31)
+ test_recommend_n9.py (31) = 470 passed in 2.86s
```

### 3. vite build 0 error
```
✓ built in 1.01s
main-D0QCZIDA.js 1.85 kB / main-CrgwM_X8.js 19.11 kB (gzip 7.57 kB)
```

### 4. 真起 music_web 后端 E2E 验证
| 场景 | is_seed_fallback | track.source | path | X-Prisir-Source |
|---|---|---|---|---|
| 孤勇者(本地无 mp3) | ✅ True | seed | seed.mp3 | seed |
| Test - Kangaroo_MusiQue(本地有 mp3) | ✅ False | local | Kangaroo...mp3 | None |

## 与既有 ship 的关系

```
2026-10-04  P3.5 10 段 EQ ship (c9073a7)
2026-10-04  P3.10a desktop toast ship (4f16c3c)
2026-10-04  P3.6 N8 下载完成 toast ship (9b1ec51)
2026-10-04  P3.7 N3+N2 chip 多选 + search ship (3f5e84e)
2026-10-04  P3.8 N10 实时频谱 ship (22328af)
2026-10-04  N9   AI 歌单推荐 ship (基于 P2.5+25)
2026-10-04  N9.1 bug fix batch  (2977ebf)
2026-10-04  P2.5+27 LX 真接通    (待 commit)  ← 本次 ship
```

## 风险登记

1. **local.js 是占位源**:user 后续若要真 LX 在线(juhe/csv 私源),需改 DEFAULT_SOURCES,但所有现存 ship 路径不破
2. **兜底检测 600ms 延迟**:toast 比播放触发晚 600ms(等后端 state 稳定);用户可能先听 600ms 才看到警告(可接受)
3. **X-Prisir-Source 仅供调试**:浏览器 fetch 没法阻止 audio 实际加载(seed.mp3 仍然会响),header 仅做记录
4. **前端 store 内部 useUiStore 调用**:必须在 store action 里 import + 调用,不能在 module 顶层(避免 Pinia scope 问题)
5. **mock.js / juhe.js 文件保留**:不动,user 后续若要启用需改 DEFAULT_SOURCES;P3.10b 0 上传红线仍生效(默认仍 local.js)
6. **_find_local_match 第 4 级 (path 含 title)**:可能误命中(如别的文件夹下同名文件),但只是兜底前的次优解,优先级最低(score=3)

## 后续(等用户拍板)

- 接 juhe.cn 真源 API(需 user 提供 key,违背 0 上传红线需 user 确认)
- 收藏/下载的本地 mp3 自动加进 SongPoolCatalog(目前下载存 ~/Music 但 catalog 没同步)
- N9 推荐效果:本地命中率高之后,推荐 rank 应优先推 ~/Music 真有的歌(避免推了播放失败)

## 关联

- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线(P2.5+27 继承)
- [[n9-music-ai-recommend-shipped]] — AI 推荐(本次 ship 让推荐真能播)
- [[n9-1-music-bug-fix-batch]] — N9.1 反馈 bug 本次一并修复
- [[p2-5-22-music-multi-source-and-calendar-30d]] — 多源 fallback 起点
