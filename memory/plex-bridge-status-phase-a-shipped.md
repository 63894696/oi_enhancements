---
name: plex-bridge-status-phase-a-shipped
description: Plex 媒体服务器 Phase A ship — custom-auth SDK mode='custom' 第三个用户 + 跨入"影视"领域
metadata:
  type: project
---

# Plex 媒体服务器 Phase A ship + custom-auth SDK 第三个用户(2026-10-07)

## 起因

继续 Phase C 协议 SDK 复用验证 — **Plex 是 custom-auth-client.js SDK 第三个用户**(前 Miniflux + Komga),与 Miniflux 同 mode='custom',但鉴权头是 `X-Plex-Token`(Plex 自有)。

**为什么 Plex 不是 Bearer?** Plex 用 `X-Plex-Token` 自定义头(支持 header 或 query param),不是标准 `Authorization: Bearer`。Token 也分两代:
- 传统老长凌 token(Preferences.xml 持久化)
- 2025-09 起的 7 天 JWT(Ed25519 device flow)

Phase A 默认接受老 token,无需 JWT flow。

**SDK 复用价值**:
- Plex / Miniflux / Komga / Miniflux 共用 custom-auth SDK,**零修改**
- 扩展家族跨领域:RSS(消息流) → 影视(媒体流)
- 同一 SDK 抽象下,"消息类媒体"与"影视类媒体"统一表达

## 设计要点

### Plex 扩展 `plex-bridge-status/index.js`(154 行)

模式 `'custom'` 调用 + X-Plex-Token:
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

return makeConfig({
  baseUrl: process.env.PRISIR_PLEX_URL || 'http://127.0.0.1:32400',
  mode: 'custom',
  customHeader: 'X-Plex-Token',
  customToken: process.env.PRISIR_PLEX_TOKEN || '',
  timeoutMs: 5000,
});
```

### Plex MediaContainer 嵌套结构处理

Plex API 返回结构 `{ "MediaContainer": { "Directory": [...] } }` — 与 Subsonic 的 `subsonic-response` 嵌套类似,SDK parseSubsonic 已处理。Plex 扩展**自己处理 MediaContainer**(因为不是 Subsonic 协议),需小心嵌套路径 `r.parsed?.MediaContainer?.Directory`。

3 个 L0 命令(纯只读):
- `plex.health` — `/identity` Plex 服务器元信息(version / machineIdentifier)
- `plex.libraries` — `/library/sections` 媒体库列表(Directory[])
- `plex.recent` — `/library/recentlyAdded` 最近添加(Metadata[])

**绝不**触碰:
- `/status/sessions`(会话)— 写入风险高
- `/library/sections/{key}/refresh` / `force=1`(扫描)— mutating
- `/:/playback/*`(播放控制)— mutating
- `/scrobble/*`(标记已看)— mutating

### env 字段

- `PRISIR_PLEX_URL` 默认 `http://127.0.0.1:32400`(PMS 默认端口)
- `PRISIR_PLEX_TOKEN` X-Plex-Token(Preferences.xml 中 `PlexOnlineToken`)

### feature tag 扩展

新增 `'media-server'` 标签 — 与 `'custom-header-auth'` 并列,表明这是媒体服务器类型扩展(与笔记/RSS/漫画区别)。

## 实现

### SDK 复用的代码

Plex `index.js` ~154 行,核心全部来自 SDK:
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

const r = await httpGet({ config: cfg, path: '/identity' });
```

**SDK 6 个 API 全零修改复用**:apiKeyHeader / basicAuthHeader / makeConfig / httpGet / describeAuth。

### 三扩展复用比(custom-auth SDK)

| 软件 | SDK mode | 自定义 header | 默认端口 |
|---|---|---|---|
| Komga | 'apiKey' | X-API-Key | 25600 |
| Komga fallback | 'basic' | Authorization: Basic | 25600 |
| Miniflux | 'custom' | X-Auth-Token | 8080 |
| **Plex** | **'custom'** | **X-Plex-Token** | **32400** |

**3 个软件零 SDK 修改** — 证明 SDK "透传 mode + customHeader + customToken" 设计真"透传"。

### MediaContainer 嵌套 vs Subsonic nested envelope

| 软件 | 顶层 | 内部 envelope |
|---|---|---|
| Subsonic | `subsonic-response` | `status` / `error.code` |
| Plex | `MediaContainer` | `size` / `Directory[]` / `Metadata[]` |
| Audiobookshelf | direct array | 无嵌套 |

3 种 envelope 风格,SDK 各管各的(Plex 解析非 Subsonic,所以不抽到 SDK)。

## 测试结果

**Plex 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 11/11 ✓(7 单 + 4 E2E,覆盖 health/libraries/recent + MediaContainer 嵌套 + 401)
```

E2E mock server 验:
- X-Plex-Token 接受 + 401 拒绝
- MediaContainer 嵌套(Directory[] / Metadata[])字段映射

**Python wrapper**:
```
$ python -m pytest tests/test_plex_bridge_status.py
============================== 4 passed in 0.86s ==============================
```

**10 扩展联合回归**:
```
$ python -m pytest tests/test_{plex,miniflux,komga,funkwhale,audiobookshelf,
                               navidrome,yesplaymusic,lx_music,
                               calibre,siyuan}_*.py
======================== 37 passed, 3 skipped in 6.71s ========================
```

3 个 skipped 仍是历史遗留。

## Phase A 模板固化:5 类(扩展家族增至 4 大领域)

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP | 局域 Open API | LX / YesPlayMusic | 自写 |
| 鉴权 salted token | Subsonic md5+Salt | Navidrome / Funkwhale | subsonic-client.js |
| 鉴权 Pure Bearer | Bearer over HTTPS | Audiobookshelf | 自写(SDK 待抽)|
| **自定义鉴权头** | API Key / X-Auth-Token / **X-Plex-Token** | **Komga / Miniflux / Plex** | **custom-auth-client.js** |
| SQLite readonly | node:sqlite | SiYuan / Calibre | 自写 |

**扩展家族跨域**: 音乐 + 笔记 + 媒体中心 + 漫画 + RSS + **影视**。

## Why

Plex 是 **custom-auth SDK 第三次复用**(mode='custom' 第二个,与 Miniflux 同 mode 不同 header),验证 SDK 接受 "任意自定义鉴权头" 的抽象在更多真实软件上稳定:
- Miniflux X-Auth-Token(消息类)
- Plex X-Plex-Token(**影视类**)
- Komga X-API-Key / X-Basic(漫画类,回退 basic auth)

**SDK 4 大领域全覆盖**:消息 / 漫画 / 影视(全部经 X-* 自定义头协议)。

如果 SDK 是 "考虑 Komga 模式" 就会 hardcode 'X-API-Key' 或 'Authorization: Basic',无法扩展 Miniflux/Plex 的 X-Auth-Token/X-Plex-Token。**SDK 这次跨域复用成功证明扩展性真稳**。

## How to apply

下次做自定义鉴权头扩展(Jellyfin / Emby / Kavita / Calibre-Web / Immich):

1. 端口确认 + 鉴权头名
2. `require('../_scaffold/custom-auth-client')`
3. 写 env 字段(makeConfig 调用)
4. 写 L0 命令(SDK 直接复用,只填 path + mode + customHeader/customToken)
5. 测三件套(Node 单测 + E2E mock server + Python wrapper)
6. 跑联合回归

每次 ~5 分钟,无 SDK 修改。

下次做 Pure Bearer 软件:
1. **优先抽 `bearer-client.js` SDK**(类似 custom-auth 但只 Pure Bearer 模式)
2. Audiobookshelf 是首个 Pure Bearer 案例,代码可作模板

**Phase A 模板 = 单软件 ship 工作量降到最低**(2-3 天 → 5 分钟)。

相关:[[komga-bridge-status-phase-a-shipped]], [[miniflux-bridge-status-phase-a-shipped]], [[phase-c-subsonic-sdk-and-audiobookshelf-shipped]], [[funkwhale-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]