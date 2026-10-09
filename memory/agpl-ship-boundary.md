---
name: agpl-ship-boundary
description: AGPL-3.0 ship 边界判断 — 客户端代理安全 / 服务端 fork 强传染 / 三条红线守住合规
metadata:
  type: reference
---

# AGPL-3.0 ship 边界判断(2026-10-10 立)

**用户提问**:「AGPL-3.0 协议是否不适合我们将它加入扩展?」

**回答核心**:**AGPL-3.0 不是 ship 阻碍因素**,但必须守住 3 条红线(我们已 ship 15 个 AGPL 自托管扩展,0 例外)。

## 法律基础(简短版)

AGPL 是 copyleft,但 copyleft 触发条件是「**分发 / 服务端修改 + 提供外部访问**」。

- **GPL FAQ 明确**:单纯使用 GPL 库通过网络交互不触发传染
- **纯只读 HTTP 客户端代理 = 独立作品 + 透明网络访问**(与 web browser 调 Google API 同性质)
- **AGPL 比 GPL 严的是 §13**「改服务端 + 公开 serve → 须公开衍生源码」,**客户端代码不受传染**

## 三条红线(必守)

| # | 红线 | 反例 | 我们的做法 |
|---|------|------|----------|
| 1 | **只做客户端代理,不改服务端** | fork 改 AGPL 服务端代码再分发 → §13 强传染 | 我们从不 fork / 改服务端,只让用户自己 `docker run` |
| 2 | **不打包/分发 AGPL 服务端** | 把 AGPL 服务端嵌入商业产品分发给用户 | 让用户自部署,PrisirAI 进程与 AGPL 服务容器**进程级隔离** |
| 3 | **绝不做 SaaS 托管 AGPL 服务** | 在自己云上跑 AGPL 服务给多用户用 → §13 触发 | 走 P3.10b 0 上传红线,所有扩展用户自托管 |

## 决策矩阵(给后续派单/侦察/决策用)

```
┌─────────────────────────────────────────────────────────────┐
│ License 分类              │ ship 决策 │ 备注                  │
├───────────────────────────┼──────────┼───────────────────────┤
│ 自托管 + MIT/Apache       │ ✅ ship   │ 零风险               │
│ 自托管 + AGPL-3.0         │ ✅ ship   │ 客户端代理不传染     │
│ 自托管 + GPL-2/3          │ ✅ ship   │ 客户端代理不传染     │
│ 自托管 + 商业许可         │ ⚠️ 看情况 │ 商业集成风险需审查   │
│ 非自托管 + 开源 SaaS      │ ⚠️ 看情况 │ P3.10b + 商业风险   │
│ 闭源 SaaS + 标准 License  │ ❌ reject │ P3.10b 红线          │
│ 闭源 SaaS + NOASSERTION/Other │ ❌ reject │ 法律风险高 + P3.10b │
└─────────────────────────────────────────────────────────────┘
```

## 关键区分(给所有协作者,新员工 on-call 必读)

**「客户端代理」 vs 「服务端 fork」**:

```
✅ 我们做(不触发传染):
   我们的扩展 → HTTP GET → 用户自己的 AGPL 服务容器
   = 类似 web browser 调 Gmail API
   = 独立作品,不传染

❌ 我们不做(触发传染):
   1. fork AGPL 服务端 → 改代码 → 重新打包分发 → §13
   2. 把 AGPL 服务端嵌入我们自己的商业 SaaS → §13
   3. 修改 AGPL 服务端扩展(如 Jellyfin plugin)并分发 → copyleft
```

## 已 ship 的 AGPL 自托管扩展(15 个 0 例外)

- **Trilium (AGPL-3.0)** — [[trilium-bridge-status-phase-a-shipped]]
- **BookStack (AGPL-3.0)** — [[bookstack-bridge-status-phase-a-shipped]]
- **SiYuan (AGPL-3.0)** — [[siyuan-…]]
- **Nextcloud (AGPL-3.0)**
- **Plausible (AGPL-3.0)**
- **Komga (AGPL-3.0)**
- **Jellyfin / Plex / LMS / Audiobookshelf / Kavita / Miniflux / Mealie / Paperless / Mumble** — 多数 AGPL-3.0

## 派单 / 侦察 SOP 增量

**侦察报告**必须包含:
1. 项目 License(含 SPDX 标识)
2. 部署模式(自托管 / 官方 SaaS / 社区 SaaS)
3. 推荐接入路径与对应法律风险
4. 是否触发 §13(改服务端 + 公开 serve)
5. 与 P3.10b 隐私红线(0 上传)是否冲突

**Phase A ship 决策必须确认**:
- [ ] License 已记录
- [ ] 三条红线 0 触发
- [ ] 不打包服务端
- [ ] 进程级隔离已设计
- [ ] 0 上传/外传已声明(符合 P3.10b)

## 相关

- 用户问询: 2026-10-10
- 拍板: AGPL-3.0 客户端代理 ship **不构成阻碍**
- 同时 ship 了 [[artcraft-skip-decision]] 作"不 ship 反例"对照(NOASSERTION + SaaS)
- Obsidian 同步: `Documents/ObsidianVault/PROJECTS/PrisirAI/扩展生态/agpl-ship-boundary-2026-10-10.md`