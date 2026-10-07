---
name: nextcloud-bridge-status-phase-a-shipped
description: Nextcloud 云盘 Phase A ship — bearer SDK 第 6 用户 + OCS extraHeaders inline + SDK 边界决策复用验证
metadata:
  type: project
---

# Nextcloud 云盘 Phase A ship — bearer SDK 第 6 用户(2026-10-07)

## 起因

继续 Phase A 模板跨域验证 — **Nextcloud 是 bearer-client.js SDK 第六个用户**,累计 6 用户。

**OAuth dance 简化**:Nextcloud 完整 OAuth2 flow 是用户注册 app + 走 `/apps/oauth2/authorize` 授权码流程,**交互式不适合 Phase A**(Phase A 是 100% 本地 + 只读)。Phase A 简化:用户手填已签发的 access token 到 `PRISIR_NEXTCLOUD_TOKEN` env,**不**在 Phase A 跑 OAuth dance(OAuth dance 留给后续 Phase B 交互式工作流)。

## 设计要点

### Nextcloud 扩展 `nextcloud-bridge-status/index.js`(180 行)

环境变量:
- `PRISIR_NEXTCLOUD_URL` 默认 `http://127.0.0.1:80`(Nextcloud 默认 HTTP)
- `PRISIR_NEXTCLOUD_TOKEN` Bearer OAuth2 access token(用户手填)

3 个 L0 命令(纯只读):
- `nextcloud.health` — GET `/ocs/v1.php/cloud/users`(鉴权 + 探活合并)
- `nextcloud.users` — GET `/ocs/v1.php/cloud/users` 用户列表
- `nextcloud.shares` — GET `/ocs/v2.php/apps/files_sharing/api/v1/shares?path=X` 分享列表

**绝不**触碰 POST `/shares` 创建分享 / DELETE `/shares/{id}` / PUT 修改任何 mutating。

### SDK 边界决策:OCS 头 inline helper(30 行)

Nextcloud OCS API 需要额外 header `OCS-APIRequest: true`(SDK httpGet 透传 Bearer 不支持自定义 extraHeaders)。

**inline httpGetOcS helper**(类似 Plausible httpPostJson):
```js
function httpGetOcS(args) {
  return new Promise((resolve) => {
    const token = cfg.token_();
    if (!token) return resolve({ ok: false, ..., error: 'no credentials' });
    const headers = {
      ...bearerHeader(token),         // ← SDK bearerHeader 复用
      'OCS-APIRequest': 'true',        // ← Nextcloud 必须
      'Accept': 'application/json',    // ← OCS 默认 XML,需 JSON
    };
    ...
  });
}
```

**SDK 复用 50% / inline 50%**:
- `bearerHeader()`、`makeConfig()`、`describeAuth()` ← **SDK 复用**
- OCS 头拼接、JSON Accept、OCS 嵌套解析 ← **inline**

### OCS 嵌套 JSON 结构

OCS API 返 `{ ocs: { meta: {...}, data: ... } }` 三层包装:
```js
const data = r.parsed && r.parsed.ocs && r.parsed.ocs.data;
const list = (data && Array.isArray(data.users)) ? data.users : [];
```

`/cloud/users` data 是 `{users: [...]}` 对象,`/shares` data 是 `[...]` 直接数组,扩展分别解析。

## 实现

### SDK 边界决策复用验证

继 Plausible(httpPostJson)后,Nextcloud(httpGetOcS)是第 2 个 inline helper。**两个 helper 共享**:
- `bearerHeader()` 复用
- `makeConfig()` 复用
- 三层失败语义对齐(缺 token / 网络错 / HTTP 4xx5xx)
- `http_status` + `error` 字段命名一致

**SDK 边界哲学验证**:GET / POST / extraHeaders 三种 inline helper 各自独立,SDK 不膨胀;但都复用 SDK 的 Bearer header + config + 失败语义,保持一致 L0 命令输出。

### SDK 失败分支 http_status 教训继承

继 Kavita E2E 抓 bug 后,Nextcloud 所有 L0 命令失败分支必带 `http_status: r.status`。E2E 一次跑通(12/12 全绿),无二次修复。

## 测试结果

**Nextcloud 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 12/12 ✓(7 单 + 5 E2E,覆盖 users/shares + path 过滤 + OCS 头校验 + 401)
```

E2E mock server 验:
- Bearer token 接受 + 401 拒绝
- **OCS-APIRequest: true 头强制**(mock server 校验缺头返 400)
- `/ocs/v1.php/cloud/users` 返 3 users(alice/bob/carol)
- `/ocs/v2.php/.../shares?path=/Photos` 路径过滤(只返 1 share)
- 错 token 401 走 OCS 嵌套结构

**Python wrapper**:
```
$ python -m pytest tests/test_nextcloud_bridge_status.py
============================== 4 passed in 0.97s ==============================
```

**15 扩展联合回归**:
```
======================== 58 passed, 2 skipped in 9.20s ========================
```

## Phase A 模板跨域矩阵(4 SDK,6 协议场景)

| SDK | 用户 | 模式 |
|---|---|---|
| subsonic-client.js | 3(Navidrome/Funkwhale/LMS) | 鉴权 salted token |
| **bearer-client.js** | **6(Audiobookshelf/Kavita/Gitea/Drone CI/Plausible/Nextcloud)** | **RFC 6750 Bearer** |
| custom-auth-client.js | 4(Komga/Miniflux/Plex/Immich) | 自定义鉴权头 |
| SQLite 自写 | 2(SiYuan/Calibre) | node:sqlite readonly |
| anonymous 自写 | 2(LX/YesPlayMusic) | 局域 Open API |

**SDK 边界(inline helpers)**:
| 扩展 | helper | 用途 |
|---|---|---|
| Plausible | httpPostJson(30 行) | POST + JSON body |
| Nextcloud | httpGetOcS(30 行) | GET + OCS extraHeaders |

**扩展家族跨域**:音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + 照片 + 书库 + 代码托管 + CI/CD + 数据分析 + **云盘**(12 个领域)。

## SDK 抽取经济性(6 用户累计)

| 维度 | 抽 SDK 前 | 抽 SDK 后(6 用户) |
|---|---|---|
| 单扩展代码 | ~200 行 | 140-180 行 |
| Bearer helper | 35-50 行/inline | SDK 复用 0 行 |
| SDK 总代码 | 0 | 114 行 |
| 跨域复用 | 0 | 6 用户(媒体 + CI/CD + 分析 + 云盘) |
| **inline helpers** | 0 | **2 个(Plausible POST / Nextcloud OCS)** |

**SDK 投资 114 行 → 6 用户共享 + 2 个 inline helper 共享 SDK Bearer 失败语义**。继续 ship 放大经济性。

## Why

Nextcloud 是 **bearer SDK 第六个用户**(累计),验证:

1. **SDK 边界决策跨扩展复用**(Plausible POST + Nextcloud OCS 两个 inline helper 共享 SDK Bearer 失败语义)
2. **OAuth dance 简化**(Phase A 用户手填 token,OAuth flow 留 Phase B)
3. **OCS API 嵌套解析**(三层 ocs/meta/data 与 SDK httpGet JSON.parse 配合)

**SDK 抽取阈值继续累计**:6+ 用户(已达),继续 ship 同模式扩展。

## How to apply

下次做 Pure Bearer 扩展(Outline / GitLab OAuth / Forgejo / Woodpecker CI / Metabase):

1. 端口确认 + token 来源
2. `require('../_scaffold/bearer-client')` —— **零修改**(bearerHeader + makeConfig + describeAuth)
3. 如需 POST + JSON body 或 extraHeaders → inline 30 行 helper(**Plausible POST / Nextcloud OCS 模板**)
4. 写 L0 命令 + **入参验证 + 失败分支 http_status**(Kavita 教训)
5. 测三件套(Node 单测 + E2E mock server 含鉴权头校验 + Python wrapper)
6. 跑联合回归

工作量:**140-180 行 index.js**(取决于 inline helper 数量)。

下次做需要 SDK 增强:
- **若累计 2+ 扩展都需 POST + Bearer**(目前 1 = Plausible),考虑加 `httpPostJson()` 进 bearer SDK
- **若累计 2+ 扩展都需 OCS-style extraHeaders**(目前 1 = Nextcloud),考虑加 SDK `httpGetWithHeaders()` 模式

当前 inline 仍可接受(各 1 个用户)。

相关:[[plausible-bridge-status-phase-a-shipped]], [[drone-bridge-status-phase-a-shipped]], [[gitea-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[kavita-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]]
