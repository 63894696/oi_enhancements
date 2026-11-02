# 语音模式并入主壳 · 重新规划（2026-11-01）

基于 [deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)（MIT，2026-10-31 发布的研究原型）的阅读，对"让 agent 智能最大化应用在各场景"这条目标做重新规划。

---

## 一、为什么 dsh 值得作为参照

它不是语音项目，也不是 UI 框架。它解决的是**同一个问题**：一个能力很强的 agent，怎么在有人的环境里持续干活而不变成噪音源。

它的定位是"proactive coding agent for one GitHub repository"——监听 GitHub/飞书，收到请求就开工单，在自己的 session + 自己的目录里干，回到原会话用 PR 答复，面板只显示"需要人处理"的东西。

对你的目标而言，它提供了三样你现在缺的东西：**工单化的执行单元**、**注意力预算的硬约束**、**授权与执行的分离**。

---

## 二、值得学的五件事（附 Prisir 映射）

### 2.1 把"对话"换成"工单"

dsh 的领域模型是一条链：

```
Source → Trigger → Thread → Eval(gate+assessment) → Decision
                → Mandate → Ticket → Assignment → Executor Session
                → Channel（回到原渠道汇报）
```

关键规则：

- **Trigger 不可变，接两次只产生一个 Ticket**（幂等 + 可审计）
- **Ticket 自己定义状态，不跟随 Executor 的状态**——因为一个工单可能被交接、恢复、由多个执行器跑过
- **Subscription 只给上下文，不给授权**：工单订阅了某条线程，线程上的新消息会"扇出"给工单当背景信息，但不会因此获得干活的许可
- **Mandate 不可变**：授权记录了策略版本、范围、强度、按哪条规则接的单。只有人能撤回

**Prisir 现状**：chat-first。语音、鼠标、日历、邮件全都是"聊天的一个入口"，进来就变成一轮对话，对话结束状态就丢了。

**映射建议**：把"一次语音指令"从"一轮对话"改成"一张工单"。这不是重构 UI，是换掉底层的执行单元。做完之后，"打断不丢任务""隔天接着做""跨场景交接"都是免费的。

### 2.2 注意力经济写进架构，而不是写成原则

原文第一条：

> **Take as little human attention as possible, and give as much value as possible.**

它推导出的 agent 行为约束：

> **它只在被请求时行动，或在一份写下来的规则允许时行动。**
> **它只在有人必须行动、或能用上结果时说话。**

具体到"什么时候可以开口"，只有四种情况：

| 开口理由 | 说明 |
|---|---|
| 确认一次 | 接单时说明要做什么，只说一遍 |
| 需要决策 | 卡在需要人拍板的地方 |
| 结果就绪 | 活干完了 |
| 无法完成 | blocked / failed |

**干活期间保持安静。** 不是"少说"，是"不说"。

**这条直接决定语音模式的输出策略。** 你现在 `hooks.thinking()` 无时无刻不在更新状态，浮动球不停闪——那是视觉噪音，语音照搬会变成听觉灾难。dsh 给的答案是：语音通道只允许这四类消息，其余一律走视觉（面板/浮球颜色），不进耳朵。

顺带解决上一轮我说的"填充语 4 秒"：填充语属于"确认一次"的延长，不算独立开口。超过 4 秒没结果，说一句"这个要一会儿"，然后闭嘴到结果出来。

### 2.3 授权与执行分离

`Mandate`（授权）和 `Assignment`（派工）是两个东西：

- Mandate 说明**为什么可以干、能干多远**，不可变，记录策略版本
- Assignment 是**这次谁来干**，一个工单先后可以有多个

策略（Policy）变更时有一条很克制的规则：

> 影响在途工单的策略变更**投递给执行器，但不撤销授权**。只有人能撤回授权。

以及：

> 策略变更**不取消**正在跑的工单——那会留下半成品；变更被记录并投递。

**为什么对你特别重要**：语音是所有输入通道里最容易注入的（旁边的人说一句就算）。Prisir 现在语音指令可以直接触发执行（`oi_pipeline` → `DynamicRouter` → 执行），链路上没有授权闸门。dsh 的 `gate`（确定性准入：谁可以发起、以什么强度）+ `assessment`（模型研判）两层，正好补这个洞。

### 2.4 Main Session / Executor Session 分离

- **Main Session**（`orchestrator`）：只做评估、接单、派工、回话。工具集更小。
- **Executor Session**（`worker-<ticket id>`）：在自己的 Session + 自己的目录里干活。
- 前台 shell 命令 **5 秒后自动转后台**，这样 worker 能在下一步收到新消息，而不是等长命令跑完。

这条对你有一个直接对应：**主壳不该执行，主壳只该路由**。

现在的 `agent_shell/oi_pipeline.py` 是同步阻塞跑完整条链（route → respond → idle），`_busy.acquire(blocking=False)` 一忙就丢话。按 dsh 的模型，主壳收到语音后应该：评估 → 建工单 → 派给执行器 → 立刻回去听。**工单不随会话死**，所以打断、重连、换场景都不丢活。

### 2.5 报告六态，工单阶段机

Assignment 的报告只有六种状态：

| 状态 | 含义 |
|---|---|
| `progress` | 进展 |
| `waiting` | 等待（等触发，不等人） |
| `ready` | 就绪，待人审阅 |
| `blocked` | 受阻 |
| `failed` | 失败 |
| `dropped` | 无需处理 |

工单阶段由"在等什么"决定，从上往下取第一个匹配：`person/delivery` → `person/answer` → `person/review` → `person/blocked,failed` → `working/queued` → `idle` → `working`。

还有一条设计上的克制值得抄：

> **不从报告的自由文本推断"在等人"。** 分类需要结构化事实，执行器必须用结构化字段提问。

**Prisir 现状**：`hooks.idle(result[:48])` —— 截断 48 字符，没有语义。语音是唯一无法"回看一眼确认"的通道，所以"已打开"和"发了指令但没法确认它开没开"必须能分开说。dsh 这六态是可用的最小集合。

---

## 三、语音模式并入主壳的设计

### 3.1 定位

**语音不是独立服务，是主壳的一个 Channel。**

dsh 里 Channel 是"Thread 的写侧"，答复、PR、已解决的 review 都是写。语音同理：它是工单结果回到人的**一条出口**，和 IM、面板通知并列。

所以 `companion` 不再作为独立 Web 服务存在，`agent_shell` 的浮动球退化为**指针层客户端**（orb + 热键 + focus context），不再自带语音栈。

### 3.2 一次语音交互的完整路径

```
麦克风（常开）
  → KWS 常驻（只认唤醒词，几 MB）
  → [命中] 建 Trigger
  → gate：谁在说？有没有授权？（确定性）
  → Eval：要干什么，多强，多大范围？（模型）
  → 接单 → 建 Ticket → 派 Assignment
  → 回一句"好，我去弄"（确认一次，唯一许可的开场）
  → 静默执行
  → 结果就绪 → 走 voice channel 汇报
```

关键点：

- **语音只负责发起和接收，不负责执行**
- 执行中的打断 = `stop speaking`（停声道），**不等于** `cancel ticket`
- 工单未完时不汇报，除非满足四条开口理由之一
- 用户说"算了"才是 `cancel`，且需要有权限的人说

### 3.3 打断语义要拆成两个

这是现在 `agent_shell/app.py:_interrupt` 混乱的根源——它把两件事混成一个 `Esc`：

| 动作 | 语义 | 触发 |
|---|---|---|
| `stop_speaking` | 停 TTS，工单继续跑 | 任何声音/按键，无需授权 |
| `cancel_ticket` | 终止工单 | 只有授权人能触发，需二次确认 |

顺带修掉已发现的 bug：`tts_daemon.py` 的 `/stop` 至今没有调用方，所以现在按 Esc SAPI 仍在念。`stop_speaking` 接上它就对了。

### 3.4 与 echo guard / KWS 的关系

上一轮建议的 echo guard、常驻 KWS、`<think>` 流上过滤、CJK 分句，落点全部改为**主壳内的 voice channel 实现**，不再碰 `voice_input/`（7 月孤岛）。

`companion` 的 `companion_asr_providers.py` 是现成的 provider 抽象，直接提升为共用契约：

```
asr_provider: start / push_audio / events / close
   ├─ local  (SenseVoice / prisir_asr Rust+ort)
   └─ cloud  (百炼 Paraformer)
tts_provider: 同构
   ├─ local  (SAPI 兜底 → Piper/VITS 提质)
   └─ cloud
```

**灵犀 IME 已经自带 `lingxi_voice.exe` + `sensevoice-small`**（安装目录 `voice/`，46MB），这是现成的本地语音资产，主壳可以直接复用，不必再从 `voice_input` 引。

---

## 四、场景矩阵：把现有资产挂到 Source 上

这张表就是"智能能力最大化应用在各场景"的落地形态。每一行都是"一个 Source + 一组 Trigger + 一个汇报 Channel"。

| 场景 | Source（现成资产） | Trigger 是什么 | 汇报 Channel |
|---|---|---|---|
| 语音 | 麦克风 + KWS | 唤醒 + 指令 | TTS（四条开口理由） |
| 指针/焦点 | `focus context`（待建） | 选区、悬停、复制 | 浮窗 / orb |
| 文件 | `prisir_findex` / `prisir_fcontent` | 文件变更、内容命中 | 面板 |
| 屏幕 | OCR + 截图 | 屏幕内容变化 | 浮窗 |
| 日历 | `prisIragent_calendar` | 时间到达、日程变更 | 系统通知 |
| 代码 | `prisir_work`（GitHub connector） | PR / Issue / CI | PR comment |
| 自托管服务 | 25 个 `*-bridge-status` | API 状态变化 | 面板 |
| 输入法 | `prisIragent_ime` | 输入上下文 | 候选窗 |

**这张表的价值**：它把"64 个扩展 + 6 个壳 + 4 套语音"从"一堆功能"变成"一张 Source 清单"。每加一个能力，就是加一行；判断该不该做，就问一句"它给哪个 Source 提供 Trigger"。

---

## 五、落地阶段

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **A. 清账** | 归档 7 月冻结目录、tauri、fcitx5；扩部分类清理；清隔离区 | 无 |
| **B. 定契约** | `asr_provider` / `tts_provider` 契约；`focus_context` 结构；报告六态枚举 | A |
| **C. 拆执行** | 主壳只做 Eval + 派工；工单与 Session 分离；`stop_speaking` / `cancel_ticket` 拆开 | B |
| **D. 语音入壳** | voice channel（KWS 常驻 + echo guard + 四条开口规则） | C |
| **E. 挂场景** | 按 §4 矩阵逐行接 Source，一次一行 | D |

**关键次序**：B 必须在 C 之前（没有契约就没有工单边界），C 必须在 D 之前（否则会把语音焊死在同步链路上，重演 `oi_pipeline` 的问题）。

---

## 六、不建议照抄的部分

| dsh 的做法 | 为什么不适用 |
|---|---|
| 只为"一个 GitHub 仓库"服务 | 你是多场景常驻助手，Source 种类多得多 |
| 工单目录用 `git worktree` | 你的工单大多是本机操作，不是代码改动 |
| 面板只显示待人工项 | 你的浮动球是常驻的，需要承载"健康态"信息 |
| 一切走 `DeepSeek Harness` 的 Session | 你的执行器是本地 Python/Rust 混合，接入成本高 |
| 免费开源可商用（MIT） | ✅ 这条可以用，代码可借鉴，保留版权声明 |

---

*本文只做设计规划，未修改代码。*
