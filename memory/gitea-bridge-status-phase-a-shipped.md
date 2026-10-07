---
name: gitea-bridge-status-phase-a-shipped
description: Gitea 代码托管 Phase A ship + bearer-client.js SDK 抽取 — 第 4 个 SDK 诞生
metadata:
  type: project
---

# Gitea 代码托管 Phase A ship + bearer-client.js SDK 抽取(2026-10-07)

## 起因

继续 Phase A 模板跨域验证 — **Gitea 是 Pure Bearer 模式第三个用户**,触发 bearer-client.js SDK 抽取。

**两次决策回退**:
1. **Calibre-Web 回退**:标准 Calibre-Web 无 REST API(只有 OPDS Atom feed + Basic Auth),Phase A 走 Basic + XML 解析成本高,改 ship 目标
2. **Emby 回退**:Emby 用 `Authorization: MediaBrowser Client=..., Token=...` 多键值 header(非标准 Bearer),虽 X-Emby-Token 单键向后兼容但语义模糊,改 Pure Bearer 真正候选 Gitea

**Pure Bearer 真正定义**:`Authorization: Bearer <token>`(RFC 6750)。三用户触发 SDK 抽取:Audiobookshelf + Kavita + **Gitea**。

## 设计要点

### Gitea 扩展 `gitea-bridge-status/index.js`(140 行)

环境变量:
- `PRISIR_GITEA_URL` 默认 `http://127.0.0.1:3000`(Gitea Docker 默认)
- `PRISIR_GITEA_TOKEN` Bearer access token(Gitea User Settings → Applications)

3 个 L0 命令(纯只读):
- `gitea.health` — `/api/v1/version` 公开探活(无需 Bearer)
- `gitea.repos` — `/api/v1/repos/search?limit=N&q=X` 仓库列表/搜索
- `gitea.orgs` — `/api/v1/orgs` 当前用户组织列表

**绝不**触碰 POST `/api/v1/user/repos` / DELETE `/api/v1/repos/{owner}/{repo}` / star/follow 等 mutating 接口。

### Gitea API JSON 嵌套

`/api/v1/repos/search` 返 `{ok: bool, data: Repo[], total_count: N}` 三层,扩展解析:
```js
const data = Array.isArray(top.data) ? top.data : (Array.isArray(top) ? top : []);
```

### bearer-client.js SDK(第 4 个 SDK,150 行)

`extensions/_scaffold/bearer-client.js`,4 个 API:
```js
bearerHeader(token)         → { Authorization: 'Bearer <token>' } 或 {}
makeConfig({baseUrl, token, timeoutMs?}) → 不可变 config(与 custom-auth SDK 风格一致)
httpGet({config, path})     → Promise<{ok, status, body, parsed, url, error}>
describeAuth(config)        → { mode: 'bearer', has_token, base_url }
```

**与 custom-auth-client.js 区别**:
- custom-auth: 自定义 header 名(X-API-Key / X-Auth-Token / X-Plex-Token / x-api-key)
- bearer: **标准 Bearer scheme**(header 名固定 Authorization)

**SDK 失败语义对齐**:三层(缺 token / 网络错 / HTTP 4xx5xx),与 custom-auth 一致,Gitea L0 命令直接透 `http_status`。

## 实现

### SDK 抽取前的代码重复

抽 SDK 前 Audiobookshelf(35 行) + Kavita(50 行) inline Bearer helper 重复。Audiobookshelf 单案例不够 SDK 阈值,Kavita 第 2 个临界但 inline 可读,Gitea 第 3 个立即触发抽取。

**SDK 抽取阈值**:3+ 用户,与 custom-auth SDK 一致。

### 失败分支 http_status 教训继承

Kavita E2E 抓到的"失败分支漏 http_status" bug 已 ship 进 muscle memory,Gitea 所有 L0 命令失败分支必带 `http_status: r.status`。E2E 一次跑通(12/12 全绿)。

## 测试结果

**Gitea 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 12/12 ✓(7 单 + 5 E2E,覆盖 health/repos/orgs + search 过滤 + 401)
```

E2E mock server 验:
- Bearer token 接受 + 401 拒绝
- `/api/v1/version` 公开路径无需 token
- `/api/v1/repos/search?q=fork` 过滤(只返 1 fork repo)
- `/api/v1/orgs` 返 2 org(public+private)

**Python wrapper**:
```
$ python -m pytest tests/test_gitea_bridge_status.py
============================== 4 passed in 0.93s ==============================
```

**12 扩展联合回归**:
```
======================== 46 passed, 2 skipped in 7.54s ========================
```

(+4 vs Kavita ship 42 passed)

## Phase A 模板跨域矩阵更新(4 SDK)

| SDK | 用户 | 模式 |
|---|---|---|
| subsonic-client.js | Navidrome / Funkwhale / LMS(3) | 鉴权 salted token |
| **bearer-client.js** | **Audiobookshelf / Kavita / Gitea(3)** | **标准 Bearer RFC 6750** |
| custom-auth-client.js | Komga / Miniflux / Plex / Immich(4) | 自定义鉴权头 |
| SQLite 自写 | SiYuan / Calibre(2) | node:sqlite readonly |
| anonymous 自写 | LX / YesPlayMusic(2) | 局域 Open API |

**扩展家族跨域**:音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + 照片 + 书库 + **代码托管**(9 个领域)。

## SDK 设计哲学:双模式(custom-auth vs bearer)

| 维度 | custom-auth SDK | bearer SDK |
|---|---|---|
| header 名 | 自定义(X-API-Key 等) | 固定 Authorization |
| scheme | 无 | Bearer(标准) |
| 用户类型 | API key, plugin token | OAuth-style access token |
| 跨平台兼容 | 软件私有不通用 | RFC 6750 标准 |
| 借鉴原则 5 评分 | 5-7/10(取决于 token 来源) | 6/10(OAuth 框架) |

**SDK 边界清晰**:
- 软件用 `X-` 前缀的自定义头 → custom-auth
- 软件用 `Authorization: Bearer` 标准 scheme → bearer
- 软件用 Basic Auth → custom-auth mode='basic'(复用)
- 软件用纯匿名 / 局域 open API → 自写 inline

## Why

Gitea 是 **bearer SDK 第三个用户触发抽取**(前 Audiobookshelf + Kavita inline),验证:

1. **Pure Bearer 模式 3 用户**够 SDK 化阈值
2. **SDK 抽取后,Gitea 扩展代码薄到 140 行**(从零写需要 ~200 行)
3. **SDK 标准 Bearer 与 custom-auth 自定义 header 并存**,不互相污染

**SDK 抽取**让未来 ship 同模式扩展(Gitea 是代码托管,Nextcloud 是云盘 / GitLab 类似 / Drone CI / Outline / Plausible 等)直接 SDK 复用,工作量降到 5 分钟。

## How to apply

下次做 Pure Bearer 扩展(Nextcloud / GitLab CE / Forgejo / Outline / Plausible / Drone CI / Woodpecker CI):

1. 端口确认 + token 来源(用户在服务端生成 Access Token)
2. `require('../_scaffold/bearer-client')`
3. 写 `gtConfig()` 调用 makeConfig
4. 写 L0 命令,SDK `httpGet` 直接复用 + 失败分支带 http_status
5. 测三件套(Node 单测 + E2E mock server + Python wrapper)
6. 跑联合回归

工作量:**140 行 index.js**。无 SDK 修改。

下次做需要 **MediaBrowser Token 多键值**(Emby)或 **Opds + Basic Auth + XML**(Calibre-Web / FreshRSS)的扩展:SDK 边界不足,需要 SDK 增强或 inline helper。

相关:[[audiobookshelf-bridge-status-phase-a-shipped]], [[kavita-bridge-status-phase-a-shipped]], [[komga-bridge-status-phase-a-shipped]], [[miniflux-bridge-status-phase-a-shipped]], [[plex-bridge-status-phase-a-shipped]], [[immich-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]]
