---
name: yesplaymusic-bridge-status-phase-a-shipped
description: YesPlayMusic 桥 Phase A 只读扩展 ship — LX 模板复用验证
metadata:
  type: project
---

# YesPlayMusic 桥 Phase A 只读 ship (2026-10-07)

## 起因

v2 对比研究([[lx-music-like-software-comparison]])P0 借鉴清单第 2 项 — YesPlayMusic 桥复用 LX 模板 5 分钟 ship。

**关键验证**:**LX 5 步法模板跨软件 0 修改可用**。YesPlayMusic 与 LX 几乎同模式(anonymous HTTP GET /status + 歌词字段),但 JSON 结构是 `data` 包裹(非 LX 的 flat JSON)。这意味着:LX 模板**只需改 endpoint 路径 + JSON 解析**,核心失败语义 / env 覆盖 / readonly 原则 / 测试三件套**完全复用**。

## 设计取舍

**严格只读 + 借鉴原则 4「本地服务默认关闭」**:YesPlayMusic 默认 Open API 是**关闭**的(用户需在「Settings → Experimental Features → Enable local API」手动启用)。这恰好是 v2 对比研究提炼的「本地服务默认关闭」原则的好范例。**绝不**触碰 `/player/play /pause /next /prev /seek /setVolume /mode`。

**LX 模板复用的差异点**:
- URL:127.27232 vs LX 23330
- 路径:`/status /current-track /playlist` vs LX `/status /lyric`
- JSON 结构:`{ data: { playing, currentTime, ... } }` 包裹 vs LX `{ status, name, singer }` flat
- 歌词:YesPlayMusic 不直接返 LRC,而是依赖 NetEase 上游 API(Phase A 不接歌词)

**3 个 L0 命令**(全部只读):
- `yesplaymusic.health` — `/status` 探活 + 延迟
- `yesplaymusic.status` — `{data: {playing, currentTime, duration, volume, loop, shuffle, player}}` 解析
- `yesplaymusic.current-track` — 当前曲目(id/name/artist/album/picUrl/duration)

**100% 本地 + env 覆盖**:`ypmBaseUrl()` 默认 `http://127.0.0.1:27232`,可被 `PRISIR_YESPLAYMUSIC_URL` 环境变量覆盖。

## 实现

**Node 端 index.js (~110 行)**:无 npm 依赖,纯 `http.get` + JSON 解析。

- `ypmBaseUrl()`:env 优先,默认 27232
- `httpGet(path)`:统一 GET helper,JSON 解析
- `fetchStatus()`:`r.parsed.data` 取包裹,返 playing/currentTime/volume/loop/shuffle + track 简版
- `fetchCurrentTrack()`:`r.parsed.data` 取包裹,返 id/name/artist/album/picUrl/duration
- `probeHealth()`:`/status` 探活 + 延迟

## 测试

**Node 端**(`__tests__/run.js`):8 单测 + 3 E2E,**E2E 起临时 Node http.createServer mock YesPlayMusic 响应**,11/11 全过:

```
✓ ypmBaseUrl env 覆盖默认
✓ fetchStatus 不可达 → ok=false + alive=false
✓ fetchCurrentTrack 不可达 → ok=false
✓ probeHealth 不可达 → alive=false + 延迟 < 5000ms
✓ httpGet 404 不可达解析
✓ fetchStatus data 包裹解析(offline mock 失败 → ok=false)
✓ fetchStatus 不可达 last_error 含 HTTP/error 描述
✓ fetchCurrentTrack 不可达 last_error 含描述

[E2E] Mock YesPlayMusic server @ 127.0.0.1:<random>
✓ E2E probeHealth → alive + latency < 500ms
✓ E2E fetchStatus → playing=true + 万神纪 + 三无Marblue
✓ E2E fetchCurrentTrack → 万神纪 + 215s 时长 + picUrl('music.126.net')

[结果] ✓ 11  ✗ 0
```

**E2E mock server 设计**:
```javascript
http.createServer((req, res) => {
  if (req.url === '/status') res.end(JSON.stringify({ data: { playing: true, ... }}));
  if (req.url === '/current-track') res.end(JSON.stringify({ data: { id: 123456, name: '万神纪', ... }}));
});
```

不依赖真实 YesPlayMusic 启动,任何机器都能跑全 11/11。

**Python wrapper** (`tests/test_yesplaymusic_bridge_status.py`):4 case,4/4 全过。

## LX 模板复用验证

| LX Phase A 元素 | YesPlayMusic 改动 |
|---|---|
| 失败语义优先(`ok:false + alive:false + last_error`)| **0 修改** |
| env 覆盖(`PRISIR_<NAME>_URL`)| **0 修改**(只改 `YESPLAYMUSIC_URL`)|
| Node http.get + JSON 解析 | **0 修改** |
| module.exports 测试钩子 | **0 修改** |
| Node 单测 + E2E + Python wrapper | **0 修改** |
| SQLite readonly mode(N/A,这是 HTTP 不是 SQLite)| N/A |
| 端点路径 | 改 `/status /current-track /playlist`(LX 是 `/status /lyric`)|
| JSON 结构 | 改 `{data: {...}}` 包裹(LX 是 flat)|

**结论**:**LX 模板**已**成为 PrisirAI Phase A 只读 HTTP 扩展的「标准模板」**,任何本地 HTTP 服务软件接入只需改「端点路径 + JSON 解析」,**核心 6 处不变**。

## 文件清单

- `extensions/yesplaymusic-bridge-status/package.json` — manifest,4 个 L0 权限
- `extensions/yesplaymusic-bridge-status/index.js` — 实现 + module.exports 测试钩子
- `extensions/yesplaymusic-bridge-status/__tests__/run.js` — Node 单测 + E2E(mock server)
- `extensions/yesplaymusic-bridge-status/package-lock.json` — SDK lock
- `tests/test_yesplaymusic_bridge_status.py` — Python pytest wrapper

## 主仓回归

`python -m pytest tests/test_yesplaymusic_bridge_status.py tests/test_calibre_metadata_indexer.py tests/test_siyuan_vault_indexer.py tests/test_lx_music_bridge_status.py`:13 passed + 3 skipped。

## 后续 Phase B/C 边界

**Phase B**(待用户开绿灯):播放控制 — 需 `yesplaymusic.play / .pause / .next / .prev / .seek` 命令 + `state.write.ypm` 权限。Phase B 必须用户**逐条**勾。

**Phase C**(更远):与 LX / Navidrome 跨音乐源协同(本地音乐统一 metadata),基于已收藏 + 最近播放的本地启发式推荐。沿用 N9 music AI 推荐架构。

## Why

YesPlayMusic 桥的存在不是为了「替代 LX」,而是验证 **PrisirAI 扩展模板的复用性**。LX 与 YesPlayMusic API 几乎同模式,意味着:

1. **Phase A 扩展是「模板化」工作** — 改 endpoint + JSON 解析即可
2. **多音乐客户端并存**:用户可同时跑 LX + YesPlayMusic,主对话按需选择
3. **借鉴原则 4「本地服务默认关闭」**:YesPlayMusic 默认关(用户手动开),LX 默认开(用户装上就开)。两种默认策略都是合理的,**只要不破坏「默认不写」红线**

## How to apply

下次遇到「本地 HTTP API 软件」做 Phase A(LX / YesPlayMusic / MusicFree / Navidrome / Audiobookshelf / Komga / TaleBook / Miniflux / etc):
1. 端口确认 + anonymous GET endpoint 枚举
2. 失败语义优先:`{ok: false, alive: false, last_error: 'HTTP ' + status}`(LX 模板)
3. env 覆盖:`PRISIR_<NAME>_URL`(LX 模板)
4. 测三件套:Node 单 + E2E + Python wrapper
5. L0 权限:`ai.invoke.command:<name>.<action>` + `ui.inject.notification`

**模板已固化**,Phase A 工作量从 2-3 天降到 30 分钟。

相关:[[lx-music-bridge-phase-a-shipped]], [[siyuan-vault-indexer-phase-a-shipped]], [[calibre-metadata-indexer-phase-a-shipped]], [[lx-music-like-software-comparison]], [[p3-10-bubble-cancelled-privacy]]