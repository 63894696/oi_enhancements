---
name: rheinmetall-modulerecon-decision
description: Rheinmetall 通信项目模块抽取可行性侦察结论(2026-10-10)— 无可抽取模块给密信(SimpleX 协议)做优化
metadata:
  type: reference
---

# Rheinmetall 通信项目模块抽取可行性侦察 — 结论:不抽取

**2026-10-10 决策:不抽取任何 Rheinmetall 通信项目模块给 `prisir-browser/mixin/`(SimpleX 协议集成)做优化。**

## 用户原话(2026-10-10)

> 「同时把莱茵金属的开源通信项目看是否有能抽给密信做优化的模块,由于密信还有安全漏洞要修复目前也不做扩展。」

## 侦察范围

- `Rheinmetall/onboardapi`(71★,双 License:数据模型 EPL-2.0 + runtime 专有 EULA)
- `Rheinmetall/tacticalapi`(37★,C#,EPL-2.0 / BSD-3-Clause 双重许可)
- `Rheinmetall/onboardapi-inspector`(8★,GUI 调试器,非抽取目标)
- `Rheinmetall/onboardapi-digital-twin`(13★,playground,非抽取目标)
- Rheinmetall org 全部 4 个公开仓 — **无其他通信/加密相关仓**

## 核心结论

**无可抽取模块**。3 个看似候选的模块,经深入分析后全部 **价值≈0**:

### 候选 1:`onboardapi/datamodel/*.rmodel` 103 文件 IDL(EPL-2.0)

- 价值:0
- 理由:
  - **README 明示**「.rmodel 数据模型仅用于文档,不反映 communication layer 的真实接口」
  - 结构是 CORBA/IDL 风格的服务接口定义,非信令/加密协议
  - **抽取它拿不到传输协议逻辑**,只拿到文档级数据形状

### 候选 2:`onboardapi/CommonTypes.rmodel::EncryptionType`(EPL-2.0)

- 价值:极低
- 理由:
  - 只是字段命名提示(Algorithm/Token/Aad/Iv/Mac/Nounce),**无协议逻辑**
  - SimpleX 已有 X3DH + 双棘轮 + NaCl crypto_box + AEAD envelope,不需要借鉴字段命名
  - EPL-2.0 法律上可抄,但**无技术收益**

### 候选 3:`tacticalapi/proto/*.proto` gRPC 服务契约(EPL-2.0 / BSD-3-Clause)

- 价值:低
- 理由:
  - 3 个 RPC:`Situation` / `BlueForceTracking` / `OwnPose` — **军用态势感知**
  - 纯 gRPC 契约,**无加密/无棘轮/无信令**,跟 SimpleX 的 Noise Protocol + 双棘轮无技术重叠
  - 战术符号表(APP-6 / MIL-2525)民用聊天无价值
  - License 干净但**架构哲学互斥**:onboardapi = 有服务端的服务化推送;SimpleX = 无服务端 + E2E

### 候选 4:`onboardapi` 二进制 runtime(EULA-RME-SDK-1.0 专有)

- 价值:不可抽取
- 理由:
  - §3 仅允许动态链接未修改软件于独立应用
  - §4 严禁修改/翻译/重构/合并/补丁/分叉
  - §5 严禁反编译
  - §6 生成代码视为软件一部分不可独立提取
  - §14 适用德国法律
  - README 明示**出口管制风险**(德/EU + 类似 ITAR)

## 设计哲学互斥(关键)

| 维度 | Rheinmetall/* | SimpleX(密信) |
|------|-------------|---------------|
| 服务端假设 | 有可信任的态势感知服务器 | **无中心**(SMP 代理看不到内容) |
| 通信模型 | 服务化推送(pub/sub) | 双棘轮 E2E + 一次性 link |
| 加密 | TLS/gRPC | Noise Protocol + X3DH + 双棘轮 |
| 用户 ID | 设备 ID | **无 ID**(每会话独立) |
| 客群 | 嵌入式/军用 | 民用跨设备通信 |

**两者设计哲学反模式**,即使技术上能塞进 mixin,也违背 SimpleX 用户的核心需求(不信任任何第三方服务器)。

## 4 个集成路径全部否决

| 路径 | 评估 |
|------|------|
| A. 直接 import DLL/SO(进程级隔离) | ❌ EULA §3 限制 + 零功能价值 |
| B. FFI 桥(类似 libsimplex 模式) | ❌ 与 A 同问题 |
| C. 重写(参考实现,不直接集成) | ❌ 可行但没必要 — SimpleX 已有类似架构 |
| D. **不抽取,记入「无价值」档案**(实际推荐) | ✅ 关闭侦察线 |

## 决策:不抽取

不抽取任何模块给 `prisir-browser/mixin/`。**侦察结论 = 无可抽取模块**。

## 下一侦察目标(给未来)

- **hs-libsimplex**(Haskell libsimplex 源码)— 密信已用,可深度学习
- **Matrix**(Synapse,Apache-2.0)— 联邦协议替代
- **libsignal-rust**(AGPL-3.0)— Signal 协议 Rust 实现
- **MLS(RFC 9420)**— Messaging Layer Security 标准
- **Noise Protocol**— SimpleX 底层加密框架

## 风险与红线

- **合规**: EULA-RME-SDK-1.0 严禁衍生 + 出口管制风险
- **架构反向**: 有服务端 vs 无服务端,反 SimpleX 哲学
- **用户决策点**:
  - 接受「不抽取」→ 关闭侦察线
  - 不接受 → 用户需明确「要抽哪个模块、用于哪个能力、接受哪些红线」

## 相关

- 用户问询: 2026-10-10
- 基础: [[rheinmetall-skip-decision]](扩展 ship 决策)
- 密信产品: `prisir-browser/mixin/`(2026-08-14 拍板方案,[[extension-ship-filter]] 标"含未修漏洞,暂不 ship")
- 同期 ship: [[simplex-bridge-status-phase-a-shipped]] + [[extension-ship-filter]]