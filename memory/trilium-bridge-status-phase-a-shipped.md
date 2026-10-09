---
name: trilium-bridge-status-phase-a-shipped
description: Trilium Notes 自托管层级笔记 Phase A 只读扩展 ship — bearer SDK 第 13 用户 + 跨入笔记/层级知识域
metadata:
  type: project
---

# Trilium Notes 自托管层级笔记 Phase A 只读扩展 ship

**2026-10-09 决策:ship trilium-bridge-status 扩展**

## 选型理由

- **zadam/trilium** — 14.4K+ stars,自托管层级笔记应用(类似 Obsidian/Logseq),用户常把密码/API key/凭据存入
- **AGPL-3.0 客户端 HTTP 集成不触发传染**(纯只读 GET,无 copyleft 风险)
- **SDK 零边界跨越**:`extensions/_scaffold/bearer-client.js` 完全适配
  - 单层 `Authorization: Bearer <token>`(RFC 6750,Trilium v0.93+ 推荐)
  - token 在 UI → Options → ETAPI → Add Token 生成(可命名/吊销)
- **envelope 直对象数组** — 与 SiYuan 一致,无需 unwrap

## SDK 复用里程碑

**Trilium 是 bearer-client.js SDK 第十三个用户**(前 12 用户跨 12 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签),**跨入笔记/层级知识域**作为第 13 类。

## 命令清单(L0 风险,纯只读)

```
trilium.health    {}  → GET /etapi/app-info 公开探活 + 取版本
trilium.notes     {search?, limit?, debug?}  → GET /etapi/notes?search=&fastSearch=&limit=
trilium.note      {noteId}  → GET /etapi/notes/{noteId} 单 note 元数据
```

## 严禁触碰(产品级 P0)

- POST/PUT/DELETE /etapi/notes(任何写入)
- GET /etapi/notes/{id}/export (ZIP 含附件,高风险)
- GET /etapi/import / /backup
- POST /etapi/branches(创建分支)
- **content 字段绝对不抓** — 笔记可能含密码/API key/医疗/日记
  - Phase A 只返 metadata(title/type/mime/dateModified/is_protected/child_count)

## 借鉴原则 5「Token≠密码」中档(6/10)

token 一旦签发可重放直到用户在 Options → ETAPI 吊销。Tier 6 风险:
- 不像密码需每次输入(更易持久化在 SDK 缓存)
- 但用户可主动吊销且 token 一般 60+ 随机字符
- 不要求 mTLS / IP 白名单(与 Vaultwarden 同档)

## 测试矩阵

- 8 unit + 8 E2E = 16/16 全绿(mock ETAPI @ 127.0.0.1 random port)
- E2E 覆盖:probeHealth / fetchNotes 默认/search/limit / fetchNote 单 note 加密标记 / 错 token 401 / 不存在 404
- 24 个 Phase A 扩展联合回归:91 passed + 1 skipped,0 fail

## 借鉴清单与跨入域累计

**当前已 ship 25 个 Phase A 扩展,跨入 14 类域**:
- 媒体 (3): Plex / Audiobookshelf / LMS / Navidrome / YesPlayMusic
- 漫画 (2): Komga / Kavita
- 照片 (1): Immich
- 笔记/层级 (3): SiYuan / **Trilium** / HedgeDoc(待 ship)
- 文档 (1): Paperless-ngx
- 知识库/wiki (2): Outline / BookStack
- 习惯/任务 (1): Habitica
- 凭据 (1): Vaultwarden
- 书签 (1): Linkwarden
- 代码托管 (2): Gitea / GitLab
- 数据分析 (1): Plausible
- 云盘 (1): Nextcloud
- 理财 (1): Firefly III
- 食谱 (1): Mealie
- RSS (1): Miniflux

## 相关

- recon 路径:见本会话任务 #65 描述
- 后续候选:HedgeDoc 协同 Markdown (已侦察,等用户拍板 ship)
- commit 期待:类似之前 batch 模式(trilium-bridge-status v0.1.0)
