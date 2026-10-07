---
name: bookstack-bridge-status-phase-a-shipped
description: BookStack 自托管 wiki Phase A ship — bearer SDK 第 8 用户 + 首个纯 REST + Bearer(零 SDK 边界跨越)
metadata:
  type: project
---

# BookStack 自托管 wiki Phase A ship

**2026-10-07 ship,bearer SDK 第 8 用户 + 首个纯 REST + Bearer 扩展**(Outline 是 RPC-style POST,BookStack 是真 REST GET)。

## Phase C SDK 复用
- **100% 适配现有 Bearer SDK**:鉴权和 verb 双标准(RFC 6750 Bearer + 真 REST GET),纯 httpGet 三招打完
- **零 SDK 边界跨越**:无 inline helper、无 extraHeaders、无 POST——扩展代码净 110 行,只用 SDK 5 API
- bearer SDK 8 个用户(累计跨域 7 类):Audiobookshelf / Kavita / Gitea / Drone CI / Plausible / Nextcloud / Outline / **BookStack**

## BookStack 扩展核心(extensions/bookstack-bridge-status/)
- **鉴权**: `Authorization: Bearer <token_id>:<token_secret>` (RFC 6750,BookStack user profile → API Tokens)
- **协议**: 真 REST GET / JSON 响应(对比 Outline 是 RPC-style POST)
- **端点**: `GET /api/books` 返 `{data:[...], total:N}` / `GET /api/shelves` 返同样包装
- **3 L0 命令**: `bookstack.health`(用 /api/books 探活+列表)/ `bookstack.shelves` / `bookstack.books`
- **绝对不**碰: POST / PUT / DELETE 任何 mutating
- **P3.10b 红线**: 100% 本地,0 上传/外传,token 借鉴原则 5 中档(6/10)

## 候选淘汰决策
- **不选 Trilium**: `Authorization: ETAPITOKEN` 自定义 literal 不是 Bearer(SDK 边界跨越)
- **不选 Wiki.js**: 纯 GraphQL(必须 POST + body),与 Outline 重复度高;且 mutating 风险中(单 endpoint 多 mutation,需白名单守卫)
- **不选 HedgeDoc**: session cookie 鉴权(SDK 不懂 cookie 生命周期,需新建 cookie 管理 layer)
- **不选 Draw.io**: 几乎无 first-party REST API,主要是 embed 渲染器
- **首选 BookStack**: 唯一「真 REST + 真 Bearer + 零边界跨越」完美候选

## 测试战绩(11/11 E2E 全绿)
- 7 单测(沙箱):env override / 无 token 早退 / 不可达 ECONNREFUSED / SDK 5 API 在场 / describeAuth has_token 验证
- 4 E2E(mock BookStack server @ 随机端口):
  - probeHealth → alive + total_books=3 + latency < 500ms
  - fetchShelves → 2 shelves + books_count 字段映射
  - fetchBooks → 3 books + slug/created_at/updated_at 字段
  - **错 token → 401 → ok=false + http_status=401 + last_error 含 'HTTP 401'**

## 全量 17 扩展回归
**203/203 passed(33.80s)** + 1 skipped(归档音乐模块)
(比 Outline ship 后的 199 多 4 个 BookStack Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传、纯只读、不调用麦克风、不外传数据