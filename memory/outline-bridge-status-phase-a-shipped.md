---
name: outline-bridge-status-phase-a-shipped
description: Outline 知识库 Phase A ship — bearer SDK httpPostJson 抽取触发(SDK 第 5 个 API)+ Plausible 重构零扩展代码改动
metadata:
  type: project
---

# Outline 知识库 Phase A ship + bearer SDK 增强 v0.5

**2026-10-07 ship,bearer SDK 第 7 用户 + SDK httpPostJson 抽取触发**。

## Phase C SDK 演化
- bearer-client.js 原 4 API: bearerHeader / makeConfig / httpGet / describeAuth
- **v0.5 新增 httpPostJson**(API 5):Plausible 是首个 inline httpPostJson(30 行 POST + JSON body helper),
  Outline ship 是第 2 个 inline POST——SDK 抽取阈值触发,**httpPostJson 抽进 bearer SDK 共享**
- **Plausible 重构** 用 SDK httpPostJson,扩展代码净减 23 行(198 → 175 行),零功能性改动
- 7 个用户复用:Audiobookshelf / Kavita / Gitea / Drone CI / Plausible / Nextcloud / **Outline**
- 跨域: 媒体中心 → 漫画/书 → 代码托管 → CI/CD → 数据分析 → 云盘 → **知识库(Notion 替代)**

## Outline 扩展核心(extensions/outline-bridge-status/)
- **鉴权**: `Authorization: Bearer ol_api_<key>` (RFC 6750 标准 Bearer, Outline Settings → API & Apps)
- **协议**: 所有端点 POST + JSON body(非典型 GET REST,反而 RPC 风格)
- **端点**: `/api/auth.info`(探活+用户)/ `/api/collections.list` / `/api/documents.list`
- **3 L0 命令**: `outline.health` / `outline.collections` / `outline.documents{collectionId?, limit?, offset?}`
- **绝对不**碰: POST /api/documents.create / DELETE /api/documents.delete / PUT /api/documents.update
- **P3.10b 红线**: 100% 本地,0 上传/外传,token 借鉴原则 5 中档(6/10)

## SDK 抽取决策记录
- 触发条件: 2 个 inline POST helper(Plausible + Outline)+ ≥1 行差异
- 抽取位置: extensions/_scaffold/bearer-client.js (与 httpGet / makeConfig 并列 API 5)
- 失败语义: 与 httpGet 同 —— 缺 token → status:0 + 'no credentials'; 网络错 → status:0 + e.message;
  HTTP 4xx/5xx → status:code + 'HTTP <code>'
- 头注入: `Authorization: Bearer <token>` + Content-Type: application/json + Content-Length

## 测试战绩(13/13 E2E 全绿)
- 7 单测(沙箱 + SDK 增强验证):env override / 无 token 早退 / 不可达 ECONNREFUSED / SDK 5 API 在场
- 6 E2E(mock Outline server @ 随机端口):
  - probeHealth → user.email + is_admin + latency < 500ms
  - fetchCollections → 3 collections(eng/product/ops + color/icon)
  - fetchDocuments → 4 docs + text_preview 截断 200 字 + published 字段
  - fetchDocuments(collectionId=c-eng) → 1 doc 过滤
  - fetchDocuments(limit=2) → 2 docs + pagination.next_offset
  - **错 token → 401 → ok=false + http_status=401 + last_error 含 'HTTP 401'**
- Plausible 重构后 11/11 全绿(SDK 5 API 全用上测试)

## 全量 16 扩展回归
199/199 passed(33.03s)+ 1 skipped(归档音乐模块)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传、纯只读、不调用麦克风、不外传数据