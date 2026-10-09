---
name: extension-ship-filter
description: 扩展 ship 筛选标准(2026-10-10 用户拍板新增)— 没有设计给 agent 操作的就不做扩展 + 密信/含安全漏洞项目暂不 ship
metadata:
  type: reference
---

# 扩展 ship 筛选标准(2026-10-10 用户拍板更新)

**用户原话**:「关于前面项目的选择还是和之前的筛选标准一样,没有设计给 agent 操作的就不做扩展。同时把莱茵金属的开源通信项目看是否有能抽给密信做优化的模块,由于密信还有安全漏洞要修复目前也不做扩展。」

## 新增筛选标准(2026-10-10)

### 标准 6:**「没有设计给 agent 操作的就不做扩展」**

**含义**:候选项目必须**为 agent / 自动化设计有 API surface**:
- 官方 CLI 命令 + flags(可批量操作)
- 官方 HTTP REST / gRPC API(可远程调用)
- 官方 SDK / 客户端库(可编程集成)
- **不**只是 GUI 工具(纯鼠标/键盘操作)

**反例**:
- ❌ 纯 GUI 调试工具(只在 inspector 内操作,无 CLI/API 暴露)
- ❌ 一次性脚本/工具(无人机交互设计)
- ❌ 仅本地文件格式(无 RPC / 协议)

**正例**(已 ship 26 个扩展 + 新增 SimpleX):
- ✅ 26 个扩展全部有官方 REST API + token auth
- ✅ SimpleX Chat CLI 6.x 有 `--api-token` + 5225 端口 HTTP API
- ✅ Music/Notes 都有 openAPI spec

### 标准 7:**「密信/含安全漏洞项目暂不 ship」**

**含义**:产品本身有未修复的安全漏洞 → 扩展整合会让漏洞暴露面更大
- **当前不 ship**:`prisir-browser/mixin/` 密信产品层(用户原话:「由于密信还有安全漏洞要修复目前也不做扩展」)
- **不 ship 路径 B**(`mixin-bridge-status` 接 Prisir 浏览器 _PAGE)— 因为依赖密信产品层
- **仍可 ship**:SimpleX Chat CLI(独立 AGPL-3.0 客户端,**不是密信产品**,SimpleX 6.x 协议层稳定 + 安全)

## 完整筛选标准(1-7)

| # | 标准 | 来源 | 适用 |
|---|------|------|------|
| 1 | 只白名单 GET,绝不触碰写端点 | [[agpl-ship-boundary]] 基础 | 所有 |
| 2 | License 匹配客户端代理 ship 边界 | [[agpl-ship-boundary]] | 所有 |
| 3 | 部署模式 = 自托管 + 100% 本地 | PrisirAI 哲学 | 所有 |
| 4 | P3.10b 隐私红线(0 上传/外传) | [[p3-10-bubble-cancelled-privacy]] | 所有 |
| 5 | 不抓 content(笔记/聊天/凭据 metadata 类) | Trilium/HedgeDoc/SiYuan 模式 | metadata 扩展 |
| 6 | **没有设计给 agent 操作的就不做扩展**(2026-10-10) | 用户拍板 | 所有 |
| 7 | **产品本身有未修复安全漏洞暂不 ship**(2026-10-10) | 用户拍板 | 集成类扩展 |

## 决策矩阵更新

| License / 部署 | ship 决策 | 备注 |
|----------------|---------|------|
| 自托管 + MIT/Apache + 官方 CLI/API + 0 漏洞 | ✅ ship | 完全通过 7 标准 |
| 自托管 + AGPL-3.0 + 官方 CLI/API + 0 漏洞 | ✅ ship | 客户端代理不传染 |
| 自托管 + 商业 License + 官方 API | ⚠️ 看情况 | 商业集成风险 |
| 自托管 + 官方 API + **有未修复漏洞** | ❌ reject | 标准 7 |
| 嵌入式/板载 SDK + **无 HTTP API** | ❌ reject | 标准 6 + 非 HTTP |
| 闭源 SaaS + 商业 License | ❌ reject | P3.10b + 商业 |
| 闭源 SaaS + NOASSERTION/Other | ❌ reject | 法律风险 + P3.10b |
| **纯 GUI 工具(无 CLI/API)** | ❌ reject | **标准 6** |

## 决策影响回看

| 项目 | 标准 6 评估 | 标准 7 评估 | 决策 |
|------|-----------|-----------|------|
| **SimpleX Chat CLI**(本轮 ship) | ✅ 有 `--api-token` + 5225 HTTP API | ✅ 协议稳定 + 无未修漏洞 | ✅ ship |
| **Prisir 浏览器密信 `chrome://mixin`** | ✅ 有 `_PAGE` API(密信产品) | ❌ **有未修漏洞** | ❌ reject(本轮) |
| **Rheinmetall/onboardapi** | ❌ 无 HTTP API(.rmodel 私有协议) | — | ❌ reject |
| **Rheinmetall/tacticalapi** | ✅ 有 gRPC | — | ❌ reject(战场态势 + 0 客群) |
| **ArtCraft** | ✅ 有 API | ❌ SaaS | ❌ reject(P3.10b) |

## 派单 / 侦察 SOP 增量

**侦察报告**必须包含 7 字段:
1. License(含 SPDX 标识)
2. 部署模式(自托管 / 官方 SaaS / 社区 SaaS)
3. 推荐接入路径与对应法律风险
4. 是否触发 §13(改服务端 + 公开 serve)
5. 与 P3.10b 隐私红线(0 上传)是否冲突
6. **是否有官方 CLI/HTTP API/gRPC(agent 可操作性)**(2026-10-10 新增)
7. **产品本身是否有未修安全漏洞**(2026-10-10 新增)

**Phase A ship checklist**(7 项):
- [ ] License 已记录
- [ ] 三条红线 0 触发
- [ ] 不打包服务端
- [ ] 进程级隔离已设计
- [ ] 0 上传/外传已声明
- [ ] **官方 CLI/API 已确认存在且稳定**(2026-10-10 新增)
- [ ] **产品本身 0 未修高危 CVE**(2026-10-10 新增)

## 相关

- 用户拍板: 2026-10-10
- 基础决策: [[agpl-ship-boundary]] 决策矩阵
- 隐私红线: [[p3-10-bubble-cancelled-privacy]] P3.10b
- 密信产品决策: `handoff/2026-08-14-mixin-into-browser-plan.md`(2026-08-14 拍板方案)
- Rheinmetall skip: [[rheinmetall-skip-decision]]
- 同期 ship: SimpleX([[simplex-bridge-status-phase-a-shipped]] 待写)
- 同期侦察: Rheinmetall 通信项目模块抽取可行性