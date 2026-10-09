---
name: hedgedoc-bridge-status-phase-a-shipped
description: HedgeDoc 协同 Markdown Phase A 只读扩展 ship — bearer SDK 第 14 用户 + 跨入协同笔记/Markdown 域 + 根 HTML 探活
metadata:
  type: project
---

# HedgeDoc 协同 Markdown Phase A 只读扩展 ship

**2026-10-10 决策:ship hedgedoc-bridge-status 扩展**

## 选型理由

- **hedgedoc/hedgedoc** — 5.7K+ stars,自托管协同 Markdown 笔记(原 CodiMD/HackMD 开源版)
- **AGPL-3.0 客户端 HTTP 集成不触发传染** — 纯只读 GET + 进程级隔离(见 [[agpl-ship-boundary]] 决策)
- **SDK 零边界跨越**:`extensions/_scaffold/bearer-client.js` 完全适配
  - v2.x Bearer Token(`ApiTokenGuard` + `ensureTokenIsValid()`,仅 validUntil 过期检查)
  - token 在 UI → Profile → API tokens 创建(可设过期)
- **envelope 兼容双轨**:v1.x 返直数组 + v2.x 返 `{data}` — 第一层容错 `Array.isArray(parsed) ? parsed : parsed.data`
- **避开 v1.x session cookie**(s:` 前缀,`httpOnly` 14 天 TTL) — inline dance 违反 SDK 零边界跨越原则

## SDK 复用里程碑

**HedgeDoc 是 bearer-client.js SDK 第十四个用户**(前 13: 媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签/笔记),**跨入协同笔记/Markdown 域**作为第 14 类。

**笔记三件套(差异化)形成**:
- **个人笔记**:SiYuan(已 ship)
- **层级笔记**:Trilium(刚 ship,[[trilium-bridge-status-phase-a-shipped]])
- **协同 Markdown**:HedgeDoc(本 ship)

## 探活路径(关键设计点)

HedgeDoc **无 `/api/status` 官方端点**(v1.x/v2.x 都不带),用 **根路径 `GET /` 验活 + 提取 `<title>` 标签** + 关键字 `hedgedoc|codi[_-]?md` 识别实例。

**SDK body 字段是 utf8 string** — 解析 HTML 用 regex `<title>([^<]+)</title>`,无需 `rawBody`(SDK 不支持)。

## 命令清单(L0 风险,纯只读)

```
hedgedoc.health    {}  → GET /  根 HTML 探活 + 提取 page_title
hedgedoc.notes     {search?, limit?, skip?, view?, ownedByUser?, ally?}  → GET /api/v2/notes
hedgedoc.user      {}  → GET /api/v2/user 当前用户
```

## 严禁触碰(产品级 P0)

- POST/PUT/DELETE /api/v2/notes(任何写入)
- GET /api/v2/notes/{id}/content (笔记正文,可能含密码/API key/凭据/医疗)
- /api/private/* (v1.x session cookie,phase A 不接)
- **content 字段绝对不抓** — 协同笔记更易含团队共享凭据

## AGPL 合规(三条红线)

1. **只做客户端代理,不改服务端** ✅
2. **不打包/分发 AGPL 服务端** ✅(让用户自部署)
3. **绝不做 SaaS 托管 AGPL 服务** ✅(走 P3.10b 0 上传红线)

进程级隔离:PrisirAI 扩展是独立 MIT 进程,只调 HTTP,不嵌入服务端代码。

## 测试矩阵

- 7 unit + 6 E2E = 13/13 全绿(mock HedgeDoc v2 @ 127.0.0.1 random port)
- E2E 覆盖:probeHealth(根 HTML 探活 + is_hedgedoc)/ fetchNotes 默认+search+limit / fetchUser / 错 token 401
- 25 个 Phase A 扩展联合回归:95 passed + 1 skipped,0 fail(91 + 4 new)
- Python wrapper `tests/test_hedgedoc_bridge_status.py` 4/4 PASSED

## 借鉴清单与跨入域累计

**当前已 ship 26 个 Phase A 扩展,跨入 15 类域**(新增第 14 类:协同笔记):
- 媒体 (5): Plex / Audiobookshelf / LMS / Navidrome / YesPlayMusic
- 漫画 (2): Komga / Kavita
- 照片 (1): Immich
- 笔记 (3): SiYuan / **Trilium** / **HedgeDoc**
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

- recon:见本会话任务 #64(2026-10-10 完成)
- AGPL 决策:[[agpl-ship-boundary]]
- 同期 ship:Trilium([[trilium-bridge-status-phase-a-shipped]])
- commit 期待:`feat(ext): HedgeDoc 协同 Markdown Phase A 只读 ship — bearer SDK 第 14 用户 + 跨入协同笔记/Markdown 域 + 根 HTML 探活 + AGPL-3.0 客户端代理合规`