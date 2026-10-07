---
name: immich-bridge-status-phase-a-shipped
description: Immich 照片库 Phase A ship — custom-auth SDK mode='apiKey' 第二个用户 + 跨入"照片"领域
metadata:
  type: project
---

# Immich 照片库 Phase A ship + custom-auth SDK mode='apiKey' 第二次复用(2026-10-07)

## 起因

继续 Phase C 协议 SDK 复用验证 — **Immich 是 custom-auth-client.js SDK 第四个用户**。Immich 用 `x-api-key`(小写)自定义鉴权头,SDK mode='apiKey',与 **Komga 的 X-API-Key(大写)同模式**。

**SDK 复用价值**:Komga 漫画 + Immich 照片两个完全不同的领域,SDK 模式 `apiKey` 复用成功,验证 SDK 接受 "任意 X-* / x-* API Key header"(大小写不敏感是 HTTP 标准)。

**Jellyfin 决策撤回**: Jellyfin 推荐用 `Authorization: MediaBrowser Token="..."` 头(X-Emby-Token 12.0 起被弃),SDK 当前不支持 `MediaBrowser scheme` 这种 "自定义 scheme 内嵌 token" 格式(与 Bearer 和 X-* 都不同)。Jellyfin 推迟到 SDK 增强或使用 deprecated X-Emby-Token 时再 ship。

## 设计要点

### Immich 扩展 `immich-bridge-status/index.js`(156 行)

mode='apiKey' 调用(与 Komga 几乎一样):
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

return makeConfig({
  baseUrl: process.env.PRISIR_IMMICH_URL || 'http://127.0.0.1:2283',
  mode: 'apiKey',
  key: process.env.PRISIR_IMMICH_API_KEY || '',
  timeoutMs: 5000,
});
```

3 个 L0 命令(纯只读):
- `immich.health` — `/api/server/version` 服务器版本 + 探活
- `immich.albums` — `/api/albums` 相册列表
- `immich.assets` — `/api/assets?take=20` 资产列表(图片/视频)

**绝不**触碰 POST `/api/assets`(上传)/ POST `/api/albums`(创建)/ DELETE 等 mutating 接口。

### Immich API 嵌套结构(双层)

Immich API 多次嵌套包装,扩展自己解析:
- `/api/albums` → `{ "albums": Album[] }` 或 `{ "albums": { "albums": Album[] } }` (v1.x 多次重构)
- `/api/assets` → `{ "assets": AssetResponse[], "total": N }` 或直接 array

测试用 `Array.isArray(top?.albums?.albums) ? top.albums.albums : top.albums : top` 三层 fallback,与 Plex 的 `r.parsed?.MediaContainer?.Directory` 类似。

### env 字段

- `PRISIR_IMMICH_URL` 默认 `http://127.0.0.1:2283`(Immich Docker 默认端口)
- `PRISIR_IMMICH_API_KEY` `x-api-key` 自定义 token

### feature tag 扩展

新增 `'media-library'` 标签 — 表明这是媒体库类型扩展(照片/视频内容),与笔记 / RSS / 漫画区别。

## 实现

### SDK 复用的代码

Immich `index.js` ~156 行,核心全部来自 SDK:
```js
const { makeConfig, httpGet, describeAuth } =
  require('../_scaffold/custom-auth-client');

const r = await httpGet({ config: cfg, path: '/api/server/version' });
```

**SDK 6 个 API 全零修改复用**:apiKeyHeader / basicAuthHeader / makeConfig / httpGet / describeAuth。

### 四扩展复用比(custom-auth SDK)

| 软件 | SDK mode | 自定义 header | 默认端口 |
|---|---|---|---|
| Komga | 'apiKey' | X-API-Key(大写) | 25600 |
| Komga fallback | 'basic' | Authorization: Basic | 25600 |
| Miniflux | 'custom' | X-Auth-Token | 8080 |
| Plex | 'custom' | X-Plex-Token | 32400 |
| **Immich** | **'apiKey'** | **x-api-key(小写)** | **2283** |

**4 个软件零 SDK 修改** — 证明 SDK "透传 mode + header 名" 设计跨 5 个真实软件稳定(漫画 + RSS + 影视 + 照片)。

## 测试结果

**Immich 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 11/11 ✓(7 单 + 4 E2E,覆盖 health/albums/assets + 嵌套解析 + 401)
```

E2E mock server 验:
- x-api-key 接受 + 401 拒绝
- Immich 三层嵌套结构(albums/albums/Album, albums/assets/Asset)fallback 解析

**Python wrapper**:
```
$ python -m pytest tests/test_immich_bridge_status.py
============================== 4 passed in 0.86s ==============================
```

**11 扩展联合回归**:
```
$ python -m pytest tests/test_{immich,plex,miniflux,komga,funkwhale,audiobookshelf,
                               navidrome,yesplaymusic,lx_music,
                               calibre,siyuan}_*.py
======================== 41 passed, 3 skipped in 6.85s ========================
```

3 个 skipped 仍是历史遗留。

## Phase A 模板固化:5 类(扩展家族增至 6 大领域)

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP | 局域 Open API | LX / YesPlayMusic | 自写 |
| 鉴权 salted token | Subsonic md5+Salt | Navidrome / Funkwhale | subsonic-client.js |
| 鉴权 Pure Bearer | Bearer over HTTPS | Audiobookshelf | 自写(SDK 待抽)|
| **自定义鉴权头** | API Key / X-Auth-Token / X-Plex-Token / **x-api-key** | **Komga / Miniflux / Plex / Immich** | **custom-auth-client.js** |
| SQLite readonly | node:sqlite | SiYuan / Calibre | 自写 |

**扩展家族跨域**: 音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + **照片**(7 个领域)。

## Why

Immich 是 **custom-auth SDK 第四次复用**(mode='apiKey' 第二次,与 Komga 同 mode 不同 header),验证 SDK 接受"任意 API Key header"的抽象在跨域复用真稳:

- Komga X-API-Key(漫画版)
- Immich x-api-key(**照片版**)

**SDK 大小写不敏感**(HTTP 标准):Komga 大写 / Immich 小写都接受。**SDK 5 大领域全覆盖**(音乐 + 笔记 + 媒体中心 + 漫画 + 影视 + 照片),Subsonic + custom-auth + Pure Bearer 自写 三个 SDK 都已就位。

## How to apply

下次做自定义鉴权头扩展(Jellyfin 推迟 / Emby / Kavita / Calibre-Web / FreshRSS):

1. 端口确认 + 鉴权头名(注意大小写 / 模式)
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

相关:[[komga-bridge-status-phase-a-shipped]], [[miniflux-bridge-status-phase-a-shipped]], [[plex-bridge-status-phase-a-shipped]], [[phase-c-subsonic-sdk-and-audiobookshelf-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]