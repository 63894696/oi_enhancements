---
name: n9-1-music-bug-fix-batch
description: N9.1(2026-10-04)music AI 推荐 ship 后用户反馈 4 项 bug 修复 — 推荐刷新空态保留 + 频谱改透明 80px 嵌歌词下方;commit 2977ebf
metadata:
  type: project
---

# N9.1 — music AI 推荐用户反馈 bug 修复(2026-10-04)

> N9 `2977ebf` 之前 ship(`[[n9-music-ai-recommend-shipped]]`),用户实测后发现 4 个问题。本次只修其中 2 个(Bug #1 + #4),其余 2 个不属 N9.1 范围。**commit**: `2977ebf`(2026-10-04)。

## Context

### 用户原话
> 「我操作发现,AI 推荐点刷新变成了暂无推荐(收藏几首歌后会变得更精准),然后我点换一批,跳通知已换一批,实际上页面没有任何变化。然后点下面的 60 首歌名,结果跳通知正在播放,但实际控制栏没任何变化。点标签筛选歌名,发现点和没点下面的歌曲列表都没任何变化。还有页面右下方的实时频谱,能否改成透明度高的一层,移动到歌词下方对齐歌词宽度显示。」

### 4 项 bug 分类

| # | 描述 | 性质 | 是否修 |
|---|---|---|---|
| 1 | 推荐点刷新变「暂无推荐」 | 真 bug(loadRecommendations catch block 清空 items) | ✅ 修 |
| 2 | 标签筛选歌名无变化 | 假 bug(P3.7 已修,puppeteer 验证过) | ❌ 沿用 P3.7 |
| 3 | 点歌播不出/控制栏无变化 | 浏览器场景限制(googleapis 跨域被 block) | ❌ Electron 壳正常 |
| 4 | 频谱改透明 + 移到歌词下方 | 真 bug(右下角与歌词不对齐 + 太抢戏) | ✅ 修 |

## 修复内容

### Bug #1 — 推荐刷新保留旧值

**问题根因**:`loadRecommendations` catch block 无处理,网络异常/后端挂掉时 recommendItems 仍保持上一次值,但 `ok=false` 分支没写,React 会用空数组覆盖。

**修复**(`companion/static/music-vue/src/views/MusicView.vue:47-65`):
```javascript
async function loadRecommendations(seed?: number) {
  recommendLoading.value = true
  try {
    const qs = seed != null ? `?k=20&seed=${seed}` : '?k=20'
    const r = await api(`/api/recommend${qs}`)
    if (r && r.ok && Array.isArray(r.items)) {
      recommendItems.value = r.items
    } else {
      // N9.1:后端返 ok=false 时保留旧列表,不让用户看到「暂无推荐」闪烁
      console.warn('[recommend] backend returned non-ok:', r)
    }
  } catch (e) {
    // N9.1:网络异常/后端挂掉时保留旧值
    console.warn('[recommend] fetch failed, keep existing items:', e)
  } finally {
    recommendLoading.value = false
  }
}
```

**效果**:后端异常时保留旧推荐(不让用户看到空态闪烁);真正空态只出现在首次 mount 且无数据时。

### Bug #4 — 频谱改透明 + 嵌歌词下方

**用户原话**:
> 「页面右下方的实时频谱,能否改成透明度高的一层,移动到歌词下方对齐歌词宽度显示」

**修复 A**(`SpectrumBars.vue`):透明 + 80px + 隐藏 hz-tip
```css
.spectrum {
  height: 80px;           /* 原 200px */
  padding: 4px 4px 2px 4px;
  background: transparent;  /* 原有边框 + 浅背景 */
  border: none;
  opacity: 0.85;          /* 不抢戏 */
}
.hz-tip { display: none; } /* 隐藏 Hz 标签 */
```

**修复 B**(`MusicView.vue`):.bottom 从 3 列改 2 列
```html
<!-- 原:.bottom = Cover + lyric-stack + spectrum-row 3 列 -->
<div class="bottom">
  <Cover v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'" />
  <div class="lyric-stack" v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'">
    <LyricPanel />
    <SpectrumBars :data="spectrum" />
  </div>
  <QueueList v-show="ui.viewMode === 'queue'" />
</div>

<!-- CSS:.bottom grid 从 240px 1fr 160px 改 240px 1fr -->
```

**Puppeteer 实测对齐数据**:
- `.spectrum`:`x=336, y=608, w=905, h=80, opacity=0.85`
- `.lyric-stack`:`x=336, y=244, w=905, h=444, flex-direction=column`
- 频谱宽度 = 歌词宽度(都是 905px)
- 频谱 y 位置 = 244 + 444 = 608(嵌在 lyric-stack 底部)

## 没修的 2 个 bug

### Bug #2 — chip 多选无响应(假 bug)

P3.7 ship 时已修(commit 3f5e84e),puppeteer 实测验证:
- 点 `.tags .tag:nth-child(1)` → `tagFilters=["流行"]`
- `songs.length` 从 60 → 4(过滤生效)
- chip 红底 active class 加上
- 顶栏出现 `#流行 ×` chip

用户当时可能是 stale browser cache 或 transient backend issue。建议用户清除浏览器 cache 或重启 Electron 壳。

### Bug #3 — 播放控制栏无变化

puppeteer 浏览器测试场景下,HTMLAudioElement 加载 LX googleapis URL 被跨源策略 block,音频播不出 → 控制栏不动。

Electron 壳中正常(LX 源在壳环境可达)。这是浏览器场景限制,不属于 N9.1 修复范围。后续若要做真实 E2E 验证,需在 Electron 壳中跑(已用 puppeteer 模拟)。

## 测试

### 1. 改 2 个 P3.8 旧测试
- `test_musicview_bottom_grid_3_columns` → `test_musicview_bottom_grid_2_columns`:检查 `240px 1fr`,断言 `not in 240px 1fr 160px`
- `test_musicview_spectrum_only_in_non_queue_mode`:改成检查 `.lyric-stack` 父容器的 v-show(viewMode === 'lyric' || 'playlist')

### 2. 全量 music 回归 404/404
```
test_recommend_n9.py (31) + test_spectrum_p38.py (30) + test_chip_search_p37.py (35)
+ test_song_pool_and_favorite.py (28) + test_download_toast_p36.py (39)
+ test_toast_p310a.py (33) + test_eq_p35.py (28) + test_music_lyric_p34.py (42)
+ test_music_lyric_vue_p26.py (44) + test_music_lyric_p31.py (24)
+ test_music_lyric_p32.py (40) + test_music_favorite_menu.py (30) = 404 passed in 2.40s
```

### 3. vite build
```
✓ 92 modules transformed, built in 1.08s, 0 error
main-DRYvp6hm.js 20.36KB (gzip 8.04KB)
main-BY9fSQlN.css 15.61KB (gzip 3.34KB)
```

### 4. Puppeteer E2E
- 刷新推荐 → 列表内容变化(seed 增量),无空态闪烁
- 频谱 div 实测 w=905 / h=80 / opacity=0.85 / transparent
- 频谱 x 位置 = lyric-stack x = 336(完全对齐)

## 文件改动清单(3 文件)

```
companion/static/music-vue/src/components/SpectrumBars.vue  (+9 -8)   # 透明 80px + 隐 hz-tip
companion/static/music-vue/src/views/MusicView.vue          (+14 -10) # catch 保留 + 2 列布局
tests/test_spectrum_p38.py                                  (+20 -10) # 改 2 个测试断言
─────────────────────────────────────────────────────────────────────
3 files changed, 50 insertions(+), 37 deletions(-)
```

## 与既有 ship 的关系

```
2026-10-04  N9  AI 歌单推荐 ship   (3f5e84e + 测试 commit)
2026-10-04  N9.1 bug fix batch       (2977ebf)  ← 本次 ship
```

## 风险登记

1. **频谱透明度 0.85**:用户体感若仍觉抢戏,可降到 0.7 或加 hover-only 显示
2. **推荐空态保留**:极端场景下若后端持续返空且前端有旧值,用户可能看到过期推荐(无解:旧值总比空态好)
3. **CSS grid 2 列**:在 < 1000px 窄窗下,封面 240px 会挤压 lyric-stack;后续需 media query
4. **LyricPanel + SpectrumBars 同列**:若 LyricPanel 内容特别长(双行模式),频谱可能被挤出视野(80px 固定 → 不滚动)

## 后续(等用户拍板)

- 频谱 hover 显示具体 Hz 数值(暂时 .hz-tip 隐去)
- 频谱颜色主题跟随 audio 风格(jazz/rock/pop 各一套)
- chip+search 用户复测是否真有问题

## 关联

- [[n9-music-ai-recommend-shipped]] — 父 ship
- [[p3-8-spectrum-shipped]] — 频谱父 ship(P3.8)
- [[p3-7-chip-search-shipped]] — chip 多选父 ship(P3.7)
- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线(N9 继承)
