---
name: navidrome-bridge-status-phase-a-shipped
description: Navidrome 桥 Phase A 只读 ship — Subsonic 协议首次落地 + Token≠密码借鉴验证
metadata:
  type: project
---

# Navidrome 桥 Phase A 只读 ship (2026-10-07)

## 起因

v2 对比研究 Top 5:[[lx-music-like-software-comparison]] #16 Navidrome — 本地音乐管理先驱,**Subsonic 协议**兼容客户端的事实标准(LMS / Funkwhale / Audiobookshelf / Ampache 也走 Subsonic)。Phase A ship 双重验证:

1. **本地音乐客户端首次接入**(LX / YesPlayMusic 是「远程 API → 本地」,Navidrome 是「本地音乐库 + 远程控制」双向)
3. **借鉴原则 5「Token≠密码」首次具体范例** — Subsonic v1.13.0+ 强制 salted token(`token = md5(password + salt)`),每次请求客户端派新 salt + 服务端用本次 salt 算期望 token 比对。

**关键价值**:Subsonic 协议本身就是个「教科书级」鉴权设计 — 截获单次 token 不能跨端点重放(因为下次 salt 是新的,服务端重算),且服务端永远**不存**用户密码哈希(只验证 `md5(pass+salt)` 这种一次性 token)。这远比「一次鉴权拿长 token」的 OAuth 设计更轻量、更适合内网服务。

## 设计取舍

**严格只读 + 借鉴原则 1「失败语义优先」**:Subsonic 三层失败路径:
- HTTP 层(不可达 / timeout / 4xx / 5xx)→ `{ ok: false, alive: false }`
- Subsonic envelope 缺失(非 JSON / 无 `subsonic-response` 根)→ `{ ok: false, alive: false, last_error: 'missing root envelope' }`
- Subsonic `status=failed` + `error.code/message` → `{ ok: false, alive: true(服务端活着), last_error: 'code=N msg=...' }`

**鉴权三参**:Subsonic v1.13.0+ 强制三参数(`u` + `t` + `s`)。`v` API 版本必须 major 一致且 client minor ≤ server minor。`c` client 名是『prisirai』(标识身份,便于服务端日志)。

**鉴权 5 字段 env**:
- `PRISIR_NAVIDROME_URL` 默认 `http://127.0.0.1:4533`(Navidrome 默认端口)
- `PRISIR_NAVIDROME_USER` 默认空 → 无 creds 走早退路径
- `PRISIR_NAVIDROME_PASS` 默认空 → 走 no-credentials 错误
- `PRISIR_NAVIDROME_TOKEN` 可选 — 用户预派生 token 跳过明文密码
- `PRISIR_NAVIDROME_CLIENT` 默认 `prisirai`
- `PRISIR_NAVIDROME_API_V` 默认 `1.16.1`

**借鉴原则 3「SQLite readonly」HTTP 类同款**:Navidrome 的 `users` 表有 `password` 字段存明文(加密算法可逆),**绝不**读这个表 — Phase A 只读 `getNowPlaying / getLicense / ping` 三 endpoint,绝不触碰任何 `jukeboxControl / set / create / update / delete` 写接口,绝不调 `getDownload / stream / scrobble`。

**3 个 L0 命令**:
- `navidrome.health` — `/ping` 探活 + 延迟
- `navidrome.now-playing` — `/getNowPlaying` 全用户当前播放流,每条 entry 含 `username/title/artist/album/year/minutesAgo/playerId`
- `navidrome.license` — `/getLicense` + serverVersion(纯探活 + 版本信息)

## 实现

**Node 端 index.js (~285 行)**:无 npm 依赖,纯 `http.get` + `crypto.md5` + JSON 解析。

- `randomSalt(n)`:`crypto.randomBytes(n).toString('hex')`,12 字节 = 24 字符 hex
- `md5Hex(s)`:`crypto.createHash('md5').update(s).digest('hex')`
- `makeToken(password)`:生成新盐 → 派生 token → 返 `{ salt, token }`
- `httpGetSubsonic({password, preToken, preSalt, endpoint})`:鉴权 + GET + 解析
- `parseSubsonic(r)`:三层失败语义检测(空 envelope / status=failed / status≠ok)
- `probeHealth() / fetchNowPlaying() / fetchLicense()`:三 L0 命令实现

**关键 Subsonic 协议细节**:
- URL:`{base}/rest/{endpoint}?u=user&t=token&s=salt&v=1.16.1&c=prisirai&f=json`
- `f=json` 强制 JSON(默认 XML)
- 响应:`{'subsonic-response': {status: 'ok'/'failed', ...data, error: {code, message}}}`
- `nowPlaying.entry` 可能是 array(多人同时播)或单 object(只有一人)— 代码两层兜底

## 测试

**Node 端**(`__tests__/run.js`):8 单测 + 4 E2E,**E2E 起临时 Node http.createServer mock Subsonic server**:

```
✓ ndBaseUrl 默认 + env override 切
✓ makeToken → md5(password + salt)
✓ fetchNowPlaying 无 creds → ok=false + last_error 含 no credentials
✓ probeHealth 无 creds → alive=false + last_error
✓ httpGetSubsonic 不可达 → ok=false + status=0
✓ parseSubsonic status=failed → ok=false + code 40
✓ parseSubsonic missing root envelope → ok=false
✓ fetchLicense 无 creds → ok=false + last_error

[E2E] Mock Subsonic server @ 127.0.0.1:<random>
  demo creds: user=admin pass=demo (mock server 用每次客户端发的 salt 算 md5(pass+salt) 验 token)
✓ E2E probeHealth → alive + latency < 500ms
✓ E2E fetchNowPlaying → 1 entry (Apocalypse / twenty one pilots)
✓ E2E fetchLicense → valid=false + serverVersion=0.54.5
✓ E2E 错密码 → ok=false + last_error 含 Wrong username

[结果] ✓ 12  ✗ 0
```

**E2E mock server 关键设计**:**完全按真实 Subsonic 协议** — mock 不写死盐值,而是用客户端每次请求发的 salt 算 `md5(pass+salt)` 比对。这才能真正验证客户端的 salted token 派生是对的(硬盐会掩盖客户端逻辑 bug)。这是 v2 对比研究借鉴的「Token≠密码」首次端到端验证。

**E2E 测试还包含**:错密码 → 服务端返 code=40 → 我们 last_error 含 `code=40 msg=Wrong username`。这是 Subsonic 服务端鉴权的典型失败路径。

**Python wrapper** (`tests/test_navidrome_bridge_status.py`):4 case,4/4 全过。

## 主仓回归

`python -m pytest tests/test_navidrome_bridge_status.py tests/test_yesplaymusic_bridge_status.py tests/test_calibre_metadata_indexer.py tests/test_siyuan_vault_indexer.py tests/test_lx_music_bridge_status.py`:

```
17 passed, 3 skipped in 8.28s
```

**5 个 Phase A 扩展联合回归全绿**(LX / YesPlayMusic / SiYuan / Calibre / Navidrome),17 实测 + 3 默认 skip(Navidrome E2E 加上跑)。

## LX 模板复用 vs Navidrome 差异表

| LX Phase A 元素 | Navidrome 改动 |
|---|---|
| 失败语义优先(`ok:false + alive:false + last_error`)| **升级为 3 层失败语义**(HTTP / envelope 缺失 / Subsonic.status=failed)|
| env 覆盖(`PRISIR_<NAME>_URL`)| **5 字段 env**(URL + USER + PASS + TOKEN + CLIENT + API_V)|
| Node http.get + JSON 解析 | Node http.get + `crypto.md5` 鉴权 + JSON 解析 |
| anonymous 访问 | **强制鉴权**(Subsonic v1.13.0+ salted token)|
| flat JSON 响应 | `{'subsonic-response': {status, ...}}` 包裹 + status=ok/failed 双值 |
| module.exports 测试钩子 | **0 修改** |
| Node 单测 + E2E + Python wrapper | **0 修改**(E2E mock server 实现按真实 Subsonic 协议) |
| SQLite readonly mode(N/A)| N/A |
| 端点路径 | 改 `/rest/ping /rest/getNowPlaying /rest/getLicense` |
| JSON 结构 | `{'subsonic-response': {status, ...}}` + 失败字段 `error.code/message` |
| 鉴权层 | 新增 `makeToken()` + `randomSalt()` + `md5Hex()`(Node crypto 内置)|

**结论**:HTTP Phase A 模板**新增一层「鉴权」(可选)**。任何走 token-based auth 的本地服务(Subsonic / Jellyfin / Plex / Emby / Komga / Audiobookshelf)套这套模板,**只需**改:
1. 鉴权协议实现(Subsonic 是 md5+token,Jellyfin 是 Authorization: MediaBrowser Token=,Komga 是 X-API-Key 等)
2. 端点路径 + JSON 解析
3. env 字段(从 1 字段 URL 升到 3-5 字段鉴权材料)

## 后续 Phase B/C 边界

**Phase B**(待用户开绿灯):播放控制 — `navidrome.jukeboxControl action=play/pause/stop/skip/setGain`,需 `state.write.nd` 权限。**Subsonic jukeboxControl 是音乐服务 mode** — 用户在 Navidrome 配置 `jukeboxMode = true` 才生效,Phase B 必须**逐条**勾。

**Phase C**(更远):与 LX / YesPlayMusic 跨音乐源协同(本地音乐 vs 流媒体统一元数据),基于已收藏 + 最近播放的本地启发式推荐。Subsonic 协议可扩展到 LMS / Funkwhale / Ampache / Audiobookshelf(Komga 是另一个协议)。

**Subsonic 协议 SDK 沉淀**(`extensions/_scaffold/subsonic-client.js`):未来 Audiobookshelf / LMS / Funkwhale / Ampache 都走 Subsonic,直接复用这次实现的鉴权 + httpGet + parseSubsonic 三件套。

## Why

Navidrome 桥的存在不是为了「多一个音乐客户端」,而是验证:

1. **Phase A 模板可扩展鉴权层** — 之前 LX/YPM 是 anonymous,这是第一个「强鉴权」组合
2. **Subsonic 协议 = 未来多个扩展**的母协议(Audiobookshelf / LMS / Funkwhale / Ampache / OpenSubsonic)
3. **借鉴原则 5「Token≠密码」首次端到端验证** — 模拟服务端用客户端发的盐重算 token 验证,确认客户端派生逻辑是对的

## How to apply

下次遇到「本地 HTTP API 软件」做 Phase A 且需要鉴权(Subsonic / Jellyfin / Plex / Emby / Komga / Audiobookshelf):
1. 端口确认 + endpoint 枚举 + 鉴权协议分析(token / API key / Basic auth)
2. 失败语义优先 + 鉴权失败的三层(HTTP / envelope / 服务端鉴权失败) + L0 权限
3. env 覆盖:**至少 3 字段**(URL + USER + CRED_FIELD),**绝不明文密码 commit**
4. E2E mock server 必须按真实鉴权协议模拟(否则测试假阳性)
5. L0 权限:`ai.invoke.command:<name>.<action>` + `ui.inject.notification`

**模板已固化到 2 种**:
- **anonymous**(LX / YesPlayMusic):1 字段 URL
- **鉴权**(Navidrome / 未来 Subsonic 系列):3-5 字段 URL + USER + PASS/TOKEN/KEY

下一步可立刻 ship **Audiobookshelf**(同样 Subsonic 协议 — Subsonic 这层 SDK 已沉淀,Audiobookshelf 是 5 分钟接入)。

相关:[[lx-music-bridge-phase-a-shipped]], [[siyuan-vault-indexer-phase-a-shipped]], [[calibre-metadata-indexer-phase-a-shipped]], [[yesplaymusic-bridge-status-phase-a-shipped]], [[lx-music-like-software-comparison]], [[p3-10-bubble-cancelled-privacy]]