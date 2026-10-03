---
name: p3-3-music-favorite-menu-shipped
description: P3.3 — music ♡/♥ 长按弹 4 项 PopupMenu(复制曲名+艺人/立即播放/查看所有收藏/取消收藏)
metadata:
  type: project
---

P3.3 是 P3.1+P3.2(歌词窗体验 dc3b83a)+ P3.9(song_pool v2 6a00d52)ship 后的下一项,来自 11 项主流播放器优化的第 7 项 N7「收藏按钮 ♥/♡ 改进」。本次 ship 把 N7 升级为「长按弹出菜单」= 复用落雪 LY + YesPlayMusic 的右键菜单范式,带 4 项最简菜单 + Toast 收藏列表。

## 决策背景

### 用户原始诉求
> 「继续 P3.3 N7 长按收藏」
(从 P3.1+P3.2 ship + P3.9 ship 后的下一项,来自原 11 项优化)

### 11 项优化 (P3.x 列表)
- ✅ N6 歌词进度条可拖动跳转 → P3.1 ship (dc3b83a)
- ✅ N11 歌词窗可锁定尺寸/透明度 → P3.2 ship (dc3b83a)
- ✅ v2 song_pool 14 标签 → P3.9 ship (6a00d52)
- 🚧 N7 收藏按钮 ♥/♡ 改进 → **本次 P3.3 ship (982c327)**
- 待办 N4 歌词单双行 toggle / N5 EQ / N8 下载完成 toast / N3+N2 chip+search / N10 频谱 / N9 AI 歌单推荐

### 当前 ♡/♥ 按钮现状 (ship 前)
```vue
<button class="ctrl fav" :class="{ active: player.isFavorite }"
        @click="onFav" :disabled="!canFav"
        :title="player.isFavorite ? '已收藏' : '收藏'">
  {{ player.isFavorite ? '♥' : '♡' }}
</button>
```
- 仅有 `@click` → `player.toggleFavorite()` → toast「已收藏/已取消收藏」
- 缺乏:复制曲名+艺人 / 查看所有收藏 / 立即播放 / 长按 contextmenu 入口

### 用户拍板 (本次 plan)
- **菜单项数** = 精简 4 项:📋 复制曲名+艺人 / ▶ 立即播放 / 📂 查看所有收藏 / ❌ 取消收藏 (仅已收藏显示)
- **收藏列表 UI** = Toast 列表 (新加 `pushListToast` action 支持多行,TTL 8s 比普通 toast 长)
- **触发** = 长按 600ms (touchstart/touchend) + 右键 (contextmenu)
- **关闭** = click-outside / ESC / 选中后立即关闭

## 实现

### 后端改动 (3 处)

**1) `companion/music/player.py` 加 list_favorites / remove_favorite (P3.3 段)**
```python
def list_favorites(self, limit: int = 50) -> List[Track]:
    """返所有 source='song_pool_fav' 的收藏,默认 50 条防 toast 太长。"""
    if not self.library:
        return []
    favs = [t for t in self.library._tracks.values() if t.source == "song_pool_fav"]
    return favs[: max(1, int(limit))]

def remove_favorite(self, fav_id: str) -> Dict[str, Any]:
    """按 fav_id 删 + 校验 source 必须是 song_pool_fav(防误删 lx/local 源)。"""
    if not fav_id:
        return {"ok": False, "err": "missing fav_id"}
    tr = self.library.get(fav_id) if self.library else None
    if not tr:
        return {"ok": False, "err": f"track not found: {fav_id}"}
    if tr.source != "song_pool_fav":
        return {"ok": False, "err": "not a favorite"}
    self.library.remove_track(fav_id)
    return {"ok": True, "removed": tr.to_dict()}
```

**2) `companion/prisIragent-music-web.py` 加 2 个 handler + 2 个 router**
```python
async def api_favorites(req: web.Request) -> web.Response:
    if not APP.player:
        return _err("player not initialized")
    favs = APP.player.list_favorites(limit=50)
    return _ok(
        count=len(favs),
        favorites=[{
            "fav_id": t.id, "title": t.title, "artist": t.artist,
            "duration": t.duration,
        } for t in favs],
    )

async def api_favorite_remove(req: web.Request) -> web.Response:
    if not APP.player:
        return _err("player not initialized")
    fav_id = req.query.get("fav_id", "").strip()
    if not fav_id:
        return _err("missing fav_id")
    return web.json_response(APP.player.remove_favorite(fav_id))

# router 注册:
app.router.add_get("/api/favorites", api_favorites)
app.router.add_post("/api/favorites/remove", api_favorite_remove)
```

### 前端改动 (5 处)

**3) `companion/static/music-vue/src/components/PopupMenu.vue` (新, ~110 行)**
- 通用 popup 菜单:props `{ x, y, items, visible }` / emits `{ select, close }`
- MenuItem interface: `{ key, label, icon?, danger?, hidden? }`
- `position: fixed; left: x; top: y` + 视口 clamp (Math.min/Math.max 边界保护)
- click-outside (mousedown listener) + ESC 键 → close
- `@mousedown.stop` 防止 popup 内 click 立刻冒泡触发 click-outside 关菜单
- 借鉴 YesPlayMusic Popup + 落雪 LY 右键菜单范式

**4) `companion/static/music-vue/src/stores/ui.ts` 加 pushListToast**
```ts
export interface ListToastMsg {
  id: number; type: 'list'
  title: string; items: { title: string; subtitle?: string }[]; ttlMs: number
}
export type AnyToastMsg = ToastMsg | ListToastMsg

function pushListToast(title: string, items: {title; subtitle?}[],
                       ttlMs = 8000) {
  const id = ++toastId
  toasts.value.push({ id, type: 'list', title, items, ttlMs })
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, ttlMs)
}
```

**5) `companion/static/music-vue/src/components/Toast.vue` 加 list 类型分支**
```vue
<div v-if="t.type === 'list'" class="toast list-toast">
  <div class="list-title">{{ t.title }} ({{ t.items.length }})</div>
  <ul class="list-items">
    <li v-for="(it, i) in t.items" :key="i">
      <span class="li-title">{{ it.title }}</span>
      <span v-if="it.subtitle" class="li-sub"> - {{ it.subtitle }}</span>
    </li>
  </ul>
</div>
```
- 样式:paper 色背景 + ink 文字 + 国画红粗体标题 + max-height: 200px 滚动

**6) `companion/static/music-vue/src/stores/player.ts` 加 listFavorites + removeFavorite**
```ts
async function listFavorites() {
  const r = await api('/api/favorites')
  if (r.ok) return { count: r.count || 0, list: r.favorites || [] }
  return { count: 0, list: [] }
}

async function removeFavorite(fav_id: string) {
  return await api(`/api/favorites/remove?fav_id=${encodeURIComponent(fav_id)}`, {})
}
```
- `api(path, body)` 函数自动判断 body !== undefined → POST,所以 `removeFavorite` 走 POST

**7) `companion/static/music-vue/src/components/MiniBar.vue` ♥/♡ 加 contextmenu + touch 长按**
```ts
const popupX = ref(0); const popupY = ref(0); const popupVisible = ref(false)
let longPressTimer: number | null = null
let longPressTriggered = false
const LONG_PRESS_MS = 600

function itemsForTrack(): MenuItem[] {
  return [
    { key: 'copy', icon: '📋', label: '复制曲名 + 艺人' },
    { key: 'play', icon: '▶', label: '立即播放' },
    { key: 'list', icon: '📂', label: '查看所有收藏' },
    { key: 'unfav', icon: '❌', label: '取消收藏', danger: true,
      hidden: !player.isFavorite },
  ]
}

function onFavContextMenu(e: MouseEvent) {
  e.preventDefault()
  if (!canFav.value) return
  void openMenu(e.clientX, e.clientY)
}

function onFavTouchStart(e: TouchEvent) {
  if (!canFav.value) return
  longPressTriggered = false
  const touch = e.touches[0]
  longPressTimer = window.setTimeout(() => {
    longPressTriggered = true
    if (touch) void openMenu(touch.clientX, touch.clientY)
  }, LONG_PRESS_MS)
}

function onFavTouchEnd(_e: TouchEvent) {
  if (longPressTimer != null) {
    clearTimeout(longPressTimer); longPressTimer = null
  }
  if (longPressTriggered) longPressTriggered = false
}

function onFavClick() {
  if (longPressTriggered) { longPressTriggered = false; return }
  void onFav()  // 单击 toggle
}

async function onSelectMenu(item: MenuItem) {
  popupVisible.value = false
  const tr = player.currentTrack
  if (!tr) return
  if (item.key === 'copy') {
    const text = `${tr.title} - ${tr.artist}`
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        ui.pushToast('info', `已复制: ${text}`)
      } else {
        ui.pushToast('warn', '当前环境不支持剪贴板')
      }
    } catch (err) {
      ui.pushToast('warn', `复制失败: ${(err as Error)?.message || err}`)
    }
  } else if (item.key === 'play') {
    await player.playById(tr.id)
  } else if (item.key === 'list') {
    const r = await player.listFavorites()
    if (r.list.length === 0) ui.pushToast('info', '暂无收藏')
    else ui.pushListToast('已收藏', r.list.map(f => ({
      title: f.title, subtitle: f.artist,
    })))
  } else if (item.key === 'unfav') {
    await onFav()  // 复用单击 handler
  }
}
```

模板改动:
```vue
<button class="ctrl fav" :class="{ active: player.isFavorite }"
        @click="onFavClick"
        @contextmenu="onFavContextMenu"
        @touchstart="onFavTouchStart"
        @touchend="onFavTouchEnd"
        :disabled="!canFav"
        :title="player.isFavorite ? '已收藏(右键菜单)' : '收藏(右键菜单)'">
  {{ player.isFavorite ? '♥' : '♡' }}
</button>
<!-- + -->
<PopupMenu :x="popupX" :y="popupY" :visible="popupVisible"
           :items="itemsForTrack()"
           @select="onSelectMenu"
           @close="popupVisible = false" />
```

## 测试结果

```
pytest tests/test_music_favorite_menu.py -v
43 passed in 0.86s
```

10 个测试类覆盖:
- TestPlayerListFavorites (3) — 空 / 过滤 song_pool_fav / limit 50
- TestPlayerRemoveFavorite (4) — 正常删 / 不存在 / 非 favorite / 空 id
- TestApiFavoritesEndpoint (2) — 空 / 有 (含 fav_id 16 hex 校验)
- TestApiFavoriteRemoveEndpoint (3) — 正常 + 不存在 + 缺 id
- TestPopupMenuComponent (10) — 文件存在 / MenuItem interface / props / emits / menu-items 渲染 / click-outside / ESC / mousedown.stop / viewport clamp / filter hidden
- TestMiniBarFavoriteMenu (12) — contextmenu / touch 长按 600ms / setTimeout+clearTimeout / 长按防 click 双触发 / 4 项菜单 / unfav hidden / danger / onSelectMenu 4 分支 / clipboard fallback / listFavorites + pushListToast 调 / PopupMenu 嵌入
- TestToastListType (2) — list 类型分支 + 样式
- TestUiPushListToast (3) — ListToastMsg interface / pushListToast 函数 (TTL 8000) / return 暴露
- TestPlayerStoreFavoriteActions (3) — listFavorites 函数 / removeFavorite 函数 / return 暴露
- TestRoutesRegistered (2) — 2 个新 router 注册

```
pytest tests/test_music_favorite_menu.py \
       tests/test_song_pool_and_favorite.py \
       tests/test_music_lyric_vue_p26.py \
       tests/test_music_lyric_p31.py \
       tests/test_music_lyric_p32.py \
       tests/test_music_multi_source.py \
       tests/test_music_vue_build.py \
       tests/test_music_lyric_vue.py -q
212 passed in 2.12s
```

```
vite build
vue-tsc 0 error
main.js 19.63 → 23.21 KB (+3.6 KB PopupMenu + 长按 + Toast list + 4 menu 分发)
lyric.js 不改
```

## 关键设计决策

1. **菜单项数 = 精简 4 项** — 用户拍板,拒绝「加 7-8 项标准菜单」诱惑,最实用最小。
2. **收藏列表 UI = Toast 列表** — 用户拍板,拒绝「独立子视图」接口,符合 ship 后 30s 一次性查看场景。
3. **长按 600ms 而非 800ms** — 落雪用 800ms 偏长,PrisirAI 移动端为主,600ms 响应更快仍能区分 click/long-press。
4. **单 trigger + 单菜单 = 1 个 PopupMenu 组件** — 不复用 contextmenu UI 库 (避免引入新依赖),自写 ~110 行通用组件,后续 LyricOnlyView 的右上也可用。
5. **click vs long-press 防冲突** — `longPressTriggered` flag 在 setTimeout 命中后 → 下一次 click 直接 return,不调 toggleFavorite。
6. **clipboard fallback** — Electron secure context 默认支持 `navigator.clipboard.writeText`;失败 toast warn,无 silent fail。

## 复用既有实现

| 复用项 | 文件:行 | 用法 |
|---|---|---|
| `LocalLibrary.remove_track` | `companion/music/player.py:159` | remove_favorite 直接调(已含 source 校验修复) |
| `LocalLibrary._tracks.values()` | `companion/music/player.py:89` | list_favorites 遍历筛 source='song_pool_fav' |
| `Player._cmd_favorite` toggle | `companion/music/player.py:604` | 「取消收藏」菜单项 → 调 toggleFavorite() |
| `_ok / _err` helpers | `prisIragent-music-web.py:114,118` | 2 个新 handler 复用 |
| `ui.pushToast` 模式 | `stores/ui.ts:22` | pushListToast 复用 setTimeout/filter 模式 |
| `player.playById` | `stores/player.ts:205` | 「立即播放」菜单项直接调 |
| `navigator.clipboard.writeText` | 浏览器 API | 「复制」菜单项 |

无重复造轮子。

## E2E 验证 (用户可手动)

```
1) node prisiragent-shell/main.js
2) 托盘 → 🎵 音乐 → 打开 → 点播 sp001 晴天
3) 收藏 → ♡ → ♥
4) 右键 ♥ 按钮 → 期望弹 4 项菜单:
   📋 复制曲名 + 艺人
   ▶ 立即播放
   📂 查看所有收藏
   ❌ 取消收藏 (红色)
5) 选「查看所有收藏」 → 期望 toast 列出「已收藏 (1)  • 晴天 - 周杰伦」 8s TTL
6) 选「取消收藏」 → ♥ → ♡ + toast「已取消收藏」
7) 再右键 ♡ → 期望弹 3 项 (取消收藏项 hidden)
8) 选「复制曲名 + 艺人」 → 期望 toast「已复制: 晴天 - 周杰伦」 + 系统剪贴板有「晴天 - 周杰伦」
9) Chrome DevTools → Toggle device toolbar → iPhone 模式 → 长按 ♥ 按钮 600ms → 期望弹菜单
```

## 与既有 ship 的关系

```
2026-10-03  P2.5+23  真歌名池 v1 + 收藏/下载 ship (388 行 6 标签)
2026-10-03  P2.5+23 hotfix  收藏 toggle fix
2026-10-03  P2.5+24  music Vue 3 + Pinia 重做
2026-10-03  P2.5+25  桌面歌词独立窗 ship
2026-10-03  P2.5+26  alwaysOnTop/lockDrag + 状态持久化
2026-10-03  P3.1+P3.2 歌词窗体验 ship (dc3b83a)
2026-10-03  P3.9 song_pool v2 接入 ship (6a00d52)
2026-10-03  本计划    P3.3 ♡/♥ 长按 4 项菜单 ship (982c327)   ← 本次 ship
```

后续 (本 ship 外):
- **P3.4**:N4 歌词单双行 toggle (2h)
- **P3.5**:N5 EQ (4h)
- **P3.6**:N8 下载完成 toast (3h)
- **P3.7**:N3 chip + N2 search (5h)
- **P3.8**:N10 频谱 (4h)
- **P3.9.v2**:第二批 158 首达 999 (待用户拍板)
- **N9** AI 歌单推荐 (待拍板)

## 关键文件

| 文件 | 类型 | 行数 |
|------|------|------|
| `companion/music/player.py` | 改 | +30 (list_favorites + remove_favorite) |
| `companion/prisIragent-music-web.py` | 改 | +38 (2 handler + 2 router) |
| `companion/static/music-vue/src/components/PopupMenu.vue` | 新 | 110 (通用 popup) |
| `companion/static/music-vue/src/components/MiniBar.vue` | 改 | +110 (1 popup state + 4 menu 分发 + 长按) |
| `companion/static/music-vue/src/components/Toast.vue` | 改 | +35 (list 类型分支 + 样式) |
| `companion/static/music-vue/src/stores/player.ts` | 改 | +20 (listFavorites + removeFavorite action) |
| `companion/static/music-vue/src/stores/ui.ts` | 改 | +20 (pushListToast + ListToastMsg interface) |
| `tests/test_music_favorite_menu.py` | 新 | 410 (43 测试) |

总计 6 改 2 新 1 commit 942 行 (+ 索引更新)。

## 关键文件: 1 commit

```bash
git commit -m "feat(music): ♡/♥ 长按弹 4 项 PopupMenu(复制/播放/收藏列表/取消)"
```

982c327eebd8c2c643b34947fb3962dc94635446