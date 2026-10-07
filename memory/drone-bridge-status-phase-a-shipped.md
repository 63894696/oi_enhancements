---
name: drone-bridge-status-phase-a-shipped
description: Drone CI 持续集成 Phase A ship — bearer SDK 跨域验证(媒体 → CI/CD)
metadata:
  type: project
---

# Drone CI 持续集成 Phase A ship — bearer SDK 跨域验证(2026-10-07)

## 起因

继续 Phase A 模板跨域验证 — **Drone CI 是 bearer-client.js SDK 第四个用户**,**首次跨域**(媒体 → CI/CD)。

**GitLab CE 决策回退**:GitLab 默认 `PRIVATE-TOKEN: <pat>`(自定义 header),OAuth 才是标准 Bearer。Phase A 走 PRIVATE-TOKEN 即 custom-auth 模式(SDK 复用但不是验证 bearer 跨域);OAuth dance 复杂。改 ship Drone CI —— 标准 Bearer `Authorization: Bearer <user-token>`。

## 设计要点

### Drone CI 扩展 `drone-bridge-status/index.js`(140 行)

环境变量:
- `PRISIR_DRONE_URL` 默认 `http://127.0.0.1:8080`(Drone Docker 默认)
- `PRISIR_DRONE_TOKEN` Bearer user token(Drone User Profile → User Token)

3 个 L0 命令(纯只读):
- `drone.health` — `/api/user` 当前用户信息(鉴权 + 探活合并)
- `drone.repos` — `/api/user/repos` 当前用户仓库列表
- `drone.recent_builds` — `/api/user/feed?limit=N` 最近 build 列表

**绝不**触碰 POST `/api/repos` 创建仓库 / POST `/api/builds` 触发构建 / DELETE 任何 mutating。

### Drone API JSON 嵌套

`/api/user/repos` 返回 Repo[],每个 Repo 含 `last_build: {number, status, event}` 嵌套对象。`/api/user/feed` 返回 Activity[],每个含 `build + repo` 双层。扩展都按 SDK 风格解构,无 SDK 修改。

## 实现

### SDK 复用验证(跨域)

Drone CI 与 Gitea 都是代码托管,但领域不同:**Gitea = 代码托管数据,Drone CI = 构建运行数据**。同一个 SDK(`bearer-client.js`)在不同领域的扩展都直接 `require` 复用,**零修改**。

**单测用例 6** 直接 require Gitea 已 ship 的 SDK 文件,验证 SDK 跨扩展共享:
```js
const sdk = require('../../_scaffold/bearer-client.js');  // 同一个 SDK 文件
const h = sdk.bearerHeader('shared-token');
assertEq(h, { Authorization: 'Bearer shared-token' });
```

**这正是 SDK 抽取的核心价值**:一次抽取,跨域复用,扩展代码降到 140 行。

### 失败分支 http_status 教训继承

继 Kavita E2E 抓 bug 后,Drone CI 直接 ship-time 全绿:**所有 L0 命令失败分支带 `http_status: r.status`**。`fetchRepos` / `fetchRecentBuilds` 失败分支均含 http_status,无需 E2E 二修。

## 测试结果

**Drone CI 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 11/11 ✓(7 单 + 4 E2E,覆盖 user/repos/feed + 401)
```

E2E mock server 验:
- Bearer token 接受 + 401 拒绝
- `/api/user` 鉴权 + 探活合并端点返 admin=true
- `/api/user/repos` 返 3 repo(active/private/archived)
- `/api/user/feed?limit=50` 返 3 build(success/failure/running)

**Python wrapper**:
```
$ python -m pytest tests/test_drone_bridge_status.py
============================== 4 passed in 0.95s ==============================
```

**13 扩展联合回归**:
```
======================== 50 passed, 2 skipped in 8.00s ========================
```

## Phase A 模板跨域矩阵(4 SDK,5 协议)

| SDK | 用户 | 模式 |
|---|---|---|
| subsonic-client.js | 3(Navidrome/Funkwhale/LMS) | 鉴权 salted token |
| **bearer-client.js** | **4(Audiobookshelf/Kavita/Gitea/Drone CI)** | **RFC 6750 Bearer** |
| custom-auth-client.js | 4(Komga/Miniflux/Plex/Immich) | 自定义鉴权头 |
| SQLite 自写 | 2(SiYuan/Calibre) | node:sqlite readonly |
| anonymous 自写 | 2(LX/YesPlayMusic) | 局域 Open API |

**扩展家族跨域**:音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + 照片 + 书库 + 代码托管 + **CI/CD**(10 个领域)。

## SDK 复用经济性

| 维度 | 抽 SDK 前 | 抽 SDK 后 |
|---|---|---|
| 单扩展代码 | ~200 行 | 140-160 行 |
| Bearer helper | 35-50 行/inline | SDK 复用 0 行 |
| 失败语义对齐 | 各自实现 | SDK 三层统一 |
| 跨域复用 | 0 | 4 用户 |

**Drone CI 是首次跨域(媒体 → CI/CD)** 真复用 —— 证明 SDK 不只在相似协议奏效,在完全不同领域(从"数据查询"到"事件流")也通用。

## Why

Drone CI 是 **bearer SDK 第四个用户**,但更重要的是**首个跨域**(从媒体库到 CI/CD)。SDK 抽取的设计哲学「透明传 mode + 鉴权头 + 失败语义」在跨域时也成立。

**SDK 抽取继续阈值**:5+ 用户(下次累计)。

## How to apply

下次做 Pure Bearer 扩展(Nextcloud / Outline / Plausible / Woodpecker CI / Forgejo / GitLab OAuth):

1. 端口确认 + token 来源
2. `require('../_scaffold/bearer-client')` —— **零修改**
3. 写 `drConfig()` 调用 makeConfig
4. 写 L0 命令,失败分支带 `http_status: r.status`(Kavita 教训)
5. 测三件套(Node 单测 + E2E mock server + Python wrapper)
6. 跑联合回归

工作量:**140 行 index.js**。

下次做需要 PRIVATE-TOKEN 模式(GitLab PAT)走 custom-auth SDK `mode='custom' + customHeader='PRIVATE-TOKEN'`,不抽新 SDK(SDK 边界清晰)。

相关:[[gitea-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[kavita-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]]
