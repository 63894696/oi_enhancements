---
name: linkwarden-bridge-status-phase-a-shipped
description: Linkwarden 自托管书签/稍后读 Phase A ship — bearer SDK 第 12 用户 + 跨入书签域 + 单层 Bearer JWT 模式
metadata:
  type: project
---

# Linkwarden 自托管书签/稍后读 Phase A ship

**2026-10-07 ship,bearer SDK 第 12 用户 + 跨入书签/稍后读域 + 单层 Bearer JWT(RFC 6750)模式**。

## Phase C SDK 复用
- **bearer SDK 第 12 用户**(累计跨域 12 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/**书签**)
- **零 SDK 边界跨越**:与 BookStack/Mealie 同 pattern,纯 `httpGet + describeAuth + makeConfig`
- **单层 Bearer 模式**(与 Vaultwarden OAuth2 dance 形成对照):
  - 无 OAuth dance / 无 refresh token / 无 scope
  - JWT 载荷 `{ id, iat, exp, jti }`,server 仅存 jti 用于撤销
  - token 一旦签发,继承该用户**所有权限**(包括 admin)— 严禁 ship 任何写端点
- **不触发 SDK 边界跨越**(与 Miniflux/Plex 同类)— 单层 Bearer 在 SDK 抽象内,边界保持稳定

## Linkwarden 扩展核心(extensions/linkwarden-bridge-status/)
- **鉴权**: `Authorization: Bearer <jwt>` 标准 Bearer(RFC 6750),token 在 Linkwarden Settings → API Tokens 创建
- **协议**: 真 REST GET + Next.js 风格 `{response:[...], status:200}` envelope(常见于 Audiobookshelf / Next.js API routes)
- **端点**:
  - `GET /api/v1/config` **公开探活**(无需 token,DISABLE_REGISTRATION / DISABLE_DEPRECATED_ROUTES / NEXT_PUBLIC_DEMO 标志)
  - `GET /api/v1/users` 鉴权后列同订阅成员(`/users/me` 不存在,Phase A 简化返 list,Phase B 再 JWT 解码)
  - `GET /api/v1/collections` 鉴权后列 collections(name + `_count.links` + `parent.id` + `members.length`)
  - `GET /api/v1/links` 鉴权后分页列链接(支持 cursor / collectionId / searchQueryString / pinnedOnly 4 维 query,Next.js cursor-based 分页)
- **4 L0 命令**: `linkwarden.health` / `linkwarden.user` / `linkwarden.collections` / `linkwarden.links`
- **绝对不**碰: `POST /api/v1/links` / `PUT /api/v1/links/{id}` / `DELETE /api/v1/links/{id}` / `POST /api/v1/tokens` / `DELETE /api/v1/tokens`
- **不触碰 Stripe 后端**: `/api/v1/subscriptions` 与 Stripe webhook 强相关,Phase A 完全隔离

## 真 bug 修复记录(E2E 第一次 ship 即暴露)
1. **sandbox 缺少 URL/URLSearchParams globals**:`fetchLinks` 用 `new URLSearchParams()` 拼 query string,v8 上下文抛 `ReferenceError: URLSearchParams is not defined`
   修复:`loadModule` sandbox 注入 `URL, URLSearchParams`(同 Buffer/http pattern)
   **教训**:sandbox 测试任何用了现代 Web API 的扩展都得显式列 globals;扩展本身在 Node 主进程跑没问题,只在 vm 上下文里需要

## 测试战绩(15/15 E2E 全绿)
- **8 单测**(沙箱): env override / probeHealth/users/collections/links 无 token 全 'no credentials' / 不可达 ECONNREFUSED / SDK 复用 / query 拼接
- **7 E2E**(mock Linkwarden server @ 随机端口):
  - probeHealth → alive + disable_registration=true + demo_mode=false
  - fetchUsers → 2 users(Alice + Bob)+ email_verified 字段映射
  - fetchCollections → 3 collections + `_count.links=42/18/5` 映射 + members_count
  - fetchLinks(默认) → 3 links + tags 数组 + pinned 布尔 + pinned_count + next_cursor
  - fetchLinks(collectionId=8) → 2 links(只 Tech Resources)
  - fetchLinks(searchQueryString=meal) → mock 返全部 3(验 query 拼接成功)
  - **错 token → 401 → ok=false + http_status=401 + last_error**

## 全量 22 扩展回归
**217/217 passed(35.55s)** + 1 skipped(归档音乐模块)
(比 Vaultwarden ship 后的 215 多 2 个 Linkwarden Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传/外传、纯只读、不调用麦克风。token 中档(6/10)— JWT 可重放但项目仅本机自托管使用

## 候选淘汰决策
- **不选 Uptime Kuma** (上轮): socket.io 主通道 + 无 Bearer 鉴权 = 新 SDK = 边界跨越
- **首选 Linkwarden**: 真 Bearer JWT(RFC 6750)+ 真 REST + 跨入书签/稍后读全新域
