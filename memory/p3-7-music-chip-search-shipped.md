---
name: p3-7-music-chip-search-shipped
description: P3.7(2026-10-04)music N3 chip 多选 OR + N2 实时搜索框 ship — song_pool/list_visible 加多 tag + 模糊 q;前端 stores/ui 加 tagFilters + searchInput 200ms debounce;MusicView 顶栏 5→7+ 元素 flex-wrap + 搜索框 + 多 chip 显示 + × 清空一键还原
metadata:
  type: project
---

# P3.7 music N3 chip 多选 + N2 search 框 ship(2026-10-04)

## Context

**用户原始诉求**:「请继续 P3.7 N3 chip + N2 search」(P3.6 ship 9b1ec51 后的下一项 — 11 项主流播放器优化的合并 ship)
**位置**:11 项主流优化的合并 ship — chip 多选 OR 合并 + 实时搜索框
**前置 ship**:P3.6(9b1ec51 N8 下载完成 toast)+ P3.5(c9073a7 10 段 EQ)+ P3.10a(4f16c3c 桌面弹卡)

## 用户拍板 4 项(本 plan 假设,用户全选推荐)

1. **chip 多选逻辑** = OR(任一命中即返;网易云桌面同款)
2. **search 范围** = visible_songs(60 首,洗牌后窗口)— 与 chip 协同一致
3. **search 匹配** = title + artist(简单包含,case-insensitive;落雪同款)
4. **search 与 chip 协同 + 顶栏布局** = chip∩search(结果累乘;Spotify/YouTube Music 风格)+ flex-wrap 折行(7+ 元素 1280px 完整可见)

## 调研:chip + search 现状

1. **前端 chip 已 toggle**(MusicView.vue:67-70 `onTagClick` 走 `tag === ui.tagFilter ? '' : tag`,再点同 chip 清空),**非 radio**;多选只需单值换成数组 + toggleTag(t)
2. **后端 /api/songs**(prisIragent-music-web.py:240)只读 `req.query.get('tag')` 单值,调 `APP.song_pool.list_visible(tag=tag)`
3. **`song_pool.list_visible`**(`song_pool.py:186`)签名 `(self, tag: Optional[str] = None)`,单 tag 等值过滤
4. **search 现状** = 零。前端 MusicView 无 `<input type="search">`;后端 `grep search` 仅命中 `api_library_search`(只搜 LocalLibrary 不走 song_pool)
5. **歌单池规模**:`song_pool.csv` 842 行(1 header + 841 数据),14 tag,唯一 artist 227,可见窗口 60 首(洗牌)

## 主流 chip+search 设计借鉴

- **Spotify Web**:三组 chip 可同时激活(AND);chips 横排可滚 + 右侧搜索框实时过滤当前 chip
- **网易云桌面**:主分类 radio + **子分类 chip 多选**(语种/风格/场景/情感),交集显示;与本项目最像
- **落雪 lx-music-desktop**:顶栏 search input + 全局 ⌘K 命令面板(P3.7 落 input,⌘K 留 P3.8+);歌单标签侧栏树状目录单选(仅参考视觉)
- **YouTube Music**:chip active 浮起(阴影 + 背景渐变)+ 搜索图标 → 展开 search bar;chip + search 协同

## 改动文件清单

**后端 2 改**:
- `companion/music/song_pool.py:list_visible` (+24 行)— 签名 `(tags: Optional[List[str]] = None, q: Optional[str] = None, tag: Optional[str] = None)`;OR 多 tag + case-insensitive title/artist 包含 q;旧 `tag=` 单值参数向后兼容(内部归并到 tags)
- `companion/prisIragent-music-web.py:api_songs` (+12 行)— 读 `req.query.getall("tag", [])` + `req.query.get("q", "")`;调 `list_visible(tags=sel_tags, q=q)`;返字段加 `sel_tags` + `q` 回显,保留 `tags` 字段为全 14 tag 列表(P2.5+24 MusicView 兼容)

**前端 2 改**:
- `companion/static/music-vue/src/stores/ui.ts` (+55 行)— `tagFilter:string` → `tagFilters:string[]`;加 `searchInput:string` + `searchQuery:string`;`setTag(t)` → `toggleTag(t)` (push/remove) + `clearTags()`;`setSearch(q)` 200ms debounce (setTimeout + watch 清旧 timer);`clearAllFilters()` 一键清 chip + search;`tagFilter` 保留为 `tagFilters[0]` computed 别名,旧 `setTag()` 旧 API 归 tagFilters = [tag](无破坏)
- `companion/static/music-vue/src/views/MusicView.vue` (+50 行)— 顶栏 .left 加 `flex-wrap: wrap`(7+ 元素自动折行);加 `<input type="search" class="search-input" :value="ui.searchInput" @input="onSearchInput" placeholder="搜歌名/歌手">`;已选 tag 显示从单 chip 改 `v-for="t in ui.tagFilters"` 多 chip;加 `<button class="btn-clear" v-if="..." @click="onClearAll">× 清空</button>`;`onTagClick(t)` → `ui.toggleTag(t)`;chip active class 改 `ui.tagFilters.includes(t)`;`onSearchInput` → `ui.setSearch(value)`;watch `[ui.searchQuery, ui.tagFilters]` 触发 `loadSongs()`;loadSongs URL 拼 `?tag=a&tag=b&q=xxx`;CSS 加 .search-input / .search-input:focus / .search-input::placeholder / .btn-clear

**测试 1 新**:
- `tests/test_chip_search_p37.py`(33 测试)— 6 类:TestSongPoolListVisibleMultiTag(5)+ TestSongPoolSearchQuery(6)+ TestApiSongsQueryParams(3)+ TestUiStoreToggleTag(4)+ TestUiStoreSearchDebounce(5)+ TestMusicViewChipSearch(10)

## 关键设计点

### 1. chip OR 语义(网易云桌面同款)
- 多选 tag 返回任一命中集合(并集)
- `list_visible(tags=["流行", "古风"])` → `[s for s in visible_songs if s.tag in {"流行", "古风"}]`
- 用户拍板 #1 满足:多选 chip 一目了然

### 2. search 200ms debounce
```typescript
let _searchTimer: number | null = null
function setSearch(q: string) {
  searchInput.value = q
  if (_searchTimer != null) window.clearTimeout(_searchTimer)
  _searchTimer = window.setTimeout(() => {
    searchQuery.value = (q || '').trim()
    _searchTimer = null
  }, 200)
}
```
- `searchInput` 立即绑 v-model(用户输入无延迟反馈)
- `searchQuery` 200ms 后才 set,MusicView watch 触发 `loadSongs()`
- 连续输入只触发 1 次请求

### 3. chip ∩ search(结果累乘)
```python
out = []
for s in self.visible_songs:
    if eff_tags and s.tag not in eff_tags:
        continue
    if qn:
        hay = f"{s.title} {s.artist}".lower()
        if qn not in hay:
            continue
    out.append(s)
return out
```
- chip 先过滤,再 search 在 chip 结果内包含 → 与用户「选 chip 再搜」心智一致
- 例:`tag=流行 AND q=稻香` → 只有 s2(流行 + 稻香)

### 4. 顶栏 7+ 元素 flex-wrap 重排
- 顶栏 5 元素(换一批 / tag-active / LX / SEED / EQ)→ 7+(+ search + 多 tag + 清空)
- `.left { flex-wrap: wrap; }` 自动折行
- 1280px 宽度完整可见;@media < 1200px 时 .probe-row 折到下一行(N10/N9 加项时易扩展)

### 5. 旧 API 兼容无破坏
- `tagFilter: string` 保留为 `tagFilters[0] || ''` 的 computed(其它调用方不破)
- `setTag(t)` 旧 API 内部归 `tagFilters = [t]`(空字符串 = 清空)
- `list_visible(tag=)` 旧单值参数仍 work(内部归并到 tags)

### 6. 跨文件响应链
```
用户敲字符 → input.searchInput v-model → MusicView onSearchInput
    ↓ ui.setSearch(value) — 200ms debounce
    ↓ searchQuery.value = ...
    ↓ MusicView watch [ui.searchQuery, ui.tagFilters] 触发
    ↓ loadSongs()
    ↓ /api/songs?tag=a&tag=b&q=xxx
    ↓ song_pool.list_visible(tags, q) → OR + case-insensitive 包含
    ↓ { count, total, tags, sel_tags, q, songs }
    ↓ MusicView.songs.value = r.songs
    ↓ Vue 反应式渲染
```

## E2E 验证

- **pytest**:`python -m pytest tests/test_chip_search_p37.py -v` → **33/33 绿**(~0.4s)
- **全栈回归**:`pytest test_chip_search_p37 + test_song_pool_and_favorite + test_download_toast_p36 + test_toast_p310a + test_eq_p35 + test_music_lyric_p34 + test_music_lyric_vue_p26 + test_music_lyric_p31 + test_music_lyric_p32 + test_music_favorite_menu -q` → **342/342 绿**(~2.29s)(P3.7 33 + 之前 309)
- **vite build**:`cd companion/static/music-vue && npx vite build` → 0 error,build 1.03s;main 15.81→16.89KB(+1KB chip+search 逻辑);3 entry(index/lyric/eq)全过

## 借鉴 / 复用

| 复用项 | 文件 | 用法 |
|---|---|---|
| `list_visible(tag)` 现有签名 | `song_pool.py:186` | 升级为 list_visible(tags, q, tag) 三参 |
| `/api/songs?tag=` query | `prisIragent-music-web.py:240` | getall('tag') + 'q' |
| `ui.tagFilter` 现有 | `stores/ui.ts:31` | 改 tagFilters + 保留 tagFilter computed 别名 |
| `ui.setTag` | `stores/ui.ts:71` | 旧 API 归 tagFilters = [t],无破坏 |
| `<button @click="onTagClick">` chip | `MusicView.vue:104` | 不动,改 onTagClick 走 toggleTag |
| `#{{ ui.tagFilter }}` 单 chip 显示 | `MusicView.vue:154` | 改 v-for 多 chip 显示 |
| P2.5+24 toggle 范式 | `MusicView.vue:67-70` | 复用 toggle(push/remove)|
| vue ref + computed | `stores/ui.ts` | 新 searchInput/searchQuery ref + tagFilter computed 别名 |
| `import { api }` | `services/api.ts` | 直接复用,fetch 改 query string |

## 风险登记(已规避)

1. ✅ **存量 `/api/songs?tag=` 兼容** — 新逻辑读 tags + q,旧 tag 单值仍 work(`list_visible(tag=)` 内部归并到 tags);`/api/songs?tag=流行&q=周杰伦` 也可同时传
2. ✅ **chip 多选 vs 0 矛盾** — 默认空数组 = 全;toggleTag 主动 push/remove;clearAllFilters() 一键清
3. ✅ **debounce 200ms 等待延迟** — searchInput 立即反馈;searchQuery 200ms 后 set;MusicView watch 触发请求;落雪 / 网易云都没 disable input
4. ✅ **chip active 太多顶栏拥挤** — 顶栏 .left flex-wrap 折行
5. ✅ **search 命中 0 首歌** — `没有歌曲` 提示已有;搜索词保留显示
6. ✅ **q 含特殊字符(逗号 / 中点)** — 后端 URL 解码已处理;q 是单一 string,不做模糊语法
7. ✅ **favorites 不动** — favorites 是另一 filter(`/api/favorites` 端点,独立)
8. ✅ **音乐缓存 cache 文件污染** — 不动 cache 目录
9. ✅ **歌词窗 LyricOnlyView 不加** — 用户拍板假设,歌词窗只显示当前曲目
10. ✅ **MiniBar 不动** — 不在 MiniBar 加 search/chip
11. ✅ **存量测试** — `test_song_pool_and_favorite.py` 等用 `list_visible(tag=)` 单参数仍 work(向后兼容旧签名)
12. ✅ **顶栏 7+ 元素 1280px 拥挤** — flex-wrap 折行,N10/N9 加项易扩展
13. ✅ **v-model + debounce 冲突** — searchInput 立即响应(用于 v-model),searchQuery 200ms 后才触发请求;两者分离避免竞争
14. ✅ **r.tags 字段含义不变** — 后端 `/api/songs` 返 `tags: 全 14 tag 列表`(P2.5+24 MusicView `r.tags` 读 chip 列表),`sel_tags: 已选` 是新字段
15. ✅ **tagFilter 旧 API 兼容** — 旧调用方读 `ui.tagFilter`(computed = tagFilters[0])仍生效

## 后续(本 ship 外)

- **P3.8** (待):N10 频谱(4h)— 复用 EqEngine.AudioContext + AnalyserNode,10 段 spectrum bar
- **backlog**:⌘K 命令面板(落雪借鉴,全局搜索)
- **backlog**:歌词窗加搜索框(用户拍板假设未做,如要可后续追加)
- **backlog**:MiniBar 加 chip 入口(用户拍板假设未做,如要可后续追加)

## 与既有 ship 的关系

```
2026-10-04  P3.5 10 段 EQ ship (c9073a7)
2026-10-04  P3.10a desktop toast ship (4f16c3c)
2026-10-04  P3.6 N8 下载完成 toast ship (9b1ec51)
2026-10-04  本计划   P3.7 N3+N2 chip 多选 + search ship    ← 本次 ship
```

11 项主流优化进度:**10/11 已 ship**(剩 N10 频谱 + N9 AI 歌单推荐)

## 关键 commit 信息

```
feat(music): chip 多选 OR + 实时搜索框 (P3.7 N3+N2)

- 后端 song_pool.py: list_visible(tags, q, tag) — OR 多 tag + case-insensitive title/artist 包含
- 后端 prisIragent-music-web.py: api_songs 读 getall('tag') + 'q' + 返 sel_tags/q 回显
- 前端 stores/ui.ts: tagFilters: string[] + searchInput + searchQuery + toggleTag/clearTags/setSearch 200ms debounce + clearAllFilters
- 前端 views/MusicView.vue: 顶栏 .left flex-wrap + search-input + 多 chip 显示 + × 清空按钮 + loadSongs 拼 tags+q query + watch 触发
- 33 pytest 全绿 / 342 回归全绿 / vite build 0 error
```

## 用户隐私顾虑(沿用 P3.10b 教训)

P3.7 全程本地,无任何上传/外传:
- search 在 visible_songs(60 首本地窗口)内 模糊匹配
- chip 多选纯本地 OR 合并
- 后端无任何外发请求
- 跨文件响应链不触网

符合 [p3-10-bubble-cancelled-privacy.md](../p3-10-bubble-cancelled-privacy.md) 中用户原话「0 上传/外传」红线。
