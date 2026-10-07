---
name: komga-bridge-status-phase-a-shipped
description: Komga 漫画库 Phase A ship + custom-auth-client SDK 抽取(第三个 Phase C SDK)
metadata:
  type: project
---

# Komga 漫画库 Phase A ship + custom-auth-client SDK 抽取(2026-10-07)

## 起因

P1 候选 LX-like 调研中的 **Komga**(漫画/BD/Manga 自托管管理器)。继续 Phase C 协议抽象路线 — 抽取 **第二个 SDK**:`custom-auth-client.js`,覆盖 HTTP Basic Auth + 自定义鉴权头(`X-API-Key` / `X-Auth-Token`)两种模式。

为什么 Komga 不是 Bearer / 不是 salted Subsonic?
- Komga **不支持** Bearer token / JWT(2025-12 官方文档明确未实现)
- 鉴权仅:`X-API-Key`(自 1.12.0+)+ `Authorization: Basic`(经典 Basic Auth)
- 因此与 Audiobookshelf(Pure Bearer)/ Navidrome(Subsonic salted) 完全不同

**SDK 抽取价值**:
- Miniflux(RSS)用 `X-Auth-Token` —— 同 SDK
- Plex(影视)用 `X-Plex-Token` —— 同 SDK
- Jellyfin(影视)用 `X-Emby-Token` —— 同 SDK
- 自托管类 REST API 主流模式,**抽这个 SDK 比抽 Bearer 更通用**

## 设计要点

### SDK 三个鉴权模式(`extensions/_scaffold/custom-auth-client.js`,170 行)

```js
const { makeConfig, httpGet, apiKeyHeader, basicAuthHeader, describeAuth } =
  require('../_scaffold/custom-auth-client');

// mode='apiKey' →  X-API-Key: <key>
// mode='basic'  →  Authorization: Basic base64(user:pass)
// mode='custom' →  <customHeader>: <customToken>  (Miniflux / Plex / Jellyfin 用)
```

零 npm dep,只 Node 内置 http + Buffer(base64)。
**借鉴原则 5「Token≠密码」层次**新增第三档:
| 协议 | 实现 | 安全等级 | 例 |
|---|---|---|---|
| API Key 头 | 中(token 可重放直至撤销) | 7/10 | Komga / Miniflux / Plex |
| Basic Auth | 弱(base64 明文) | 4/10 (over HTTPS) / 1/10 (over HTTP) | Komga fallback / 部分 Jellyfin |
| X-Auth-Token | 中(token 可重放) | 7/10 | Miniflux |

### Komga 扩展 `komga-bridge-status/index.js`(190 行)

**`auto` auth_mode 解析**:
```js
const apiKey = process.env.PRISIR_KOMGA_API_KEY || '';
const user   = process.env.PRISIR_KOMGA_USER || '';
const explicit = (process.env.PRISIR_KOMGA_AUTH_MODE || '').toLowerCase();
let mode;
if (explicit === 'apikey' || explicit === 'api_key') mode = 'apiKey';
else if (explicit === 'basic') mode = 'basic';
else if (apiKey) mode = 'apiKey';   // auto:有 Key 优先
else if (user)   mode = 'basic';    // auto:无 Key 回退 basic
else mode = 'apiKey';                // 默认 — 让 SDK 早退返 no credentials
```

3 个 L0 命令(纯只读):
- `komga.health` — `/actuator/health` 探活(Komga 1.9.0+ 标准)
- `komga.libraries` — `/api/v1/libraries` LibraryDto list
- `komga.series` — `/api/v1/series?page=0&size=20&search=...` PageSeriesDto

**绝不**触碰 PATCH / POST / PUT / DELETE / bookmark / progress read+write。

### env 字段(独立命名空间)

- `PRISIR_KOMGA_URL` 默认 `http://127.0.0.1:25600`(Komga 1.x 默认端口,不是 8080)
- `PRISIR_KOMGA_AUTH_MODE` 默认 `auto`
- `PRISIR_KOMGA_API_KEY` — X-API-Key
- `PRISIR_KOMGA_USER` / `PRISIR_KOMGA_PASS` — Basic Auth

## 实现

### SDK 抽取路径

1. 借鉴 Subsonic SDK 提炼路径:把"鉴权 + httpGet + parse + describe"4 抽到 `_scaffold/custom-auth-client.js`
2. SDK 接受 `mode` 参数,扩展自己决定用哪种
3. 同一 SDK 在 Komga / 未来 Miniflux / Plex / Jellyfin 复用

### 鉴权模式抽象的难点

| 难点 | 决策 |
|---|---|
| 鉴权头构造因软件而异 | SDK 提供 `mode` 参数 + 工厂方法 `apiKeyHeader / basicAuthHeader`,扩展也可手动注入 `mode='custom'` |
| 无 creds vs 不可达 区分 | SDK 在 http.get 前先早退 `no credentials`,不可达由 ECONNREFUSED 区分 |
| HTTP 401 vs 404 区分 | SDK `ok = statusCode < 400`,401/403/404 都算 ok=false 由扩展决定 alive |
| HTTP Basic Auth over HTTP 风险 | 文档明文提醒:`HTTPS 强烈推荐`,本地局域网用 HTTP 仅自担风险 |

## 测试结果

**Komga 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 13/13 ✓(7 单 + 6 E2E,覆盖 API Key / Basic / 401 三路)
```

E2E mock server 同时支持两种鉴权:
- X-API-Key:`kmg_demo1234567890abcdef` 接受
- Basic Auth:`demo:demo` 接受
- 错误 Key → 401

**Python wrapper**:
```
$ python -m pytest tests/test_komga_bridge_status.py -v
test_js_syntax_parses PASSED
test_node_unit_tests_all_pass PASSED
test_node_e2e_all_pass PASSED
test_extension_register_commands_via_sdk PASSED
============================== 4 passed in 0.86s ==============================
```

**8 扩展联合回归**:
```
$ python -m pytest tests/test_{komga,funkwhale,audiobookshelf,
                               navidrome,yesplaymusic,lx_music,
                               calibre,siyuan}_*.py
======================== 29 passed, 3 skipped in 4.81s ========================
```

3 个 skipped 仍是历史遗留(LX / Calibre / SiYuan 各一个 e2e 用例需真实本地实例)。

## Phase A 模板固化:5 类

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP(1 字段 URL)| 局域网 Open API | LX / YesPlayMusic | 自写 |
| **鉴权 salted token**(5+ 字段)| Subsonic md5+Salt | Navidrome / Funkwhale | subsonic-client.js |
| **鉴权 Bearer**(3 字段)| Bearer over HTTPS | Audiobookshelf | 自写(SDK 待抽)|
| **自定义鉴权头**(API Key / X-Auth-Token)| 自定义 header | **Komga** / Miniflux / Plex | **custom-auth-client.js** ← 新增 |
| **SQLite readonly**(2 字段)| node:sqlite | SiYuan / Calibre | 自写 |

**关键洞察**:
- 同协议多软件可抽 SDK(Subsonic + custom-auth 都验证)
- 异协议独立封装(Bearer / SQLite / anonymous 各需独立 SDK)
- **抽取时机**:SDK 至少 2 个不同软件需要才抽,1 个不值得抽(过度工程)

## Why

Komga 是 **Phase C 协议 SDK 抽取的第三个落地**(前两个:Subsonic / 内部 SQLite helper)。这次抽取回答了关键设计问题:

1. **SDK 抽象粒度**:鉴权模式作为 SDK 参数透传,而不是 SDK 替扩展决定(SDK 接受 mode='apiKey'|'basic'|'custom')
2. **零 npm dep 持续**:http + Buffer(base64) + 内置 crypto(暂未用到)足够
3. **makeConfig factory 模式继续**:env 字段各扩展独立,SDK 不绑死
4. **HTTP Basic over HTTP 红线提示**:文档明确写"绝不 over HTTP 用",与 P3.10b 0 上传红线一致

Plex / Jellyfin / Miniflux 未来 ship 工作量降到 5 分钟,**Phase A 模板固化到 5 类**。

## How to apply

下次做自定义鉴权头扩展(Miniflux / Plex / Emby / Jellyfin / Miniflux):

1. 端口确认 + 鉴权模式(apiKey/basic/custom)
2. `require('../_scaffold/custom-auth-client')`
3. 写 env 字段(makeConfig 调用)
4. 写 L0 命令(SDK 直接复用,只填 path + mode)
6. 测三件套(Node 单测 + E2E mock server 同时支持两种方式 + Python wrapper)
7. 跑联合回归

下次做 Pure Bearer 软件(Kavita / Calibre-Web / Immich 自定义 / Outline):
- **优先抽 `bearer-client.js` SDK**(类似 custom-auth 但只 Pure Bearer 模式)
- Audiobookshelf 是首个 Pure Bearer 案例,代码可作模板

**Phase A 模板 = 单软件 ship 工作量降到最低**(2-3 天 → 5 分钟)。

相关:[[phase-c-subsonic-sdk-and-audiobookshelf-shipped]], [[funkwhale-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]