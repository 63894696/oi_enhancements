---
name: p2-5-23-hotfix-playback-prev-favorite
description: P2.5+23 hotfix — LX 探测灯仍灰 + 上下首无反应 + 收藏无限增长 3 个 ship 后实测 bug
metadata:
  type: project
---

# P2.5+23 hotfix — 探测灯仍灰 + 上下首无反应 + 收藏无限增长(2026-10-03)

## 概要

P2.5+23 hotfix 1(`622ff10`)ship 后用户实测 3 个新 bug:

1. **LX 探测灯仍灰色**:虽然后端 ship 了 no-store + `startLxHealthProbe`,但 Electron BrowserWindow 子窗**复用**时没 reload,旧子窗仍渲 ship 前的 app.js。
2. **上下首点击无反应**:后端 `_cmd_next/prev` 已存在,但前端 `onStateEvent` 收到 ws state 时只更新 `state.track`、没切 `audio.src` — 下一首的 audio 实际没换。
3. **收藏无取消,无限增长**:用户原话「再点击无法取消收藏这不是无限增加了吗,那么收藏上限是多少首歌?最好的方式无疑就是已收藏的歌曲再点收藏就取消」。

## 根因

### Bug 1: LX 探测灯仍灰(船 ship 后 BrowserWindow 缓存)

`prisIragent-shell/main.js` 的 `_createChildWindow`:
```js
const existing = childWindows.get(spec.label);
if (existing && !existing.isDestroyed()) {
  existing.show();
  existing.focus();
  return existing;  // ← 没 reload,旧 app.js 缓存继续用
}
```

子窗 close 只 hide 不 destroy,第一次 ship 前打开的 music 子窗 hide 后,第二次 open 复用,即使后端 ship 新前端 + 加 no-store 头,**旧子窗的 webContents 还在用磁盘缓存的旧版本**(Electron Chromium 启发式)。

### Bug 2: 上下首点击无反应

前端 `onStateEvent`(app.js)收到 `music_state` 时:
```js
state.track = ev.track || null;
// ← 没切 audio.src!
```

`audio.src` 只在 `onSongClick` 时设过一次。prev/next 走 cmd → 后端 _cmd_play → 推 ws state → 前端 onStateEvent 更新 state.track 但**不切 audio**,用户看不到实际播放切换。

`audio.ended → fetchRandomNext` 也走 onSongClick(不是 onStateEvent),所以自动衔接 OK,但**手动 prev/next 坏了**。

### Bug 3: 收藏无取消,UI 不显示状态

`_cmd_favorite` 是 idempotent(同 title/artist 已存在就返 dedup=True,**不删**)。
前端 favoriteBtn:
- 文案永远 `♥ 收藏`,从不切换
- 点击后端返 `dedup: true` 但前端 toast「已在本地库」,用户**不知道是收藏了还是没收藏**

**没有取消机制 + UI 不显示状态 = 用户以为「只能加,不能减,是不是会无限增加」**。完全合理。

## 修复

### 修 1: 子窗复用时强制 reloadIgnoringCache

```js
// prisiragent-shell/main.js _createChildWindow
const existing = childWindows.get(spec.label);
if (existing && !existing.isDestroyed()) {
  existing.show();
  existing.focus();
  // P2.5+23 hotfix:子窗复用时强制 reloadIgnoringCache,
  // 避免 BrowserWindow 首次加载的旧 app.js / app.css 缓存到磁盘,
  // 后续 open 即使后端 ship 新前端,旧子窗仍渲旧版本。
  try { existing.webContents.reloadIgnoringCache(); }
  catch (e) { logWarn("childWindow", "reloadIgnoringCache fail", `err=${e.message}`); }
  return existing;
}
```

**原理**:`reloadIgnoringCache` 让 Chromium 绕过磁盘 HTTP 缓存(虽然 aiohttp 已加 no-store 头,但 Chromium 还会做启发式缓存 = 算 ETag + Last-Modified + Content-Length 命中即用磁盘副本),强制重新 GET。

### 修 2: onStateEvent 同步切 audio.src + 切收藏

```js
function onStateEvent(ev) {
    if (ev.type === "music_state" || ev.type === "music_progress") {
        const prevTrackId = state.track && state.track.id;
        state.playing = (ev.status === "playing");
        state.track = ev.track || null;
        state.progress = ev.progress || 0;
        state.duration = ev.duration || 0;
        // 旧代码只改 state,不切 audio → prev/next 坏了
        const newTrackId = state.track && state.track.id;
        if (newTrackId && newTrackId !== prevTrackId) {
            const newSrc = `/api/stream/${encodeURIComponent(newTrackId)}`;
            if (els.audio.src !== newSrc) {
                els.audio.src = newSrc;  // ← 关键:ws 推 track → 同步 audio.src
            }
        }
        // 后端说 playing → audio.play();后端说暂停 → audio.pause()
        if (state.track && state.playing) {
            if (els.audio.paused) els.audio.play().catch(() => {});
        } else if (state.track && !state.playing) {
            if (!els.audio.paused) els.audio.pause();
        }
        // ...
        if (newTrackId !== prevTrackId) refreshFavoriteState();
    }
}
```

prev/next handler 加 toast 反馈(失败时给用户「无上下首」提示):
```js
els.prevBtn.onclick = async () => {
    const r = await api("/api/cmd", {method:"POST", body:{action:"prev"}});
    if (!r.ok) showMusicToast(`上一首: ${r.err || "无"}`);
};
els.nextBtn.onclick = async () => {
    const r = await api("/api/cmd", {method:"POST", body:{action:"next"}});
    if (!r.ok) showMusicToast(`下一首: ${r.err || "无"}`);
};
```

### 修 3: 收藏 toggle + is_favorite + 按钮文案切换

**后端**(`player.py`):

```python
def remove_track(self, track_id: str) -> bool:
    """P2.5+23 hotfix:取消收藏。同步清理 _by_artist,避免 search() 返回已删除 id。"""
    if track_id not in self._tracks: return False
    tr = self._tracks.pop(track_id)
    if tr.artist and tr.artist in self._by_artist:
        lst = self._by_artist[tr.artist]
        if track_id in lst: lst.remove(track_id)
        if not lst: self._by_artist.pop(tr.artist, None)
    return True

async def _cmd_favorite(self, track_id):
    """P2.5+23 hotfix:toggle 语义。已收藏再点 = 取消(用户原话)。"""
    tr = self.library.get(track_id)
    for existing in self.library._tracks.values():
        if (existing.source == "song_pool_fav"
                and existing.title == tr.title
                and existing.artist == tr.artist):
            self.library.remove_track(existing.id)
            return {"ok": True, "favorited": False,
                    "fav_id": existing.id, "title": existing.title,
                    "unfavorited": True}
    fav_id = hashlib.sha1(f"fav::{tr.id}".encode()).hexdigest()[:16]
    fav = Track(id=fav_id, title=tr.title, artist=tr.artist,
                album=tr.album, path=tr.path, duration=tr.duration,
                source="song_pool_fav")
    self.library.add_track(fav)
    return {"ok": True, "favorited": True, "fav_id": fav_id, "title": fav.title}

def is_favorite(self, track_id) -> Dict[str, Any]:
    """P2.5+23 hotfix:前端查收藏状态,favoriteBtn 文案切换用。"""
    if not track_id: return {"ok": True, "favorited": False}
    tr = self.library.get(track_id)
    if not tr: return {"ok": True, "favorited": False}
    for existing in self.library._tracks.values():
        if (existing.source == "song_pool_fav"
                and existing.title == tr.title
                and existing.artist == tr.artist):
            return {"ok": True, "favorited": True, "fav_id": existing.id}
    return {"ok": True, "favorited": False}
```

`cmd` 路由加 `is_favorite` action。

**前端**(`app.js`):

```js
function renderFavoriteBtn() {
    const isFav = !!state._favorited;
    els.favoriteBtn.textContent = isFav ? "♥ 已收藏" : "♡ 收藏";
    els.favoriteBtn.classList.toggle("fav-active", isFav);
}
async function refreshFavoriteState() {
    if (!state.track) { state._favorited = false; renderFavoriteBtn(); return; }
    const r = await api("/api/cmd", {method:"POST",
        body:{action:"is_favorite", track_id: state.track.id}});
    state._favorited = !!(r && r.ok && r.favorited);
    renderFavoriteBtn();
}

els.favoriteBtn.onclick = async () => {
    if (!state.track) { showMusicToast("请先播放一首歌曲"); return; }
    const r = await api("/api/cmd", {method:"POST",
        body:{action:"favorite", track_id: state.track.id}});
    if (r.ok) {
        state._favorited = !!r.favorited;
        renderFavoriteBtn();
        if (r.unfavorited || r.favorited === false) {
            showMusicToast(`已取消收藏: ${r.title || state.track.title}`);
        } else {
            showMusicToast(`已收藏: ${r.title || state.track.title}`);
        }
    } else showMusicToast(`收藏失败: ${r.err || "?"}`);
};
```

`onSongClick` 末尾 + `onStateEvent` track 变化时调 `refreshFavoriteState()` → favoriteBtn 文案自动切。

**没有硬性上限**(用户没要求)。toggle 语义就够。

## E2E 验证

```
$ curl -X POST --data '{"action":"play_url",...,"title":"晴天"}' /api/cmd
{"ok":true,"state":{"track":{"id":"94adc5b3d7f90b32","path":"...seed.mp3",...}}}

$ curl -X POST --data '{"action":"is_favorite","track_id":"94adc5b3..."}' /api/cmd
{"ok":true,"favorited":false}                          ← 未收藏

$ curl -X POST --data '{"action":"favorite","track_id":"94adc5b3..."}' /api/cmd
{"ok":true,"favorited":true,"fav_id":"a0e5c4c47578050b"}  ← 收藏

$ curl -X POST --data '{"action":"is_favorite",...}' /api/cmd
{"ok":true,"favorited":true,"fav_id":"a0e5c4c47578050b"} ← 收藏后

$ curl -X POST --data '{"action":"favorite",...}' /api/cmd
{"ok":true,"favorited":false,"unfavorited":true,...}    ← toggle 取消

$ curl -X POST --data '{"action":"is_favorite",...}' /api/cmd
{"ok":true,"favorited":false}                          ← 取消后

$ pytest tests/test_music_multi_source.py tests/test_song_pool_and_favorite.py
======================== 40 passed, 1 warning in 1.03s ========================
```

**40/40 全绿**(原 38 + toggle / is_favorite / remove_track 3 新增)。

## 测试

- `test_favorite_toggle_unfavorite_on_second_click` — 第 1 次收藏, 第 2 次取消, 第 3 次再收藏(fav_id 稳定)
- `test_is_favorite_returns_true_after_favorite` — 端点返 favorited bool, toggle 后正确切换
- `test_local_library_remove_track_cleans_by_artist` — remove_track 同步清理 `_by_artist`

## 修改文件清单

**修改 4**:
1. `companion/music/player.py` — `LocalLibrary.remove_track` + `_cmd_favorite` toggle + `is_favorite` 方法 + cmd 路由加 `is_favorite`(约 +50 行)
2. `companion/static/music/app.js` — `onStateEvent` 同步切 audio.src + `renderFavoriteBtn` / `refreshFavoriteState` + favoriteBtn.toggle handler + prev/next 加 toast(约 +50 行)
3. `prisIragent-shell/main.js` — `_createChildWindow` 复用时 `webContents.reloadIgnoringCache()`(~6 行)
4. `tests/test_song_pool_and_favorite.py` — toggle / is_favorite / remove_track 3 新测试,改 1 个 idempotent 测试

**总计**:**4 改** / 约 +110 行 / 3 bug 修。

## Commit

`<see git log>` 标题:`fix(music): LX 探测灯 ship 仍灰 + 上下首切换 + 收藏 toggle(3 bug)`

## 经验

**1. Electron 子窗复用 = 缓存陷阱**:close hide + quit destroy 的设计对用户友好(关窗保活),但 ship 后第一次 open 加载的版本会锁到磁盘。修法**只有 reloadIgnoringCache**(no-store 头不够,Chromium 启发式缓存独立)。所有 ship 包含前端资源的代码,主窗口 + 4 子窗都要走这个机制。

**2. ws 推 state 不等于 audio 同步**:前端 `state.track` 是内存模型,`audio.src` 是 DOM 资源。两者必须显式同步,否则用户点了 prev/next,UI 显示新歌名,但 audio 还在播旧歌。修法:`onStateEvent` 检测 track.id 变化 → 切 audio.src,这是 Electron / aiohttp / 任何 ws+audio 架构都要写的桥接。

**3. 用户点收藏只能加不能减 = 设计漏洞**:toggle 是基本期望,任何「收藏 / 关注 / 喜欢」按钮都该支持 toggle。配套:UI 必须显示当前状态(`♥ 已收藏` vs `♡ 收藏`),让用户**知道当前是不是收藏了**,才不会误点。

**4. 不设收藏上限** = 不必要的限制。用户原话隐含「无限增长我没安全感」,但**有 toggle 就不需要硬上限**。如果以后用户真要 N 个上限(如内存约束),再加。但默认放无限更符合「私人歌单」语义。

**5. 后端 idempotent ≠ UI 不切换状态**:idempotent 是 API 设计正确(同操作多次安全),但 UI 必须反映状态变化(`favorited: true/false`)。如果 UI 只显示成功失败,用户就以为「第二次点怎么没反应」。

## 与既有 ship 的关系

```
2026-10-03  P2.5+23 music 真歌名池 + 新前端布局 [commit 9761fab]
2026-10-03  P2.5+23 hotfix no-store 头 [commit b9211ef]
2026-10-03  P2.5+23 hotfix LX 探测灯 + googleapis 兜底 + mimetypes [commit 622ff10]
2026-10-03  本次 hotfix LX 探测灯 ship 仍灰 + 上下首切换 + 收藏 toggle  ← 本次
```

后续 ship 任何前端资源,**默认 _createChildWindow 已有 reloadIgnoringCache**;任何 toggle 类按钮,**前后端都做状态显示**;ws 推 track 变化,**前端必须切 audio**。
