---
name: rheinmetall-skip-decision
description: Rheinmetall/onboardapi + Rheinmetall/tacticalapi 不 ship Phase A 桥接扩展的决策 — 嵌入式 SDK + 战场态势感知,与 P3.10b 隐私红线 + 用户场景不匹配
metadata:
  type: reference
---

# Rheinmetall/* 不 ship 决策(2026-10-10)

**两个仓都真实存在**(我前一轮 WebSearch 误判"搜不到"已更正 — 应直接 curl GitHub API 验证)。**结论:不 ship 两个 Phase A 桥接扩展**。

## 项目概要(事实清单)

### onboardapi
- **GitHub**: https://github.com/Rheinmetall/onboardapi(71★/20 forks/2026-04-17 创建,2026-09-10 最后 push)
- **描述**: "Onboardapi data-model and middleware for **distributed modular architectures**"
- **应用场景**: 传感器网络 / 板载通信 / 机器人系统 / 嵌入式分布式 / 服务化边缘平台
- **License 双轨**:
  - `.rmodel` 数据模型接口定义 = **EPL-2.0**(Eclipse Public License 2.0,Apache 系 OS-approved)
  - 预编译 runtime libraries(C++/Python/C#/Java)= **Custom EULA-RME-SDK-1.0**(专有,NOASSERTION)
  - README 原话: "Network communication is only possible through the provided libraries"
- **协议**: **不是 HTTP REST** — 自定义 `.rmodel` 数据模型 + 预编译 SDK 库
- **相关仓**: onboardapi-inspector(8★,Graphical debugging),onboardapi-digital-twin(13★,playground)

### tacticalapi
- **GitHub**: https://github.com/Rheinmetall/tacticalapi(37★/15 forks/C#/2026-05-04 创建,2026-09-01 最后 push)
- **描述**: "The TacticalAPI is an interface from Rheinmetall providing access to **situational awareness systems**"
- **License**: **EPL-2.0**(单轨,干净)
- **协议**: C# 项目,主流接口是 Protobuf / gRPC
- **应用场景**: 战场/部队/装备位置共享(态势感知 = military situational awareness)

## 与 PrisirAI 已 ship 26 个扩展的查重

**完全无重复**(已 ship 26 扩展 0 命中):
- 26 个扩展全部 HTTP REST
- 26 个扩展无任何嵌入式/板载/机器人/态势感知域
- 4 套 SDK(bearer / custom-auth / subsonic / sqlite-reader)全部不适用 — onboardapi 不是 HTTP,tacticalapi 走 gRPC

## 决策:不 ship(双仓均 reject)

| 字段 | onboardapi | tacticalapi |
|------|-----------|-------------|
| License | 双 License,runtime 专有 ⚠️ | EPL-2.0 ✅ |
| 部署模式 | **嵌入式/板载 SDK**,非服务器 | **态势感知系统接口**,通常私有部署 |
| §13 AGPL | 不适用 | — |
| P3.10b 隐私 | 本地数据,但**用户 0 场景** | **战场/部队位置共享 = 涉敏/涉密** ❌ |
| 用户场景 | PrisirAI 0 用户是机器人工程师 | PrisirAI 0 用户是军事机构 |
| 自定义 EULA | runtime NOASSERTION ⚠️ | 无 |
| SDK 边界 | 0 边界,inline gRPC/Protobuf | 0 边界,gRPC 客户端 |
| 决策 | ❌ reject | ❌ reject |

## 与 ArtCraft skip 决策的对照

- **ArtCraft**:SaaS + License NOASSERTION + P3.10b 冲突
- **onboardapi**:非 HTTP + 自定义 EULA + 0 用户场景
- **tacticalapi**:EPL-2.0 协议干净 + 战场态势感知 P3.10b 冲突 + 0 用户客群

3 个都是"协议/合规/客群/技术"中至少 2 项不达标,标准是 [[agpl-ship-boundary]] 决策矩阵。

## 关键教训(我前一轮误判的更正)

**我前一轮 WebSearch 误判"两个仓都搜不到"是不准的** — WebSearch 对 GitHub 直链覆盖有限,应**直接 curl GitHub API**(`api.github.com/orgs/<org>/repos` 即可列所有 public repos,带完整 metadata)。

**修正后 SOP**(给未来侦察参考):
1. 用户给 GitHub URL → 优先 `curl -L` + `curl api.github.com` 直拉 metadata(stars/forks/license/description/last pushed)
2. WebSearch 仅作辅证
3. WebFetch(本会话工具)对 GitHub domain 报"unable to verify",需用 curl 绕过

## 推荐替代(给未来军工/嵌入式/战场类候选参考)

**不 ship 任何军用/政府/受控 SDK 类项目** — 客户群不在 PrisirAI 目标(个人 / 小团队 / 知识工作者)。

## 决策

**不 ship rheinmetall-onboardapi-bridge-status 扩展**。
**不 ship rheinmetall-tacticalapi-bridge-status 扩展**。

写本档供未来被人问起 Rheinmetall / 军工 / 嵌入式 SDK 类项目时直接引用。

## 相关

- 用户问询: 2026-10-10
- 用户给的文档站 URL 是 `rheinmetall.github.io/onboardapi-document`(拼错),实际是 `rheinmetall.github.io/onboardapi-documentation`
- 与 [[artcraft-skip-decision]] 同档位 reject
- 决策依据: [[agpl-ship-boundary]] 决策矩阵
- 隐私红线: [[p3-10-bubble-cancelled-privacy]] P3.10b 0 上传/外传
- 已 ship 26 个 Phase A 扩展,跨入 15 类域 — **仍无重复**