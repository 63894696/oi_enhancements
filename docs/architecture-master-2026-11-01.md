# Prisir Agent 总设计 v1（2026-11-01）

> **本文是唯一现行设计文档。** 之前的 `architecture-north-star.md`、`plan-2026-11-01-voice-mode-in-main-shell.md`、`competitor-study-2026-11-01.md` 降级为参考资料（结论已并入本文）。
> 新工程一律在 `D:\Projects\` 下创建；`oi_enhancements` 保留为存量资产仓 + 设计文档仓。

---

## 1. 终局形态（用户 2026-11-01 定调）

**本地轻量壳 + 27B 能力后端，云地一体化。**

- 本地只留壳：触达、首响应（<100ms）、边界裁决
- 能力全部沉到后端，跑在 27B 运行环境（云端主用，本地 Ollama 降级兜底）
- 两端用**同一套契约**通信：断网降级而不变形
- 对标 Cursor，但差异点不在模型——在「手」和「入口」：Cursor 的世界是 repo，Prisir 的世界是整台机器（文件/窗口/输入法/屏幕/日历/自托管服务）

## 2. 三层架构与责任边界

| 层 | 职责 | 判定问题 |
|---|---|---|
| **壳** | 捕获、渲染、状态、触达 | “云端能不能替它做？”能 → 不该在壳里 |
| **契约** | 协议 spec、能力声明、权限模型 | “写不出 spec 的能力就是没想清楚” |
| **脑** | 规划、研判、编排、长任务 | “本地降级时有没有兜底？”没有 → 补契约 |

## 3. 领域模型（采 dsh，已定案）

```
Source → Trigger → Thread → Eval(gate + assessment) → Decision
                    → Mandate → Ticket → Assignment → Executor
                    → Channel（回到原渠道汇报）
```

落定规则：

- **语音/鼠标/日历/代码/自托管服务都是 Source**，统一产出 Trigger
- **Ticket 自持状态**，不随 Executor/会话死亡 → 打断不丢活，隔天可续
- **报告六态**：`progress / waiting / ready / blocked / failed / dropped`
- **四条开口理由**（注意力经济）：确认一次 / 需要决策 / 结果就绪 / 无法完成。干活期间沉默
- **打断语义拆分**：`stop_speaking`（停声道，工单继续）≠ `cancel_ticket`（终止，需授权 + 二次确认）

## 4. 壳的构成（现有资产 → 目标形态）

| 模块 | 现有资产 | 目标形态 |
|---|---|---|
| 主壳 | `prisiragent-shell`（Electron，补语音模式） | 唯一对话主界面 |
| 指针层 | `agent_shell` 浮动球 + 热键 | orb 纯客户端，无自带语音栈 |
| 输入法 | `prisIragent_ime` + `prisir_ime`（已修切分 bug） | 指针级入口 + capture 事件源 |
| **capture service** | `a11y_extract` + `prisir_fcontent` + `prisir_findex` | **合并重做为事件驱动捕获 + FTS 存储 + 三层权限门**（screenpipe 模式） |
| 桥接层 | 25 个 bridge-status + 2 indexer | `bridge-runtime` 一个运行时 + 配置条目 |
| 本地工具 | 20 个扩展 | `local-capabilities` 一个扩展 + `task-runner` 独立 |
| 语音 | `voice_input`（归档）+ `companion`（并入）+ IME 内 `lingxi_voice` | 主壳内的 voice channel |

**capture service 事件源清单**：应用切换、焦点窗口、选区、剪贴板、鼠标悬停、打字停顿、IME 输入上下文、系统音/麦克风（参考 VoiceStudio 思路，代码不入）。

**capture 存储**：SQLite + FTS5 单库装帧/文本/转录；findex 保留文件元数据索引角色。

## 5. 契约（spec 先行，采 e2b）

1. **协议 spec 是单一事实来源**：先写 spec，两端各自实现；spec 变更走 pin + 校验（防漂移）
2. **能力声明**：每个工单/引擎声明自己会什么、要什么权限；声明喂给工具列表和权限门
3. **VoiceEngine 契约**（继承 Mark-LVI 思路，落 voice channel）：
   ```
   asr_provider / tts_provider: start · push_audio · events · close
   events: { type: vad|partial|final|wake, text, ts, confidence }
   ```
   实现：local（SenseVoice/Piper）与 cloud（百炼 Paraformer）两档，运行时可切

## 6. 权限（采 screenpipe，确定性三层）

| 层 | 机制 |
|---|---|
| skill gating | 被拒的端点/能力**不进入** agent 的工具列表（学不到，就调不了） |
| agent interception | 执行前拦截，检查工单 Mandate 的 scope |
| per-source token | 每个数据源独立凭据，服务端中间件校验 |

声明式过滤：`allow-apps / deny-apps / 时间窗 / allow-raw-sql: false`。
egress policy 由此显式化：哪条链路走云、哪条必须本地，是配置不是口号。

## 7. 脑（27B 运行时，采 e2b 生命周期）

- **工单 → sandbox**：`create / pause / resume / fork / kill`；fork = 从某状态继续
- 网络白名单显式化：默认禁公网，按需开
- 阿里云抢占机跑推理 + 重型逆向（REA 工具链）；本地 Ollama 做降级兜底
- `fastlane` 升级为**模型编排 gateway**：三协议互译（OpenAI/Anthropic/Gemini）+ 订阅登录 provider + 路由组（smart/order/rotate/usage）
- **监督者-工作者分级**：supervisor（付费高智能模型）只做研判/拆解/验收；worker（免费额度路由组）做体力活；交叉审核兜底（详见 `research-2026-11-01-model-orchestration-egress-tailscale.md`）
- **免费模型能力摸底表**：标准化题库 + 自动 runner + 交叉互评，产出"任务类型 → 可用免费模型"分档表（新项目 `prisir-model-lab`）

## 7.5 egress 网关（VPS 出口保护）

**主 IP 永不直接碰目标站。** 分层出口：

| 出口 | 用途 | 风控 |
|---|---|---|
| 出口0 直连 | LLM API / 自有服务（白名单） | 主 IP 只做可信流量 |
| 出口1 Proton WG（gluetun 容器组，可多国轮换） | 常规抓取、地域解锁 | 可牺牲，被封换节点；VPN IP 在反爬库中被标记，定位是"挡枪"不是"洗白" |
| 出口2 住宅代理（按次） | 高价值/高风险任务 | token 化，按次购买 |

规则：URL 分类 → 出口选择；速率控制；失败自动降级。**Proton ToS 商用条款需人工核实后再上。**（详见 research 文档）

## 7.6 Tailscale 智能网

- **壳的定义扩展**：Tailscale 网内任意设备（手机、未来机器人）都是壳，契约的消费端不限于 Electron
- 服务发现走 MagicDNS 命名（`prisir-brain` / `prisir-egress`）
- 网内设备外网请求统一走 egress 网关，不暴露各设备家庭 IP
- 新设备接入 = 邀请 + tag（ACL 管控），27B 端点按 tag 授权

## 8. 记忆（采 Letta 分层 + dsh 归属）

| 层 | 内容 | 现状 |
|---|---|---|
| 核心记忆 | 用户红线、偏好、当前状态、规则 | **待建** |
| 档案记忆 | 任务报告、经验、phase-a 记录 | `memory/`(105 篇) + Obsidian 已有 |

规则：记忆属于**工单**而非会话；核心记忆只由授权事件改写。

## 9. 工程布局

```
D:\Projects\
├── rea\                  (已有) 逆向工具
├── VoiceStudio\          (已有) 语音参考，AGPL，仅学习/自托管
├── prisir-capture\       [待建 B 阶段] capture service（Rust）
├── prisir-contract\      [待建 C 阶段] spec + 双端 SDK
├── prisir-brain\         [待建 D 阶段] 27B 运行时 + sandbox 编排
├── prisir-model-lab\     [待建] 免费模型能力摸底（runner + 题库 + 分档表）
└── prisir-egress\        [待建] VPS 出口网关（gluetun 组 + 住宅代理 + URL 路由）
```

`oi_enhancements` 保持为：存量资产仓（PrisirAI 主产品线）+ 设计文档仓 + 大扫除现场。

## 10. 路线图与挂起队列

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 本文定稿 | ✅ 2026-11-01 |
| B | capture service（事件捕获 + FTS + 三层权限） | 下一项 |
| C | 壳脑契约（spec 先行） | 待 B |
| D | 27B 运行时（阿里云抢占机） | 待 C |
| E | 小件回补 | 排队 |

**挂起队列**（独立、低风险、随时可插）：
1. IME 候选抖动 3 行修复（整句只在击键结束时跑，不插中间候选）——需重编 `prisir_ime_tsf.dll`
2. IME 安装包重打（等 E 阶段 query 性能一起，版本升 beta.19；先修 `build_win.sh` 缺引擎 DLL 的坑）
3. 扩展清理执行：2 个半成品 promo 已隔离；4 个 promo 合并 `api-catalog`；20 工具合并 `local-capabilities`；25 桥接收敛 `bridge-runtime`
4. `voice_input`/`dynamic_router` 等 7 月冻结目录归档

---

*本文为定稿 v1；后续架构变更一律改本文并记变更日期。*
