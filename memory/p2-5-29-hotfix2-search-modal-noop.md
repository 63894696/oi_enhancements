# P2.5+29 hotfix2 — search modal Enter 无反应 + 后端 silent fall-through (2026-10-05)

## 用户原始反馈

> 「看到了在线搜歌,点开输入任何内容按回车都没反应」

## 根因分析(3 层)

### 根因 #1:`OnlineSearch.search_multi` 在 aiohttp loop 内 `RuntimeError`

```python
# companion/music/player.py (旧)
def search_multi(self, keywords, limit=20, sources=None) -> Dict:
    ...
    try:
        results = asyncio.run(_gather())  # ❌ aiohttp 已经在跑 loop
    except Exception as e:
        return {"ok": False, "err": f"gather fail: ..."}
```

`api_search()` 在 aiohttp handler 里 await 调 `search_multi` → aiohttp 的 event loop 已在跑 → `asyncio.run()` 抛 `RuntimeError: cannot be called from a running event loop` → 走 fail 路径 `{ok: False}`。

但 `api_search` 把它 wrap 在 `_ok()` 里,front-end 拿到 `ok:true, count:0, sources_hit:[]` — **silently fall-through**,用户看不到任何错。

### 根因 #2:`api_search` 不透传 `r.ok / r.err`

```python
# companion/prisIragent-music-web.py (旧)
return _ok(
    q=q,
    count=len(items),       # 0
    sources_hit=sources_hit, # []
    results=items,           # []
)
```

只看 `r.get("items", [])`,不看 `r.get("ok")` / `r.get("err")`。失败也返 ok=True。

### 根因 #3:Enter 键在 results 为空时静默 no-op

```vue
// SearchModal.vue (旧)
} else if (e.key === 'Enter') {
  e.preventDefault()
  if (activeIdx.value >= 0 && activeIdx.value < results.value.length) {
    void onPickSong(results.value[activeIdx.value])
  }
  // ❌ 无结果时不传空,啥都不做
}
```

用户感受:输入了 query、Enter → 期望「再发一次请求」,实际「啥都不发生」。

## ship 改动(1 commit)

### 1. `companion/music/player.py` — search_multi 异步化

```python
# 新:async def + 直接 await gather
async def search_multi(self, keywords, limit=20, sources=None) -> Dict:
    ...
    async def _run_one(src):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, src, kw, limit)
    try:
        results = await asyncio.gather(*[_run_one(s) for s in srcs], return_exceptions=False)
    except Exception as e:
        return {"ok": False, "err": f"gather fail: {type(e).__name__}: {e}"}
    ...
```

`def` → `async def`,内部 `asyncio.run(_gather())` → `await asyncio.gather(...)`。Node 子进程 call() 仍走 `run_in_executor`(阻塞式 stdin/stdout)。

### 2. `companion/prisIragent-music-web.py` — api_search 透传 + await

```python
# 旧:
r = APP.online.search_multi(q, limit=limit)
# 新:
r = await APP.online.search_multi(q, limit=limit)
# + 透传 r.ok / r.err
inner_ok = r.get("ok", False) if isinstance(r, dict) else False
inner_err = r.get("err", "") if isinstance(r, dict) else ""
return _ok(q=q, ok=inner_ok, count=len(items), sources_hit=sources_hit,
           err=inner_err, results=items)
```

### 3. `companion/static/music-vue/src/components/SearchModal.vue` — Enter 重搜 + 后端失败可视化

```vue
// Enter 分支:results 非空时播,空时强制重发
} else if (e.key === 'Enter') {
  e.preventDefault()
  if (activeIdx.value >= 0 && activeIdx.value < results.value.length) {
    void onPickSong(results.value[activeIdx.value])
    return
  }
  // 无结果:立即重发,不走 debounce
  const trimmed = query.value.trim()
  if (trimmed) {
    if (debounceTimer != null) { clearTimeout(debounceTimer); debounceTimer = null }
    void doSearch(trimmed)
  }
}
```

```vue
// doSearch:3 路分支(成功 / 后端 ok 但 results 空 / 后端 err)
if (okFlag && resultsArr.length > 0) {
  // 正常显示
} else {
  results.value = []
  const detailed = errMsg
    || (sourcesHit.length === 0
         ? '5 源都没命中(可能限流 / 区域屏蔽 / 关键词太冷门)'
         : `命中的源 ${sourcesHit.join(',')} 也无结果`)
  lastErr.value = detailed
  ui.pushToast('warn', `「${trimmed}」无结果:${detailed}`)
}
```

modal-footer 提示从「Enter 播放」改为「Enter 播放或重搜」明示双语义。

### 5. 测试 5 处改动

- `tests/test_music_search_endpoint.py` — 3 个 `sync.search_multi` 调用套 `asyncio.run()`(因为 search_multi 现在是 async);新增 `test_search_multi_works_inside_running_loop`(在嵌套 loop 内 await 跑通 — 旧版会 RuntimeError,新版过)
- `tests/test_chip_search_p37.py` — placeholder 断言从「搜歌名/歌手」改为「本地过滤」+「840」(沿用 N9.1 hotfix)

## 验证

### 1. 单元 + 集成测试

```bash
python -m pytest tests/test_music_search_endpoint.py -v
# 18 passed(包括新增 test_search_multi_works_inside_running_loop)
```

### 2. 全栈 music 回归

```bash
python -m pytest tests/test_music_multi_source.py \
       tests/test_music_favorite_menu.py tests/test_music_lyric_*.py \
       tests/test_music_vue_build.py tests/test_song_pool_and_favorite.py \
       tests/test_eq_p35.py tests/test_chip_search_p37.py \
       tests/test_download_toast_p36.py tests/test_toast_p310a.py \
       tests/test_spectrum_p38.py tests/test_recommend_n9.py \
       tests/test_music_search_endpoint.py -q
# 518 passed in 3.08s
```

### 3. vite build

```bash
cd companion/static/music-vue && npm run build
# vue-tsc 0 error + vite build 1.28s,新 bundle main-CCy-858v.js
```

### 4. 后端真起 E2E

```bash
# 重启 music-web 后:
curl -sG --data-urlencode "q=孤勇者" --data-urlencode "limit=20" \
    "http://127.0.0.1:18803/api/search"
# {"ok": true, "q": "...", "count": 20, "sources_hit": ["wy"], "err": "", "results": [...]}
```

旧对比:
```bash
# {"ok": true, "q": "...", "count": 0, "sources_hit": [], "results": []}
# ↑ silent fall-through:err=""但实际 5 源全 RuntimeError
```

backend log tail:
```
[player] INFO [online.search] tx fail: search unknow error
[player] INFO [online.search] kw fail: search unknow error
[aiohttp.access] INFO 127.0.0.1 [...] "GET /api/search?q=...&limit=20 HTTP/1.1" 200 5227
```
注意:**没有 `coroutine was never awaited` warning** — 后端 async 修生效。

### 5. puppeteer 端到端

```js
// 1. 点按钮 → SearchModal 弹
document.querySelector('.btn-search').click()
JSON.stringify({modal: true, inputFocused: true})

// 2. 输入 + 等待 debounce → 结果出现
await type('周杰伦')
// resultRows: 20 (wy 命中 jettison wye public API)

// 3. results 非空时 Enter → 触发 onPickSong(activeIdx=0)
window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))
// → player.playSongItem(results[0])
```

实际查 main-CCy-858v.js 含新逻辑 + 后端 services_hit=["wy"] + count=20。

## 与既有 ship 的关系

```
2026-10-05  P2.5+29 search endpoint + modal + 5 源并行 fallback ship (77cc886)
2026-10-05  P2.5+29 hotfix: killBackend + 顶栏 UX (commit 8e12123)
2026-10-05  P2.5+29 hotfix2: search modal Enter + 后端 silent fall-through (本次,1 commit)
```

## 关键决策记录

1. **不动 `get_url_multi`** — 它用 sequential for-loop,无 asyncio 问题。`search_multi` 之所以 break 是因为我用了 `asyncio.gather` 并发,得异步才能 await。
3. **api_search 透传 err 而非 raise** — 一致性:前端的成功 toast / warn toast 都基于 r.ok 分支;raise 异常会让前端走 catch 路径,但 toast 文本生成在 doSearch 里更灵活。
4. **Enter 双语义** — 1) results 非空 + 高亮行 → 播歌;2) results 空 → 强制重发。modal-footer 提示词同步改「Enter 播放或重搜」。
5. **`run_in_executor` 保留** — Node 子进程 call() 仍是阻塞式 stdin/stdout,不能放 async gather 直接执行,得并发起来用 executor 跑。

## Why / How to apply

**Why**:PrisirAI music 后端是 aiohttp 异步应用,任何 sync 函数内部 `asyncio.run()` 都会撞上「loop already running」。Backend 把 sync error 静默 wrap 是双重坑:用户看不见错,debug 也无从下手。

**How to apply**:
- 任何**新加的搜索 / 聚合 / 并发调用**都要 async def,让调用方 await
- 任何 aiohttp handler 的成功响应 `_ok` 必透传 inner r.ok / r.err — silent fall-through 是 UX 大坑
- 任何键盘 Enter 行为不要 silent no-op:无结果时也要给用户「我能做什么」(重搜 / 清除 / 关闭)
- 任何 vitest / pytest 单测里 sync 调用一个本来该 async 的函数,测试不会暴露 running-loop 错误 — 真起 server 才会。测试用例加 `test_*_works_inside_running_loop` 是必须的。

## 给用户的下一步

+ 重启 PrisirAI 主壳(如果还在跑老版)
+ 顶栏「🔍 在线搜歌」按钮
+ 输入任意中文 → 300ms 后自动搜(歌词里"周杰伦"返 20 条 wy 源结果)
+ 「⚠ 无结果:5 源都没命中(可能限流 / 区域屏蔽 / 关键词太冷门)」toast 会弹出(即使 API 返了结果但内容不匹配)
+ 按 Enter 也可强制重发 — 不会再 silent no-op
+ 当前 wy 公共 API(lxmusicapi.onrender.com)对中文 query 匹配质量差(返古典乐/拉丁语);用户预期「搜孤勇者能出孤勇者」需要加 ingross-source 调研或回到 P3.10b 0 上传红线下的本地化方案 — 下个 ship 拍板。