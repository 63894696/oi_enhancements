---
name: prisIr-companion-m366-classification-head-survey
description: M3.66 L6 agent-jev 适用范围调研 + 实测对比方案(2026-09-23)
metadata:
  type: project
---

# M3.66 L6 AgentJev 适用范围调研(2026-09-23)

## 调研结论

### AgentJev 定位(README + 论文基线)
- **官方卖点**:agent reflex — agent 循环里的快速判断层,替代 27B-70B 模型"写字解析"做 boolean/choice 决策
- **设计目标**:
  - "Is this command safe to run?" → boolean (T/F + criteria)
  - "Which tool is next?" → choice (2-255 options)
  - "Where on this rubric?" → score (2-10 levels)
- **训练数据**:`synth.py` 生成 5 类 agent question family:completion / evidence / progress / retry / risk / stop
  - 全部用 **数学精确 gold**(biased coin, Bayesian posterior, Markov chains)
  - 不用 Compose。完全靠 exact computation 出的 distribution
- **Eval 数据集**:"Typed Decisions" 400 cases × 5 questions = 2,000 questions
  - 任务类型:agent workflow 决策(测试是否通过/下一步选什么/风险评分)
  - 不是通用分类(不像 ImageNet/CIFAR)

### 跟 companion 8 scenario 对齐分析

| Companion scenario | 任务本质 | 跟 AgentJev typed-decisions 相似度 | 适合实测对比? |
|---|---|---|---|
| **perf** | 性能快照 → 5 类风险 | 中 — 都是"agent 决策风险" | **✅ 适合**(5 类 choice 接近 risk family) |
| **intents** | 用户消息 → 5 类意图 | 低 — 纯文本分类,跟 agent workflow 无关 | ⚠️ 可能 zero-shot 很差 |
| **task** | 用户消息 → 6 类任务 | 低 — 同上 | ⚠️ |
| **disk_cleanup** | Windows 路径 → 5 类风险 | 中 — 跟 perf 类似风险分类 | **✅ 适合**(单 state = 单文件元数据,跟 perf 同结构) |
| **tempfile** | 文件路径 → 5 类风险 | 中 | **✅ 适合** |
| **email** | 邮件内容 → 5 类风险 | 中 | **✅ 适合** |
| **log** | 日志条目 → 5 类风险 | 中 | **✅ 适合** |
| **safety** | 用户消息 → 安全/越狱识别 | 低 — 不是 agent workflow | ❌ |

### 关键不确定
**官方权重在结构化 5-类风险分类场景的 zero-shot 表现未知**。两种可能性:
1. **乐观**:AgentJev 训练在 ~2,000 个分布 gold 上,泛化能力强 → 5-类风险有 60-70% ACC
2. **悲观**:训练数据全是 agent workflow (completion/evidence/progress/retry/risk/stop),结构化分类如 perf/disk_cleanup 训练时没见过 → ACC 仅 25-40%(随机)

### 实测对比三种策略

#### 策略 A:**先启动官方 server 跑 zero-shot**(省钱 + 快)

**做什么**:
1. aliyun 上启动官方 HTTP server (`python -m jev_service.server --checkpoint ... --model-path /workspace/agent-jev-src --port 18765`)
2. 写 `bench_perf_agentjev_official.py`,对 perf 50 条 TEST_CASES 调官方 server
3. 输出:`official_agent_jev` 的 ACC/p50/单条 latency
4. **对比三组**:
   - perf_conf_v3 (LoRA, 78% ACC)
   - perf_ch_v1 (我们训的 AgentJev head, 20% ACC, 60 steps)
   - official_agent_jev (官方权重, 未知 ACC)

**成本**:
- aliyun server 启动 5 min + 推理 50 条 × ~0.5s = ~2 min
- 总花费:aliyun T4 仍开着 ≈ ¥0.5/h × 0.1h = ¥0.05
- 时间:15 min

**意义**:
- 验证 AgentJev zero-shot 泛化能力 → 决定 L3 是否值得做
- 如果 official ACC 70%+,我们训的 head(20%) 明显欠训 → L3 应扩到 500+ steps + 1000+ 样本
- 如果 official ACC 30-40%,说明 typed-decisions 权重不适用 → L3 应用我们自己的 scenario-specific 数据重训全套 head

#### 策略 B:**只对比结构化场景**(完整 5 scenario)

**做什么**:跟策略 A 类似,但跑 perf / disk_cleanup / tempfile / email / log 五个结构化场景各 50 条。

**成本**:
- aliyun 推理 5 × 50 条 × 0.5s = ~5 min
- 总花费:aliyun T4 ¥0.5/h × 0.3h = ¥0.15
- 时间:30 min

**意义**:
- 5 个结构化场景对比给出 AgentJev 是否**整体**适合 companion 场景的全局判断
- 5/5 都好 → 走 AgentJev 路径
- 2-3/5 好 → 混合(好的走 AgentJev,不好的留 LoRA)
- 0/5 好 → 路线不可行

#### 策略 C:**仅 paper 数字 vs 我们 bench**(零成本)

**做什么**:只对比论文里的 79.25% Top-1 / Brier 0.0448,跟 perf_conf_v3 78% / perf_ch_v1 20% 摆一起。

**意义**:**几乎没有意义** — paper 是在 Typed Decisions 上测的,我们是在 perf 上测的,不同分布。

## 推荐

**走策略 A** — 因为它是性价比最高的决策信息:
- 15 min 拿到"官方 zero-shot 在 perf 上 ACC 多高"的关键证据
- 如果官方 ACC 70%+,我们就有清晰 L3 路径(扩我们自己的 head)
- 如果官方 ACC 30%,AgentJev 路径直接砍掉,M3.66 暂停

**先调研,后决策**:目前已读完 README + synth.py + engine.py,结论是**有可执行路径**(server + HTTP API + 官方权重 2.4GB 已在 aliyun),但**零样本泛化到结构化 5-类风险未知**。

## 等用户决策

建议跑策略 A(perf 50 条 zero-shot,三组对比),再决定是否扩到策略 B(5 scenario)。

## 关键参考

- `/workspace/agent-jev-repo/README.md` — 论文官方卖点 + Eval 表
- `/workspace/agent-jev-repo/agentjev/synth.py` — 训练数据生成(精确数学 gold)
- `/workspace/agent-jev-repo/jev_service/engine.py` — 推理 server
- `/workspace/agent-jev-repo/jev_service/server.py` — HTTP API (port 18765)
- `/workspace/agent-jev-src/model.safetensors` (2.4GB) — 官方权重 step 600
- `memory/prisIr-companion-m366-classification-head.md` — 立项对比
- `memory/prisIr-companion-m366-classification-head-l2-closed.md` — L2 demo 闭环
- `companion/bench_perf_local.py` — LoRA baseline harness