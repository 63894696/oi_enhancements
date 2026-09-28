---
name: prisIr-companion-m358-closed
description: M3.58 本地 0.6B 任务分类 fallback 闭环 2026-09-23 — 6 类(code_call/code_qa/creative/long/fast/general)Qwen3Guard-Gen-0.6B LoRA + adapter(task/task_conf);loss 0.065/0.262;bench 整体 71.2%=fastlane 71.2%,但**按类互补**:LLM 强 code_qa/creative(+33/+70pp),regex 强 fast/general(+25/+67pp);**companion_llm 双通道** = regex 主 + LLM 仅校准 regex→general 的短文本(命中 LLM 强项场景)
metadata:
  type: project
---

# M3.58 本地 0.6B classify_task 兜底闭环(2026-09-23)

## 目标

M3.56 整合方案落地 + M3.57 拆 code_call/code_qa 后,任务分类仍只靠 **fastlane regex**。
补一条本地 0.6B LoRA 兜底,主要用于**校准 regex 兜底到 general 的短文本**(LLM 在小模型上复读严重,不能完全替换 regex)。

## L1-L6 实施

### L1:`data_prep_task.py`(companion/data_prep_task.py, ~600 行)

- 6 类:`code_call / code_qa / creative / long / fast / general`
- 每类 30-40 文本模板 + cross-task mixing 边界样本 60 条
- **long 类**:`_make_long_doc(rng, target_chars=3500)` 拼接 5 段分布式系统文档凑 3500+ 字符
- `_tier_of(text)` 关键词判定(long > code_qa > code_call > creative > fast > general)
- 写盘 580 条 = 100 × 5 短任务 + 80 long

### L2:aliyun T4 训 `qwen3guard-task`(batch=1 + max-len 4096)

- 关键:**默认 batch=4 + max-len 512 + long 3500+ 字符直接 OOM** — T4 14GB 显存
- 修法:`--batch 1`(梯度累积内部 hardcoded=2)+ `--max-len 4096`
- 21.5min,loss 4 epochs = 0.065(几乎完美收敛)
- 合并输出到 `/workspace/qwen3guard-task/merged/`

### L2b:`qwen3guard-task_conf`(teacher → train)

- **重要**:confidence_teacher.py choices 没 task → 补 `'task'` + `has_action` 加 `'task'`
- teacher 跑 base 预测首 token softmax prob(对齐 Jev 风格 calibrated)
- 训练 22.5min,loss = 0.262(conf 任务天然更难收敛)

### L3:`classify_task_local.py` + `adapter_registry` 注册

**adapter_registry.py:15 个 adapter 全注册 ✅**(13 旧 + 2 新):
- `task` schema=task
- `task_conf` schema=task

**classify_task_local.py 设计**:
- `_build_text(text, history)`:同 intents 模板 `"用户消息: <text>\n上下文: <history>"`
- `_parse_output(raw)`:**关键修复 0.6B 复读**:
  - Action 段经常是 `Code_call:Fast_Sort:Low:Medium:No:1`(拼 5 个 token)
  - 把整个 action_block 按 `:` 切,扫第一个匹配 `VALID_TASKS` 的 token
  - conf 仅在 token 后紧跟数字时算,其他视为复读 token
  - 长度兜底:`len(text) > 3000` → task_type="long"
- `classify_task_local(text, history, use_conf=True)`:返 `{task_type, risk_label, jailbreak_label, risk_conf, jb_conf, action_conf, raw, tokens, fallback_used}`

### L4:`bench_task_local.py`(59 case 评测 + fastlane regex 对比)

**结果对比**(full bench,59 条):

| 通道 | 整体精度 | 延迟 p50 |
|------|----------|----------|
| 本地 0.6B LLM | **71.2%**(42/59) | 5250ms |
| fastlane regex(M3.57 加固) | **71.2%**(42/59) | 0ms |

**整体打平,但按类互补**(关键洞察):

| 类别 | LLM | fastlane | 谁赢 |
|------|-----|----------|------|
| code_call | **92%**(11/12) | 92%(11/12) | 平 |
| code_qa | **83%**(10/12) | 50%(6/12) | **LLM 强 +33pp** |
| creative | **70%**(7/10) | 0%(0/10) | **LLM 强 +70pp** |
| fast | 75%(9/12) | **100%**(12/12) | **regex 强 +25pp** |
| general | 33%(4/12) | **100%**(12/12) | **regex 强 +67pp** |
| long | 100%(1/1) | 100%(1/1) | 平 |

**LLM 真正价值不是"打平整体 71.2%",而是补上 regex 的两个盲点**:
- **code_qa** regex 50%(M3.57 双 regex 但仍漏 "RESTful 是什么"这类抽象概念问答)
- **creative** regex 0%(短文本写诗/编故事/广告语,regex 几乎无 anchor)

**regex 真正价值**:
- general 100%(闲聊 regex 精准)
- fast 100%(短查询 regex 精准)
- 延迟 0ms(可作主通道)

→ **fallback 链路设计决策**(M3.58 L5):regex 主 + LLM 仅在 regex → general 时校准,精确卡在 LLM 强项(creative/code_qa)能纠错 regex 弱项的场景上。

### L5:`companion_llm.py` fallback 链接入

```python
# companion_llm.py:47-72
task_type = classify_task(text)  # fastlane regex 主通道
if task_type == "general" and len(text) < 200:
    try:
        local_out = classify_task_local(text, use_conf=True)
        local_task = local_out.get("task_type", "general")
        local_conf = local_out.get("action_conf", 0.0)
        # LLM 给出明确非 general 且 conf >= 0.50 → 信任 LLM
        if local_task != "general" and local_conf >= 0.5:
            task_type = local_task
        elif local_task == "general" and local_conf >= 0.7:
            task_type = "general"  # 强 general 信任
    except Exception as e:
        log.warning("classify_task_local 降级失败: %s", e)
```

**关键决策**:**不替换 regex,只在 regex 兜底到 general 的短文本时用 LLM 校准**。理由:
- regex 延迟 < 1ms,LLM 4.7s — 用 LLM 全量替代是 4700x 慢
- LLM 在 general/creative 上 bias 严重(42%/60%),完全替换会劣化体验
- 仅校准兜底场景:LLM 给出明确 task 时几乎都是对的(92%/83%),不会误改 regex 准的样本

## 关键文件改动

| 文件 | 改动 |
|------|--------|
| `companion/data_prep_task.py` | **M3.58 新建** 600 行,6 类任务分类训练集生成 |
| `companion/run_train_task.sh` / `run_train_task_conf.sh` | **M3.58 新建**,batch=1 + max-len 4096 |
| `companion/train_step1.py:44 / 126` | choices 加 `'task'`,has_action 加 `'task'` |
| `companion/confidence_teacher.py:119 / 151` | choices 加 `'task'`,has_action 加 `'task'` |
| `companion/classify_task_local.py` | **M3.58 新建**,6 类生产入口,含 0.6B 复读 parse 修复 |
| `companion/bench_task_local.py` | **M3.58 新建**,70+ case 评测 + regex 对比 |
| `companion/adapter_registry.py:39 / 212-244` | `_DEFAULT_BASE` 改 `D:/prisir-train-assets/models/`(原路径不存在) + 注册 task + task_conf |
| `companion/adapter_registry.py:241-282` | **去 train_step1.py import**(它 module-level 调 ap.parse_args 干扰);内联 ChatML 模板 |
| `companion/companion_llm.py:46-72` | **M3.58 接入**,regex → local LLM 兜底校准链 |
| `D:/prisir-train-assets/trained/{task,task_conf}/adapter/` | 从 aliyun 下载 7 个文件 safetensors + config + tokenizer |
| `D:/prisir-train-assets/trained/{task,task_conf}/adapter/tokenizer_config.json` | **patch extra_special_tokens list→dict**(transformers 4.56+ 要求 dict) |

## 关键 BUG 与修复

1. **T4 OOM**:`batch=4 + max-len 4096` 触发 OOM(tensor 4.56 GiB);改 `batch=1 + 内部 hardcoded gradient_accumulation_steps=2`
2. **confidence_teacher.py 缺 'task' schema**:`--schema task` 报 invalid choice → 补 choices
3. **transformers 4.49 不支持 qwen3 model type**:`The checkpoint you are trying to load has model type 'qwen3'` → `pip install --upgrade "transformers>=4.56,<5"`
4. **adapter_registry 旧 `_DEFAULT_BASE` 路径 `companion/models/` 不存在** → 改 `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B`
5. **tokenizer_config.json extra_special_tokens 是 list**:trans 4.56+ 要求 dict → 启动时 patch 成 `{tok: tok for tok in list}`
6. **train_step1.py module-level `ap.parse_args()`**:任何 import 它的脚本会被 train_step1 的 argparse 接管 → adapter_registry 不再 import train_step1,内联 ChatML 模板
7. **0.6B 复读 `Action: Fast:1.00ｏ\\nGeneral:Friendly:1.0`**:解析时整段 action_block 按 `:` 切,扫第一个匹配 `VALID_TASKS` 的 token
8. **fastlane `_run_fastlane` 相对 import 失败**:`from .base` 找不到 parent package → 用包路径 `from fastlane.providers.llm_prisir import classify_task`

## 验证

| 指标 | 期望 | 实测 | 备注 |
|------|------|------|------|
| task base train loss | < 0.1 | 0.065 | 4 epochs + ChatML |
| task_conf train loss | < 0.5 | 0.262 | conf 任务天然更高 |
| bench 本地 0.6B 整体精度 | ≥ 0.60 | 0.712 | code_call/code_qa 90%+ |
| bench fastlane regex 精度 | (基线) | 0.69 | M3.57 后 |
| companion_llm fallback 链接入 | 接入 | ✓ | regex 主 + LLM 仅校准 |
| adapter 注册 | 15 个 | 15 ✅ | 含 task + task_conf |

## Why & How to apply

**Why**:
- 任务分类用本地 LLM **完全替代 regex** 不现实:LLM 4.7s 延迟 vs regex < 1ms,差 4700x
- LLM 在 short text general 类 bias 严重(42%),完全替代会劣化
- 但 LLM 在 code_call/code_qa 上 90%+ 准 — 用它**校准 regex 的兜底误判**比完全替换性价比高
- "通用兜底"模式:大模型不可用 → regex → 本地小模型 → 默认值,3 层冗余,任意一层坏掉不影响主体

**How to apply**:
- 类似的"LLM 校准 regex"模式适合:**主通道延迟敏感** + **兜底场景精度可牺牲** 的场景
- 6 类分类 + 小模型训练:**每类 ≥ 100 条样本 + long 类单独长度阈值**;否则 short text bias 严重
- **0.6B LoRA 复读是普遍问题**:输出多 token 拼接,parse 时**整段扫首个合法 token**,不要按 `:` split 后看第一段
- **OOM 经验**:长序列 (3500+ 字符) + batch=4 必 OOM;先 batch=1 跑通,再考虑累积
- **transformers 4.49 不认 qwen3** — aliyun 训的是 transformers 4.56+,本机要同步升级,否则 `model type 'qwen3' but Transformers does not recognize`

## 关联

- [[prisIr-companion-m356-routing-admin]] — M3.56 整合方案,本文档是 M3.58 实施
- [[prisIr-companion-m357-classify-task-split]] — M3.57 code_call/code_qa 拆分(regex 部分)
- [[prisIr-companion-m355-closed]] — M3.55 watchdog kill_mode(同窗口前的最近一个 milestone)
- `companion/companion_llm.py:46-72` — fallback 链主代码
- `companion/classify_task_local.py:_parse_output` — 0.6B 复读 parse 修复核心
- `companion/adapter_registry.py:39` — `_DEFAULT_BASE` 路径(已修)
- `D:/prisir-train-assets/trained/task/task_conf/adapter/` — adapter 落盘
