---
name: miniflux-bridge-status-phase-a-shipped
description: Miniflux RSS Phase A ship — custom-auth SDK mode='custom' 复用验证(零修改)
metadata:
  type: project
---

# Miniflux RSS Phase A ship + custom-auth SDK mode='custom' 验证(2026-10-07)

## 第三方项目

P2 候选 LX-like 调研中的 **Miniflux**(自托管 RSS reader,UI/API 分离)。本次 ship 是 Komga 物理真验证后立即复用 — 验证 custom-auth SDK 三模式透传设计真实落地:

- mode='apiKey' → X-API-Key: <key> (Komga,已 ship)
- mode='basic'  → Authorization: Basic (Komga fallback,已 ship)
- mode='custom' → <header>: <token> (**Miniflux X-Auth-Token**,本轮 ship) ← 首次复用

**关键问题回答**: SDK 是否真的接受 "任意自定义鉴权头" 作为参数?本次 ship 验证: **是**。Miniflux SDK 代码与 Komga 完全相同,只调 `customHeader: 'X-Auth-Token'`。

## 设计要点

### Miniflux 扩展 `miniflux-bridge-status/index.js`(148 行)

模式 `'custom'` 调用:
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

return makeConfig({
  baseUrl: process.env.PRISIR_MINIFLUX_URL || 'http://127.0.0.1:8080',
  mode: 'custom',                                      // ← X-Auth-Token 选这个
  customHeader: 'X-Auth-Token',
  customToken: process.env.PRISIR_MINIFLUX_TOKEN || '',
  timeoutMs: 5000,
});
```

**Miniflux 与 Komga 对比**(都用 custom-auth SDK):

| 字段 | Komga | Miniflux |
|---|---|---|
| 默认端口 | 25600 | 8080 |
| 鉴权模式 | 'apiKey' + 'basic' fallback | 'custom' 锁定 |
| SDK 调用行数 | ~10 | ~10 |
| SDK 改动 | 0 | 0 |
| index.js 行数 | 190 | 148 |

**SDK 透传 mode 设计被复用验证**:2 个扩展只改 mode 参数与扩展自己的 env 字段名,SDK 本身零修改。

### 3 个 L0 命令(纯只读)

- `miniflux.health` — `/v1/me` 用户身份 + 探活
- `miniflux.feeds` — `/v1/feeds` 订阅源列表
- `miniflux.entries` — `/v1/entries?status=unread&limit=20&search=...` 条目列表

**绝不**触碰 `/v1/entries/[id]/bookmark` / `/v1/entries/[id]/unread` / `/feeds` CRUD 等 mutating 接口。

### env 字段(独立命名空间)

- `PRISIR_MINIFLUX_URL` 默认 `http://127.0.0.1:8080`
- `PRISIR_MINIFLUX_TOKEN` X-Auth-Token token

## 实现

### SDK 复用的代码

Miniflux `index.js` 只 ~148 行,核心全部来自 SDK:
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

const r = await httpGet({ config: cfg, path: '/v1/me' });
```

**SDK 6 个 API 全零修改复用**:apiKeyHeader / basicAuthHeader / makeConfig / httpGet / describeAuth。

### Komga vs Miniflux 复用比

两个扩展都 `require('../_scaffold/custom-auth-client')` — SDK 自身:
- 没加新函数
- 没改任何签名
- 没改任何 mock 测试

**唯一差异是 mode 参数**:`'apiKey'` / `'custom'`。

## 测试结果

**Miniflux 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 12/12 ✓(7 单 + 5 E2E,覆盖 health/feeds/entries/status filter/search filter)
```

E2E mock server 验 `X-Auth-Token` 自定义头 + 401 错 token + 多 status 过滤。

**Python wrapper**:
```
$ python -m pytest tests/test_miniflux_bridge_status.py
============================== 4 passed in 0.86s ==============================
```

**9 扩展联合回归**:
```
$ python -m pytest tests/test_{miniflux,komga,funkwhale,audiobookshelf,
                               navidrome,yesplaymusic,lx_music,
                               calibre,siyuan}_*.py
======================== 33 passed, 3 skipped in 6.36s ========================
```

3 个 skipped 仍是历史遗留(LX / Calibre / SiYuan 各一个 e2e 用例需真实本地实例)。

## Phase A 模板固化:5 类

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP(1 字段 URL)| 局域网 Open API | LX / YesPlayMusic | 自写 |
| 鉴权 salted token(5+ 字段)| Subsonic md5+Salt | Navidrome / Funkwhale | subsonic-client.js |
| 鉴权 Pure Bearer(3 字段)| Bearer over HTTPS | Audiobookshelf | 自写(SDK 待抽)|
| **自定义鉴权头**(API Key / X-Auth-Token)| 自定义 header | **Komga / Miniflux** | **custom-auth-client.js** ← 二次复用 |
| SQLite readonly(2 字段)| node:sqlite | SiYuan / Calibre | 自写 |

**关键洞察**: custom-auth SDK 从 "刚 ship 第一个用户" 到 "当天就 ship 第二个用户",**验证 SDK 设计对'任意自定义鉴权头'的兼容性**。

## Why

Miniflux 验证的是 Phase C 协议抽象的 **可扩展性** — SDK 是否真的设计成 "透传 mode" 而非 "考虑扩展"。

Komga ship 时(三模式被 framework 调) 已经验证 SDK 是 "透传 mode"。但当时只用了 1 个 mode('apiKey')+ 1 个 fallback('basic')。Miniflux 用 'custom' mode 是 SDK 接受 **任意 header 名 + token 值** 的硬验证。

如果 SDK 是 "考虑 Komga 模式" 就会 hardcode 'X-API-Key' 或 'Authorization: Basic',无法扩展 Miniflux 的 X-Auth-Token。**SDK 这次复用成功证明三模式抽象是真"透传"**。

## How to apply

下次做自定义鉴权头扩展(Plex / Jellyfin / Emby / Kavita / Calibre-Web):

1. 端口确认 + 鉴权头名(X-Plex-Token / X-Emby-Token / Authorization Bearer / X-Auth-Token)
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

相关:[[komga-bridge-status-phase-a-shipped]], [[phase-c-subsonic-sdk-and-audiobookshelf-shipped]], [[funkwhale-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]