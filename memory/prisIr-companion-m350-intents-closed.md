---
name: prisIr-companion-m350-intents-closed
description: M3.50 聊天意图分类闭环 — 本地 intents adapter 精度 0.88 兜底 Jev(0.94 在线)离线场景
metadata:
  type: project
---

# M3.50 聊天意图分类闭环(2026-09-23)

## 完整流程

L1: `data_prep_intents.py` — 5 类(chat/code/search/tool_call/roleplay)+ cross-intent mixing 50 条 + 600 条总样本,每类 120 cap
L2: 训 intents base adapter — 4 epochs / 300 steps / ~5min,loss 收敛,期间多次 OOM warning 但都回收
L3: 训 intents_conf adapter — confidence_teacher 用 base 跑 data_intents.jsonl → data_intents_conf.jsonl(149KB)+ 同 base 训
L4: 注册 intents + intents_conf + 写 `classify_intents.py`(复用 classify_log.py 的 _parse_output 模式)
L5: 写 `bench_intents_local.py`(复用 bench_intents.py:47-107 的 50 条 TEST_CASES)+ 在 aliyun 上跑(本地 torchvision 冲突)
L6: 改 `companion_jev.py:518 ask_intent()` 加本地 intents adapter fallback 段

## L5 评测结果

```
整体精度:  88.0%   (44/50)
parse_fail: 6.0%   (3 条 roleplay 未 parse — "继续讲..." / "你是海盗..." / "模仿苏东坡...")
延迟 p50:   2330ms (aliyun T4 4bit;本地 GPU 应当 < 100ms)
```

**每类精度**(对齐 Jev 基线 0.94):
```
  chat       9/10   (90%)
  code       9/10   (90%)
  search     9/10   (90%)
  tool_call  10/10  (100%)
  roleplay   7/10   (70%)
```

**对比 Jev**(bench_intents_2026-09-22.json):
| 指标 | Jev | 本地 intents_conf | 备注 |
|------|-----|------------------|------|
| accuracy | 0.94 | **0.88** | -6% 可接受 |
| parse_fail | 0% | 6% | 3 条 roleplay 边界样本 |
| p50 latency | 1147ms | 2330ms(aliyun 4bit) | **本地 GPU 应 < 100ms** |
| 离线可用 | ❌ | ✅ | **关键收益** |
| 单次成本 | $0.0005 | $0 | **节省** |

**关键误判样本**:
- "今天天气真好呀" → search(本应 chat):关键词"天气"被搜素 anchor 误抓
- "解释下 React 的 useEffect" → search("解释"被搜素 anchor 误抓)
- "怎么跟女朋友道歉比较好" → chat("道歉"模糊边界)

## 关键修复点

### 1. `train_step1.py:44 / 123` — 扩 schema choices
```python
ap.add_argument("--schema", ..., choices=[..., "log", "intents"], ...)  # +intents
has_action = schema in (..., "log", "intents")  # +intents
```
**踩坑**:aliyun 上 train_step1.py 是老版本,必须先 sftp.put 覆盖,否则 choices 报 invalid choice。

### 2. `confidence_teacher.py:119-121 / 151` — 同上扩 schema
```python
ap.add_argument("--schema", choices=[..., "log", "intents"], ...)
has_action = args.schema in (..., "log", "intents")
```

### 3. 改 `ask_intent()` 加 fallback
```python
async def ask_intent(user_text, ...):
    # 主通道:Jev(原有)
    judgments = await try_jev(state, questions=INTENT_QUESTIONS, ...)
    if judgments and isinstance(judgments.get("intent"), dict):
        return {**judgments["intent"], "fallback_used": False}

    # 备通道:本地 intents adapter(M3.50 新增)
    try:
        from classify_intents import classify_intents as _local
        parsed = _local(user_text, use_conf=True)
        if parsed and parsed.get("intent") != "unknown":
            return {
                "choice": parsed["intent"],
                "confidence": float(parsed.get("action_conf", 0.5)),
                "probabilities": {parsed["intent"]: float(parsed.get("action_conf", 0.5))},
                "fallback_used": True,
            }
    except Exception:
        pass
    return None
```

### 4. `adapter_registry.py` 增 2 个 spec(intents + intents_conf)

### 5. `classify_intents.py` 生产 caller
- `_build_text(text, history)` → "用户消息: <text>\n上下文: <history>"
- `_parse_output(raw)` → {intent, risk_label, jailbreak_label, *conf}
- 主入口 `classify_intents(text, history="", use_conf=True)`

## 踩坑清单

1. **CRLF 已是历史痛点**:train_step1.py 在 git 之前是 CRLF,Edit 工具改不动,后来重写过应该 OK;但上 aliyun 仍然要 sftp.put 重新覆盖老版本
2. **confidence_teacher OOM**:因为 train_step1 进程没退,显存 14817MB 一直占着;`pkill -9 -f train_step1.py` 后 teacher 才能跑
3. **paramiko listdir 神秘 ENOENT**:`qwen3guard-intents-conf` 用 listdir 失败,但用 listdir_attr 成功 — 怀疑是某些边缘 case,统一改用 listdir_attr + a.st_size
4. **aliyun 跑 bench 路径问题**:`_DEFAULT_BASE` 写死了 `companion/models/...`,实际 base 在 `/workspace/models/...`;本地路径 `D:/...` 在 aliyun 上不存在 → bench 报 OSError
   - **解法**:`replace(spec, base_model=Path('/workspace/models/Qwen3Guard-Gen-0.6B'))`(frozen dataclass 不能 attr 赋值,用 dataclasses.replace)
5. **parse 失败集中 roleplay**:"继续讲..." / "你是海盗..." / "模仿苏东坡..." 三条样本 raw 输出含 \n 复读,parse 没抓到最后 action
   - 后续可加 _parse_output 的复读保护(M3.49 log 已有同问题)

## 关键文件

- `companion/data_prep_intents.py` — 训练数据生成器
- `companion/classify_intents.py` — 生产 caller
- `companion/bench_intents_local.py` — 本地评测 harness
- `companion/adapter_registry.py` — 注册 intents + intents_conf
- `companion/companion_jev.py:518` — ask_intent 加 fallback 段
- `companion/train_step1.py:44,123` — 扩 intents schema
- `companion/confidence_teacher.py:119-121,151` — 同上
- `run_train_intents_v1.sh` / `run_train_intents_conf_v1.sh` — aliyun 训脚本
- `data_prep_intents.py --limit 600 --mix-count 50` — 600 条 by_intent 各 120
- `D:/prisir-train-assets/trained/intents/adapter/` — 18MB base(已下载)
- `D:/prisir-train-assets/trained/intents_conf/adapter/` — 18MB conf(已下载)
- `companion/reports/bench_intents_local_2026-09-23.json` — 本地 bench 报告

## aliyun 状态

- 实例 ap-southeast-1 继续保留(用户指示"先不用关实例")
- T4 GPU 现在空闲
- 下个训练目标:重训 9 个旧 adapter 用 ChatML(预估 30min,自动收益)或 新场景

## 相关 memory

- [[prisIr-companion-m345-p01-intent]] — M3.45 P0-1 意图分发初版(Jev)
- [[prisIr-companion-m349-l6-chatml]] — ChatML 模板修复(intents 自动继承)
- [[prisIr-companion-m349-l-closed]] — M3.49 log 闭环(同样 ChatML + bench 模式)
- [[prisIr-companion-m345-step1-train-deploy]] — 训练 + 部署模板

**Why:** 完成 M3.50 L1-L7 闭环,从数据 → 训 → 评测 → fallback 接入。本地 intents adapter 精度 0.88 兜底 Jev 0.94,关键收益是离线可用 + 单次成本 0。
**How to apply:** 以后给 Qwen3Guard 加新场景 schema,直接改 train_step1.py / confidence_teacher.py 的 choices 和 has_action 即可(2 处硬编码);bench 必须有路径 override 模板(aliyun 上 base/adapter 路径都跟本地不同)。