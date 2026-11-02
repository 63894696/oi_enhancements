# 竞品架构研究 → Prisir 裁决（2026-11-01）

> 调研对象 8 个：Cursor、opencode、screenpipe、e2b、Letta、dsh-superagent、xiaozhi-hub-server、Mark-LVI。
> 视角：只用「壳 / 契约 / 脑」三层（见 `architecture-north-star.md`）提问——它怎么切壳与脑？上下文怎么来？权限怎么定？记忆怎么存？
> 每条给一个**可复现的设计**，落一张裁决表。

---

## 一、每个竞品最重要的一个设计

### 1. Cursor — shadow workspace（本地壳的验证隔离）

AI 改代码 → 不想污染用户真实工作区、又想要完整 LSP/lint 反馈 → **开一个隐藏的 shadow 窗口**，把 AI 的编辑应用在隐藏窗口里，跑完 lints 再把结果传回来，AI 迭代后再展示给用户。

**可复现点**：壳里凡是"agent 想自己试一下"的动作，都放在**隔离的工作副本**里做，只有"可以给人看"的结果才进主界面。这是"本地壳"最值得抄的一条——它定义了壳的双重身份：**展示面 + 试错场**。

对 Prisir 的映射：工单执行放独立工作目录（dsh 模式）就是同一件事；再把"验证"这步显式化——agent 说"做好了"之前，先在隔离区跑过验证。

### 2. opencode — 一个 codebase，三种壳 + build/plan 双 agent

- 同一个核心（monorepo 的 `core`），交付 CLI、桌面 App、Web console 三种形态
- 内置 **build**（全权限干活）与 **plan**（只读探索，跑命令前要许可）两个 agent
- 能力注册 = MCP + providers + plugins 三层；`@general` 在消息里引子代理

**可复现点**：壳和脑的切分已经有人做成了产品——**同一个脑，多个壳**。这直接验证了北星架构：Prisir 的脑（27B 后端）应该对 orb、Electron 壳、IME、语音一视同仁；壳的差异只在"入口形态"。

plan agent 的"只读 + 命令前许可"也值得照搬：**探索类工单默认只读**。

### 3. screenpipe — 事件驱动捕获 + 确定性三层权限（本调研最大收获）

捕获层：

- **不轮询，等事件**：应用切换、点击、打字停顿、滚动、剪贴板 → 事件发生时同时抓 screenshot + accessibility tree（同一时间戳）
- **a11y 树优先，OCR 兜底**（远程桌面/游戏才走 OCR）
- 全部入 **SQLite + FTS5**，本地优先，可加密落盘

权限层（给 agent 上下文时的三层确定性门）：

1. **skill gating** — AI 永远学不到被拒的端点（技能列表本身就被过滤）
2. **agent interception** — 执行前拦截
3. **per-pipe 加密 token + 服务端中间件**

外加 YAML 声明式过滤：`allow-apps` / `deny-apps` / 时间窗口 / `allow-raw-sql: false`。

**可复现点**：这是 Prisir「手眼耳」层的完整参考答案。你现有的 `a11y_extract`（7 月冻结）+ `prisir_fcontent`（OCR）+ `prisir_findex`（索引）三件事，screenpipe 用**一个事件驱动的 capture service + FTS 存储 + 权限门**解决了，而且给出了"哪些数据允许进 AI 上下文"的确定性答案——正是你在 ADR-001 里追求的"边界"，从口号变成了可执行的过滤器。

### 4. e2b — 云端沙箱的生命周期与 spec 单一来源

- Sandbox 生命周期：`create / pause / resume / fork / kill`——**fork 意味着"从某个状态继续"**，不是每次从零
- 沙箱内 `envd` daemon 暴露文件系统 + 代码执行
- 网络策略**显式化**：`allow_public_traffic`、`allow_out` 白名单、流量令牌
- SDK 从 OpenAPI spec 自动生成，spec 从上游用 pin + CI 校验同步（防漂移）

**可复现点**：这是"27B 运行环境"的搭建参考。你的阿里云抢占机 = 一个自建 e2b：**工单 → sandbox（可暂停、可 fork、可杀）→ 结果回壳**。关键取舍是生命周期语义和网络白名单的显式化——直接抄。

### 5. Letta — memory blocks + channels

- 记忆分成**块**（memory blocks）：核心记忆（我是谁/规则）与档案记忆（过去的经验），独立于对话历史
- 记忆随 agent 跨会话、跨设备（cloud sync）
- 渠道层：Slack/Telegram/Discord/自定义渠道都是同一 agent 的不同入口

**可复现点**：你的 `memory/`（105 篇）+ Obsidian 已经是"档案记忆"的雏形，但**没有"核心记忆"层**（用户偏好、红线、当前项目状态），也没有块级管理。Letta 给出分层；dsh 给出"记忆属于工单而非会话"。

### 6. dsh-superagent — 工单 + 注意力经济（已在前文档详述）

Trigger→Thread→Eval→Ticket→Assignment→Channel；只在四种情况开口。

### 7. xiaozhi-hub-server — 语音网关的"叫名字路由 + 待汇报箱"（已详述）

### 8. Mark-LVI — 引擎契约 + 延迟工程（已详述；许可证 CC BY-NC，只学思路）

---

## 二、裁决表：什么进 Prisir，进哪一层，怎么落

| 模式 | 来源 | 落层 | 怎么落 | 明确不抄的部分 |
|---|---|---|---|---|
| 事件驱动捕获（a11y 优先 + OCR 兜底） | screenpipe | 壳 | `a11y_extract` + `fcontent` + `findex` 统一成一个 capture service，事件源含：焦点窗口、选区、剪贴板、IME 输入上下文 | 不抄它的 Tauri UI |
| FTS 统一存储 | screenpipe | 壳 | SQLite + FTS5 一个库装帧/文本/转录 | 保留 findex 的文件元数据索引，不合并 |
| 确定性三层权限门 | screenpipe | 契约 | skill gating + 拦截 + per-source token；能力声明喂给工具列表 | prompt 里"不要乱看"这种软约束 |
| 双壳双 agent（build/plan） | opencode | 契约 | 每个工单声明强度：只读/可写/可执行；探索默认只读 | — |
| shadow workspace | Cursor | 壳+契约 | 工单在独立工作目录执行，验证通过才进主壳 | 不抄隐藏窗口 hack（Electron 特有问题） |
| sandbox 生命周期 + fork | e2b | 脑 | 27B 运行时按工单起 sandbox，可暂停/fork/杀；网络白名单显式 | 不照搬 Dockerfile 模板体系 |
| spec 单一来源 + codegen | e2b | 契约 | 壳与脑之间的协议先写 spec，两端生成 | — |
| memory blocks 分层 | Letta | 脑 | 核心记忆（红线/偏好/状态）+ 档案记忆（memory/+Obsidian）分块 | 不上 Letta 服务，自建 |
| 工单/授权/报告六态 | dsh | 脑 | 已在 plan 文档定案 | — |
| 名字路由 + 待汇报箱 | xiaozhi | 壳 | 语音 channel 定案内容 | — |
| 引擎契约 + 延迟工程 | Mark-LVI | 契约 | warm-up、分级冷却、诚实结果 | 代码（CC BY-NC） |

---

## 三、对现有资产的再裁决（大扫除语境下的增量结论）

| 现有资产 | 之前的裁决 | 现在（基于竞品） |
|---|---|---|
| `a11y_extract`（7 月冻结） | 归档 | **反转：它是 capture service 的起点**，screenpipe 证明这个方向对，不该归档，该按事件驱动模式重做 |
| `prisir_fcontent` / `prisir_findex` | 保留 | 保留，但**并入 capture/index service 的存储层**（FTS5 一个库） |
| 25 个 bridge | 收敛成 runtime | 不变 |
| 语音（voice_input/companion） | 并入主壳 channel | 不变，且 screenpipe 的 audio capture（系统音+麦克风+说话人分离）是语音 channel 的补充参考 |
| IME | 壳的核心入口 | 不变，IME 的"输入上下文"成为 capture service 的一个事件源 |
| memory/ + Obsidian | 保留 | 升级为 Letta 式分层：核心记忆（新）+ 档案记忆（现状） |

**一个值得注意的巧合**：screenpipe 的 Pipes（markdown 定义的定时 agent）和你的 `memory/*-phase-a-shipped.md` + 扩展工厂是同一个思路——"声明式定义一批小 agent"。你们不约而同，但 screenpipe 多做了一层：**每个 pipe 有 OS 级的确定性权限声明**。这正是你缺的那层。

---

## 四、优先级重排（本轮结论）

原顺序是"先修 IME 小件，再合并扩展"。竞品研究后的新顺序：

| 阶段 | 内容 | 为什么提前/推后 |
|---|---|---|
| **A 架构定稿** | 本文 + north star + 语音 plan 合并成一份总设计 | 用户定调：架构优先于小改动 |
| **B capture service** | 事件驱动捕获 + FTS 存储 + 三层权限门（screenpipe 模式） | **它是所有场景的上下文来源**，比语音、比扩展合并都基础 |
| **C 壳脑契约** | spec 先行：Session/Ticket/Trigger/能力声明 | e2b 经验：协议漂移是最大的隐性成本 |
| **D 27B 运行时** | 阿里云抢占机 + sandbox 生命周期（e2b 模式） | 需要 B/C 定义好边界后才有意义 |
| **E 回到小件** | IME 候选抖动（3 行）、扩展合并、语音 channel | 这些小件在 B 之后做，很多会自动变简单 |

**IME 那个 3 行修复保持在队列里**，它是独立、低风险、随时可插的；但不再插队。

---

## 五、下一步建议

1. 你可以先看 `screenpipe` 的 `pipes` 权限模型和 `apps/` 结构——它跟我们方向最像，值得深读
2. 阿里云抢占机建议留到 D 阶段：现在用它跑 27B 推理实验（Ollama 本地只有小模型），验证"脑"的最小可行形态
3. 确认后，我把 A 阶段的总设计文档写出来（合并三份文档，删掉已过时的内容）

---

## 六、补记（2026-11-01 14:35）：本地研究资源盘点

| 资源 | 位置 | 用途 | 许可注意 |
|---|---|---|---|
| **REA** (Reverse Engineer Anything) | `D:\Projects\rea`（morluto/rea，今天导入） | 逆向解剖竞品：二进制/应用/运行时行为，MCP 工具目录 | MIT ✅ |
| **VoiceStudio** | `D:\Projects\VoiceStudio`（debpalash，今天导入，v0.5.7） | 本地 AI 语音工作台：克隆/设计/配音/听写/转录/叙述，646 语言 | **AGPL-3.0** ⚠️ 只可学思路或独立自托管，代码不可并入 |
| 竞品 IDE | 本机 Cursor / Kiro / MiniMax Code×3 / opencode desktop / Grok Build Desktop / Kimi | 日常对比与逆向 | 闭源 |
| 本地模型 | Ollama | 壳侧降级兜底 | — |
| 云算力 | 阿里云抢占式实例 | D 阶段 27B 运行时 + 逆向重型任务 | — |
| 新项目目录 | `D:\Projects\`（用户授权自行建目录） | 后续 capture / contract / brain 工程落位 | — |

**REA 的用法建议**：对 closed-source 竞品（Cursor、Kiro、MiniMax Code）做行为级逆向时，REA 提供 GHIDRA/运行时 hook 等工具链；对开源项目直接读源码更快。逆向结论只用于**架构借鉴**，不复制任何专有实现。

*调研基于各仓库 README/公开资料，Cursor 基于其官方博客（shadow workspace）与公开信息。*
