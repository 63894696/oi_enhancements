---
name: simplex-bridge-status-phase-a-shipped
description: SimpleX 跨设备 E2E 通信 metadata Phase A 只读扩展 ship — bearer SDK 第 15 用户 + 跨入跨设备/E2E 通信第 16 类域 + 0 content 抓取产品级 P0
metadata:
  type: project
---

# SimpleX 跨设备 E2E 通信 metadata Phase A 只读扩展 ship

**2026-10-10 决策:ship simplex-bridge-status 扩展**

## 选型理由

- **simplex-chat/simplex-chat**(AGPL-3.0,自托管)**真正 E2EE 跨设备通信协议**:
  - 无用户 ID 概念(每会话独立 1 次性 link)
  - SMP 服务器只中转加密消息看不到内容
  - 双棘轮 + X3DH 端到端加密
  - 客户端形态:CLI / Desktop / iOS / Android
  - **CLI 启 5225 端口 HTTP API**(`--api-token=<token>` 鉴权)
- **填补 26 扩展 0 通信域空白** — SimpleX 是第 16 类
- **SDK 零边界跨越**:`extensions/_scaffold/bearer-client.js` 完全适配
- **envelope unwrap** — SimpleX CLI 返 `{result}` 或 `{resp}`,第一层兼容
- **AGPL-3.0 客户端代理合规** — 纯只读 GET/POST + 进程级隔离(见 [[agpl-ship-boundary]])

## 决策背景(用户拍板)

用户原话(2026-10-10):
> 「直接 ship,关于前面项目的选择还是和之前的筛选标准一样,没有设计给 agent 操作的就不做扩展。同时把莱茵金属的开源通信项目看是否有能抽给密信做优化的模块,由于密信还有安全漏洞要修复目前也不做扩展。」

3 个关键约束(写到 [[extension-ship-filter]]):
1. **没有设计给 agent 操作的就不做扩展** — SimpleX CLI 6.x 有 `--api-token` + 5225 HTTP API ✅
2. **密信/含安全漏洞项目暂不 ship** — `prisir-browser/mixin/` 有未修漏洞 ❌(密信产品层 reject)
3. **Rheinmetall 通信项目侦察模块抽取可行性** — 不 ship 扩展(侦察任务,见 [[rheinmetall-skip-decision]] + 单独侦察报告)

## SDK 复用里程碑

**SimpleX 是 bearer-client.js SDK 第十五个用户**(前 14 跨 14 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签/笔记/协同笔记),**跨入跨设备/E2E 通信域**作为第 16 类。

**首次使用 `httpPostJson`** — 之前 14 用户全用 `httpGet`,SimpleX 是首个 metadata 拉取用 POST(SimpleX CLI 6.x API 设计就是这样)。

## 命令清单(4 L0,纯只读 metadata)

```
simplex.health    {}  → GET /  探活(API 状态)
simplex.contacts  {search?, limit?}  → POST /v3/contacts 列联系人
simplex.chats     {chatType?, limit?}  → POST /v3/chats 列聊天列表
simplex.groups    {limit?}  → POST /v3/groups 列群组
```

## 严禁触碰(产品级 P0)

- 任何带 `text` / `formattedText` / `file` 字段的端点(productive 消息)
- 任何 `POST /v3/{send,delete,update,...}` 写端点
- 私钥文件(`~/.simplex/`)直读 — 只能经 SimpleX CLI HTTP API
- 私钥物理隔离在用户本地,扩展不读不存

## AGPL 合规(三条红线)

1. **只做客户端代理,不改服务端** ✅
2. **不打包/分发 AGPL 服务端** ✅(SimpleX CLI 用户自跑)
3. **绝不做 SaaS 托管 AGPL 服务** ✅(走 P3.10b 0 上传红线)

进程级隔离:PrisirAI 扩展只调 `http://127.0.0.1:5225`,**私钥绝不出 PrisirAI 进程**。

## 借鉴原则 5「Token≠密码」最低档(3/10)

- 本地 token 只绑 127.0.0.1,无吊销机制
- 即便 token 泄漏,只能从本机访问
- 用户改 `--api-token` 重启 SimpleX CLI 即可换 token
- **私钥物理隔离** — 扩展不读 `~/.simplex/`

## 测试矩阵

- 8 unit + 6 E2E = 14/14 全绿(mock SimpleX CLI 6.x @ 127.0.0.1 random port)
- E2E 覆盖:probeHealth / fetchContacts(3 contacts + is_user 标记)/ fetchChats(metadata-only)/ fetchGroups(has_description)/ 错 token 401
- 27 个 Phase A 扩展联合回归:99 passed + 1 skipped,0 fail(95 + 4 new)
- Python wrapper `tests/test_simplex_bridge_status.py` 4/4 PASSED

## 累计跨入 16 类域(已 ship 27 个 Phase A 扩展)

| # | 域 | 代表扩展 |
|---|----|----------|
| 1 | 媒体 (5) | Plex / Audiobookshelf / LMS / Navidrome / YesPlayMusic |
| 2 | 漫画 (2) | Komga / Kavita |
| 3 | 照片 (1) | Immich |
| 4 | 笔记 (3) | SiYuan / Trilium / HedgeDoc |
| 5 | 文档 (1) | Paperless-ngx |
| 6 | 知识库 (2) | Outline / BookStack |
| 7 | 习惯 (1) | Habitica |
| 8 | 凭据 (1) | Vaultwarden |
| 9 | 书签 (1) | Linkwarden |
| 10 | 代码托管 (2) | Gitea / GitLab |
| 11 | 数据分析 (1) | Plausible |
| 12 | 云盘 (1) | Nextcloud |
| 13 | 理财 (1) | Firefly III |
| 14 | 食谱 (1) | Mealie |
| 15 | RSS (1) | Miniflux |
| **16** | **跨设备 E2E 通信 (1)** | **SimpleX** |

## 关键设计点

### 1. Bearer Token via SimpleX CLI `--api-token`
SimpleX CLI 6.0+ 支持:
```bash
./simplex-chat -p 5225 --api-token=<long-random-token>
```
扩展用 bearer-client.js SDK 适配,沿用 Linkedwarden/Trilium/HedgeDoc 14 用户 pattern。

### 2. envelope unwrap
SimpleX CLI 6.x 返 `{resp: {type: "contactsList", contacts: [...]}}`,第一层 unwrap 抽 `resp`(也兼容 `{result}`)。

### 3. metadata-only 严格不抓 content
- 列联系人:返 `{contactId, localDisplayName, profile.displayName, profile.fullName, isUser, active}` 字段
- 列消息:返 `{chatId, chatInfo.type, chatInfo.localDisplayName}` **不抓** `text` / `formattedText` / `file`
- **类比 Trilium `is_protected` 标记 / HedgeDoc `content_included=false`**

### 4. 进程级隔离
- SimpleX CLI 由**用户自己跑**
- 扩展**不启动** SimpleX 进程
- 扩展**只调** `http://127.0.0.1:5225`

## 与 Rheinmetall 通信项目的关键区别

| 维度 | Rheinmetall/* | SimpleX |
|------|--------------|---------|
| License | NOASSERTION / EPL-2.0(混) | AGPL-3.0(单轨) |
| 协议 | .rmodel 私有 / gRPC | HTTP REST + Bearer |
| 客群 | 嵌入式/军用 | 民用跨设备通信 |
| 内容 | 战场态势(涉敏) | 私聊(只 metadata) |
| ship 决策 | ❌ reject([[rheinmetall-skip-decision]]) | ✅ ship(本文件) |

## 与 Prisir 浏览器密信(mixin)的关系

- **本扩展**:对接 SimpleX Chat CLI(独立 AGPL-3.0 客户端,SimpleX 协议层)
- **Prisir 浏览器密信**:`prisir-browser/mixin/` 内置 `chrome://mixin` 页(用户产品层,基于 SimpleX 协议)
- **两者不冲突**:
  - 浏览器密信 = 用户 UI(密信产品层)
  - 本扩展 = LLM 引用 metadata(不抓 content)
- 浏览器密信当前**有未修漏洞** → 本轮不 ship `mixin-bridge-status`(密信产品层)
- 浏览器密信漏洞修复后,未来**可 ship** `mixin-bridge-status` 接 `prisir-browser/mixin/_PAGE`

## 相关

- recon:无需侦察(协议 + CLI API 已熟悉)
- 用户拍板:[[extension-ship-filter]] 2026-10-10
- AGPL 决策:[[agpl-ship-boundary]]
- 同期侦察:Rheinmetall 通信项目模块抽取可行性(task #70,后台跑)
- 同期 commit:本扩展 ship 期待
- Obsidian 同步:`Documents/ObsidianVault/PROJECTS/PrisirAI/扩展生态/simplex-bridge-status-phase-a-shipped-2026-10-10.md`