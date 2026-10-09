---
name: paperless-bridge-status-phase-a-shipped
description: Paperless-ngx 自托管文档管理 Phase A ship — custom-auth SDK 第 7 用户 + 跨入文档域 + DRF 分页循环 + thumb 二进制 inline
metadata:
  type: project
---

# Paperless-ngx 自托管文档管理 Phase A ship

**2026-10-08 ship,custom-auth SDK 第 7 用户 + 跨入文档管理域 + DRF 分页循环跟随 + thumb 二进制 inline 实现**。

## Phase C SDK 复用
- **custom-auth SDK 第 7 个用户**(累计跨域 7 类:漫画/照片/RSS/媒体中心/凭据/习惯/**文档**)
- **不触 SDK 边界**:SDK `mode='custom'` 完美拟合 `Authorization: Token <key>`(扩展在 customToken 内拼 "Token " 前缀)
- **DRF 分页循环** inline 在 `fetchDocuments()` 内跟随 `next` URL,跳过 `all` 数组省内存
- **thumb 端点 inline 实现**(不走 SDK httpGet) — SDK body 是 utf8 string,binary PNG 失真 → 扩展 inline http.request 保留 Buffer

## Paperless-ngx 扩展核心(extensions/paperless-bridge-status/)
- **鉴权**: `Authorization: Token <api_token>`(Django REST Framework TokenAuthentication,Web UI → My Profile → API Token 生成)
- **协议**: DRF 标准 envelope `{count, next, previous, all, results}` + 自定义 `pathToRelativePath()` 抽 next URL pathname+search
- **端点**:
  - `GET /api/` 探活(DRF root,返所有 endpoint schema)
  - `GET /api/documents/?page_size=N&query=&tags__id__all=&correspondent__id__all=` 文档列表(分页循环跟随 next,max_pages 默认 5 防 OOM)
  - `GET /api/tags/?page_size=N` 标签列表
  - `GET /api/documents/{id}/thumb/` PNG 缩略图 base64
- **4 L0 命令**: `paperless.health` / `paperless.documents{page?,page_size?,query?,tags__id?,correspondent__id?,max_pages?}` / `paperless.tags` / `paperless.thumb{documentId}`
- **绝对不**碰: `POST /api/documents/` / `PUT / PATCH / DELETE` / `POST /api/documents/post_document/`(上传/邮件消费)
- **DRF `all` 数组 skip**:防止 10 万级文档库 OOM — Phase A 只拉 `page_size` 切片
- **P3.10b 红线**: 100% 本地、0 上传/外传、纯只读、不调用麦克风、不外发文档(用户文档可能含合同/账单/医疗)

## 真 bug 修复记录(E2E 第一次 ship 即暴露 3 个)
1. **DRF Token prefix 缺失**:`customToken: 'tok_demo'` SDK 拼成 `Authorization: tok_demo` → 401。**修复**:扩展内 `customToken: process.env.PRISIR_PAPERLESS_API_KEY ? 'Token ${...}' : ''` — DRF 标准要求 "Token <key>" 头值自带前缀
2. **thumb 二进制 utf8 失真**:SDK `body = Buffer.concat(chunks).toString('utf8')` 把 PNG 字节当成 utf8 解码,base64 encode 后 base64 解码回 0x7B(`{`) 而非 0x89(PNG magic)。**修复**:`fetchThumb()` inline http.request 保留 Buffer,用 `buf.toString('base64')` 输出
3. **mock server 路由顺序**:`/api/documents/{id}/thumb/` 的 startsWith(`/api/documents/`) 先匹配 → 走 documents 分页逻辑返 DRF envelope 而非 PNG。**修复**:mock 把 thumb 路由提到 documents 之前,先 regex 精确匹配 `^\/api\/documents\/\d+\/thumb\/?$` 再走 startsWith
   **教训**:mock 路由顺序 = 真实 server 路由顺序的镜像,精准匹配 must come before startsWith

## 测试战绩(14/14 E2E 全绿)
- **8 单测**(沙箱): env override / parseDrfEnvelope / pathToRelativePath / 4 个无 token + thumb invalid + ECONNREFUSED
- **6 E2E**(mock Paperless-ngx server @ 随机端口):
  - probeHealth → alive + endpoints_count=4
  - fetchDocuments(page_size=2) → 跟随 next 拉 2 页得 3 docs
  - fetchDocuments(query=Invoice) → query 拼接工作
  - fetchTags → 3 tags + slug + color + document_count
  - fetchThumb(2) → PNG base64 + magic bytes `0x89 0x50 0x4E 0x47` + size
  - 错 token → 401 → ok=false + http_status=401

## 全量 24 扩展回归
**225/225 passed(40.86s)** + 1 skipped(归档音乐模块)
(比 Habitica ship 后的 221 多 4 个 Paperless Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传/外传、纯只读、不调用麦克风。文档缩略图 base64 仅在前端渲染,**绝不缓存到 PrisirAI 后端**

## 候选淘汰决策
- **不选 Uptime Kuma**(上轮):socket.io 主通道 + 无 Bearer 鉴权 = 新 SDK = 边界跨越
- **首选 Paperless-ngx**(本轮):鉴权 Token 头 + DRF envelope 标准 + thumb inline binary + 跨入全新文档管理域

## SDK 边界观察
- SDK `body` 字段是 utf8 string,对 binary 端点(PNG/PDF/音频)不友好
- 第二个用户需要 binary 时(媒体/附件)考虑给 SDK 增加 `responseType: 'binary' | 'text'` 选项
- 当前**保持不变**:Paperless 是 inline 第一个 binary 需求,如果后续 Audiobookshelf cover/Immich thumbnail 也需,再考虑 SDK 抽象升级