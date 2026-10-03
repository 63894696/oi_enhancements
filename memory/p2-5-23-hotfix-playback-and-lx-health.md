---
name: p2-5-23-hotfix-playback-and-lx-health
description: P2.5+23 hotfix — 点歌播不出 + LX 探测灯一直灰色 + mimetypes 缺失 3 个 ship 后实测 bug
metadata:
  type: project
---

# P2.5+23 hotfix — 点歌播不出 + LX 探测灯灰色 + mimetypes NameError(2026-10-03)

## 概要

P2.5+23 ship(`9761fab`)后用户实测 3 个 ship 后可见 bug,先修 2 + 1 个 ship 漏:

1. **LX 探测灯一直灰色**:前端 lxDot 永远 `lx-probe`(灰),`探测中` 文案不变。
2. **点歌播不出**:点任何歌曲 → `audio.src = /api/stream/<id>` → audio 静默加载失败(无 error 提示)。
3. **mimetypes NameError**:`/api/stream/<id>` HEAD/GET 返 500(`mimetypes.guess_type` 未 import,测试时全过,真实 E2E 才发现)。

用户原话:「探测中的状态灯一直是灰色没变过,然后是没有哪首歌点击能正常播放,先做修复」

## 根因

### Bug 1: LX 探测灯灰色

`app.js` 的 `connectWsState()` 只在 `ws/state` 收到 `music_state` / `music_progress` 时更新播放状态,**从未**碰过 `lxDot` / `lxText`。HTML 模板默认 `⚪` 灰色 + 「探测中」文字 → 用户永远看到「灰色 + 探测中」。

后端 `api_health` 只返 `service / version / port / library`,没有 LX online client 状态,前端没有任何 `/api/health` 调用入口。

### Bug 2: 点歌播不出

mock.js 永远返 `commondatastorage.googleapis.com/codeskulptor-demos/.../Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3`。**此 URL 在 sandbox + 国内网络封**,`/api/stream` 走 `_stream_remote_url` aiohttp 透传 → `DNS resolution failed / 10060` → aiohttp 抛异常 → 返 500。

前端 `audio.onerror` 监听**完全没加**,失败静默,用户看不到任何反馈。

### Bug 3: mimetypes NameError

`prisIragent-music-web.py` 顶部 `import` 段一直缺 `mimetypes`。`api_stream` 函数 L297 用 `mimetypes.guess_type(str(p))` → 第一次真访问 `/api/stream/<id>`(任何 track id)就 500。

之前测试没暴露:
- `TestApiSongsEndpoint` 只测 `/api/songs` 不测 `/api/stream`
- P2.5+22 ship 时 `api_stream` 加了 Range 支持 + mimetypes guess_type,但 `mimetypes` 没人加 import,所有 mock 测试只走 `/api/cmd` + `/api/songs`,没 HEAD `/api/stream/<id>`

## 修复

### 修 1: api_health 加 online + seed_fallback 字段

```python
async def api_health(req):
    online = APP.online
    online_info = {
        "configured": bool(online),
        "initialized": False,
        "sources": list(online._sources) if online else [],
    }
    if online:
        cli = online._ensure()
        online_info["initialized"] = bool(cli)
    seed_path = STATIC_DIR / "music" / "seed.mp3"
    seed_fallback = seed_path.exists() and seed_path.stat().st_size > 0
    return _ok(service=..., online=online_info, seed_fallback=seed_fallback, ...)
```

### 修 2: 前端加 startLxHealthProbe()

```javascript
function setLxStatus(state, label) {
    // state: "probe"(灰)|"ok"(绿)|"warn"(黄,configured 但未 init)|"down"(红)
    els.lxDot.classList.remove("lx-probe","lx-ok","lx-warn","lx-down");
    els.lxDot.classList.add(`lx-${state}`);
    els.lxText.textContent = label;
}
async function probeLxHealth() {
    const r = await api("/api/health");
    const online = (r && r.online) || {};
    if (!online.configured) setLxStatus("down","LX 未配置");
    else if (online.initialized) setLxStatus("ok",`LX 已就绪 (${online.sources.join(", ")})`);
    else setLxStatus("warn","LX 待初始化(首次播放触发)");
}
function startLxHealthProbe() { probeLxHealth(); setInterval(probeLxHealth, 5000); }
```

init 段调 `startLxHealthProbe()`。CSS 加 4 档颜色:
```css
.health .dot.lx-probe { color: #888; }
.health .dot.lx-ok    { color: #2e9b53; text-shadow: 0 0 6px rgba(46,155,83,0.4); }
.health .dot.lx-warn  { color: #c69214; }
.health .dot.lx-down  { color: #c0392b; }
```

### 修 3: seed_from_url 检测 googleapis → 兜底本地 seed.mp3

新增 `companion/static/music/seed.mp3`(30s 静音 mp3, 119827 bytes, ffmpeg 生成)。

```python
_SEED_MP3 = Path(__file__).resolve().parent.parent / "static" / "music" / "seed.mp3"

async def seed_from_url(self, song_info, title="", artist=""):
    r = self.online.get_url_multi(song_info)
    if not r.get("ok"): return {"ok": False, "err": ...}
    url = r.get("url","")
    src = r.get("source","lx")
    # id 用 title+artist+songid 哈希(同歌同 id,跨 mock/juhe 源稳定)
    id_seed = f"{actual_title}|{actual_artist}|{song_info.get('hash',song_info.get('songmid',''))}"
    tid = hashlib.sha1(id_seed.encode("utf-8")).hexdigest()[:16]

    is_googleapis = "googleapis.com" in url
    if is_googleapis and self._SEED_MP3.exists():
        t = Track(id=tid, title=..., artist=..., path=str(self._SEED_MP3),
                  duration=0.0, source="local")
        self.library.add_track(t)
        return {"ok": True, "track_id": tid, "source": "seed",
                "url": str(self._SEED_MP3), "fallback": "seed.mp3"}
    # 非 googleapis 或 seed.mp3 缺失 → 原 lx: 透传路径
    ...
```

### 修 4: 顶层加 `import mimetypes`

```python
# prisIragent-music-web.py L33-L42
import argparse
import asyncio
import json
import logging
import mimetypes    # ← P2.5+23 hotfix:api_stream 用 guess_type
import os
import sys
import time
...
```

### 修 5: 前端 audio.onerror 反馈(以前静默)

```javascript
els.audio.addEventListener("error", () => {
    const code = els.audio.error && els.audio.error.code;
    showMusicToast(`音频加载失败(code=${code ?? "?"}): ${state.track ? state.track.title : "?"}`);
    console.warn("[audio error]", els.audio.error, "src=", els.audio.src);
});
```

## E2E 验证

```
$ curl -s http://127.0.0.1:2734/api/health
{
  "ok": true, "service": "prisiragent-music-web", "library": true,
  "online": {"configured": true, "initialized": true, "sources": ["mock.js","juhe.js"]},
  "seed_fallback": true
}

$ curl -X POST --data '{"action":"play_url","song_info":{"hash":"songA"},"title":"晴天","artist":"周杰伦"}' \
       http://127.0.0.1:2734/api/cmd
{
  "ok": true, "action": "play",
  "state": {"status":"playing",
    "track": {"id":"8c57128fdf7f424f", "title":"晴天", "artist":"周杰伦",
              "path":"C:\\Users\\Administrator\\oi_enhancements\\companion\\static\\music\\seed.mp3",
              "source":"local"}},
  "source": "seed"     ← googleapis fallback 触发
}

$ curl -I http://127.0.0.1:2734/api/stream/8c57128fdf7f424f
HTTP/1.1 200 OK
Content-Type: audio/mpeg
Content-Length: 119827
Accept-Ranges: bytes
Cache-Control: no-store

$ curl http://127.0.0.1:2734/api/stream/<id> --range 0-15 | od -c | head -1
ID3 \004 \0 \0 \0 \0 # T S S E \0 \0    ← mp3 ID3 sync header 正确

$ pytest tests/test_music_multi_source.py tests/test_song_pool_and_favorite.py
======================== 38 passed, 1 warning in 1.04s ========================
```

## 测试

新增 4 测试,共 38/38 绿:

- `test_seed_from_url_googleapis_fallback_to_seed_mp3` — mock 返 googleapis → track.source="local",path 落 seed.mp3
- `test_seed_from_url_non_googleapis_stays_lx` — example.com URL 仍走 lx: 透传
- `test_api_health_includes_online_field` — `_FakeOnlineEmpty` 注入 → online.configured=true,initialized=false,sources=[mock.js,juhe.js]
- `test_api_health_includes_seed_fallback` — seed.mp3 存在 → seed_fallback=true

## 修改文件清单

**修改 4**:
1. `companion/music/player.py` — `seed_from_url` 加 googleapis fallback(~30 行)
2. `companion/prisIragent-music-web.py` — `api_health` 加 online 字段 + 顶层 `import mimetypes`(L36 加 1 行,api_health 改 20 行)
3. `companion/static/music/app.js` — `startLxHealthProbe` + `probeLxHealth` + `setLxStatus` + init 调 + audio.onerror(~60 行)
4. `companion/static/music/app.css` — `.lx-probe/ok/warn/down` 4 颜色(~7 行)
5. `tests/test_music_multi_source.py` — 2 个 googleapis fallback 测试
6. `tests/test_song_pool_and_favorite.py` — `TestApiHealthEndpoint` 2 测试

**新增 1**:
1. `companion/static/music/seed.mp3` — 119827 bytes,30s 静音 mp3(ffmpeg `-f lavfi -i anullsrc` 生成)

**总计**:**5 改 + 1 新** / 约 +130 行 / 3 bug 修。

## Commit

`<see git log>` 标题:`fix(music): LX 探测灯 + googleapis 兜底 + mimetypes 缺失(3 bug)`

## 经验

**1. 测试 ≠ 真 E2E**:`/api/cmd` + `/api/songs` 全过 ≠ `/api/stream/<id>` 不 500。Python 测试 + curl 简单端点 = 不够,真实 HTTP 探活**必须覆盖每个返回响应的端点**,特别是 stream / file response 类(状态码、Content-Type、Content-Length、Range)。

**2. import 必须从「第一次跑全模块」触发**:之前 ship 只在测试里跑了几条 handler,mimetypes 没碰 L297 → ship 时 lint / pytest 全过。这次 PID 16952 跑起来第一次 `/api/stream/<id>` HEAD 就 500。修法:**完整 E2E 跑一遍所有路由**(包括 HEAD 静态资源、404 路径),别只测「OK 路径」。

**3. 用户点歌流程任何一步失败要可见**:`audio.onerror` 没加是巨大坑,前端静默 = 端到端无反馈。`showMusicToast` 已有(`开始播放:XX` 弹过),错误时同样调用,用户立刻知道「是不是我机器问题」。

**4. mock.js 的稳定性 mock**:mock 永远返同一首 googleapis mp3,**sandbox/国内网都不可达**。修法:**永远有本地兜底**(seed.mp3),前端能保证「能播」(虽然不是用户点的歌名),agent 后续可走 juhe/ikun/kw 真实源替换 mock(已在 P2.5+22 留口子)。

**5. ID 哈希用 title+artist 而非 url**:同歌跨 mock/juhe 源 mock 的 url 不一样,但用户的歌名是稳定的。`sha1(title|artist|songid)` 保证同歌 → 同 track_id → 同一份 local library 记录,避免重复入库 + 收藏重复。

## 与既有 ship 的关系

```
2026-10-03  P2.5+23 music 真歌名池 + 新前端布局 [commit 9761fab]  ← 上一个 ship
2026-10-03  P2.5+23 hotfix no-store 头 [commit b9211ef]             ← hotfix 1
2026-10-03  本次 hotfix LX 探测灯 + googleapis 兜底 + mimetypes     ← hotfix 2 (本次)
```

后续 ship 任何前端资源前,**先 E2E 跑完所有路由(包括 HEAD 静态 / 4xx 路径)**,别再让用户看到 ship 后的「能开但跑不起来」。
