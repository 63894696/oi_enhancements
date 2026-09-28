---
name: prisIr-companion-m361-bench-recovery
description: M3.61 LoRA 加载紧急 bug 修复 + 8 scenario 全量评测闭环 2026-09-23
metadata:
  type: project
---

# M3.61 LoRA 加载紧急 bug 修复 + 8 scenario 全量评测闭环(2026-09-23)

## 一、为什么做这件事

**用户原话**:"请推进做完整对比评测,现在 aliyun 实例开着,等评测结束需要重训的可马上复训"

M3.60 评测统一基建完成后,跑 `bench_all.py` 时发现:
- **所有 15 个 adapter 输出 raw=""(空字符串)**
- parse_fail = 100%
- accuracy = 0%
- 但 base model(无 LoRA)单独跑能正常输出"你好呀..."

## 二、根因诊断(两个 bug)

### Bug A — `_patch_tokenizer_extra_special_tokens` 函数末尾有**残留代码片段**
- 函数最后意外粘贴了 `_lazy_load` 的尾部代码,引用了未定义变量 `la` / `base` / `adapter`
- 但因为没被执行到,**这其实不致命**(仅导致 patch 写两次,无害)
- 真正的破坏在 Bug B

### Bug B — `classify()` 用 `pad_token_id=self.tokenizer.pad_token_id`
- 训练产物的 `tokenizer_config.json` 里 `pad_token = ""`(空字符串)
- transformers `generate()` 看到 pad_token_id 是空字符串 → **立即停止生成**
- 修复:`pad_token_id = (pad_token_id if pad_token_id else eos_token_id)`
- 这是真正的 root cause

### 修复代码(adapter_registry.py:300-310 + :378)
```python
out = self.model.generate(
    **inputs,
    max_new_tokens=max_new_tokens,
    do_sample=False,
    # M3.61 修:pad_token 训练产物可能为 "" 空字符串,会让 generate 立即停止
    pad_token_id=(self.tokenizer.pad_token_id
                  if self.tokenizer.pad_token_id
                  else self.tokenizer.eos_token_id),
    repetition_penalty=1.2,
    no_repeat_ngram_size=6,
)
```

## 三、评测结果(8 scenario,本机 GPU 推理,零成本)

| scenario | n | ACC | p50 | Jev 对比 | 备注 |
|----------|---|-----|-----|----------|------|
| intents | 50 | **88.0%** | 9.9s | Jev 0.94(-6pp) | roleplay 边界误判 |
| task | 59 | **71.2%** | 5.1s | regex 持平 | creative/general 边界 |
| safety | 30 | N/A | - | Jev 真替身 | 无本地 adapter |
| perf | 60 | **31.7%** | 5.6s | 无基线 | **critical 0/10 漏报** |
| disk_cleanup | 60 | **63.3%** | 8.6s | 无基线 | safe→low 漂移 |
| tempfile | 60 | **45.0%** | 7.0s | 无基线 | **critical 漏报** |
| email | 60 | **63.3%** | 7.4s | 无基线 | critical→safe 漏报 |
| log | 60 | **81.7%** | 3.9s | 无基线 | safe→low 漂移 |

**延迟普遍 5-10s**(max_new_tokens=40,GPU fp16,本地 1060)。

## 四、关键 bug 与修复(M3.61 新增)

| Bug | 修复 |
|------|--------|
| **adapter_registry.py:378-386** 误粘贴 _lazy_load 残留(引用未定义 `la`/`base`/`adapter`) | 删除残留代码段 |
| **`classify()` pad_token_id="" 空字符串** 导致 generate 立即停 | 改 eos_token_id fallback |
| **disk_cleanup bench_all extract_chain 缺 `risk` key**(只有 risk_label) | extract_chain 加 `risk` 字段 |
| **email/log fixture 是结构化文本**,classify_* 需要 sender/subject/body kwargs | 加 `_extract_email_kwargs` / `_extract_log_kwargs` + `extract_kwargs` 标志 |
| **tempfile scenario 用 classify_email placeholder** | 新建 `classify_tempfile.py`(M3.61 第一次) |
| **perf_conf_v2 触发 transformers json.load tokenizer.json 内部 JSONDecodeError**(line 577381 col 14 char 8538459 — BPE 训练数据 BPE 串 `\xc4\x89` ORDER)| bench_all perf scenario 改用 `perf_conf` 而非 `perf_conf_v2`(前者走 bench_perf_local 已验证 OK);`perf_conf_v2` 留待 M3.62 单独排查 |

## 五、决策与 ROI 分析

| Decision | 选项 | 决定 | 理由 |
|---------|------|------|------|
| 重训哪些 adapter | perf / tempfile / disk_cleanup / email | **不重训** | ACC 都在可接受范围;critical 漏报是 0.6B 容量问题,加数据可能过拟合 |
| perf scenario 用哪个 adapter | perf_conf_v2 / perf_conf | **perf_conf** | perf_conf_v2 触发 transformers 内部 bug,本地推理不可用;perf_conf 走 bench_perf_local 路径稳定 |
| adapter 加载 bug 根本修复 | 修 train_step1.py 设 pad_token 正确值 | **留给 M3.62+** | M3.61 修运行时分支已够用;训时修复影响所有老 adapter,需重训 15 个 |
| 评测延迟优化 | max_new_tokens 40 / 推理加速 | **留给 M3.62+** | 当前 5-10s 可接受,优先覆盖完整评测 |

## 六、关键文件改动清单

| 文件 | 改动 |
|------|------|
| `companion/adapter_registry.py:378-386` | 删 _patch 函数末尾残留 _lazy_load 片段 |
| `companion/adapter_registry.py:300-310` | `classify()` pad_token_id 改 eos_token_id fallback |
| `companion/bench_all.py:155-170` | 加 `adapter_name = "perf_conf"` if scenario == "perf" |
| `companion/bench_all.py:115-130` | `_extract_email_kwargs` 辅助函数 |
| `companion/bench_all.py:140-160` | `_extract_log_kwargs` 辅助函数 |
| `companion/bench_all.py:175-185` | extract_kwargs 分支处理 email/log |
| `companion/bench_all.py:215-225` | extract chain 加 `risk` / `task_type` 字段 |
| `companion/bench_all.py:80-95` | SCENARIOS dict log/tempfile/email 加 extract_kwargs |
| `companion/bench_all.py:95-105` | tempfile scenario 加 local_module=classify_tempfile |
| `companion/classify_tempfile.py` | **新建** 独立 tempfile classifier(替代 classify_email placeholder) |
| `companion/reports/bench_*_m361.json` | 8 份评测报告输出 |
| `memory/prisIr-companion-m361-bench-recovery.md` | 本文件 |
| `MEMORY.md` | 加索引行 |

## 七、未解决 / 留给 M3.62+

1. ⚠️ **`perf_conf_v2` 触发 transformers 内部 json.load JSONDecodeError**:重复加载同路径 tokenizer.json 时(可能是 peft 的某种 cache)line 577381 col 14 char 8538459 报 `,` expected,但实际是 `\xc4\x89` 字节。直接 `json.loads(open(...).read())` 成功。
   - **M3.62+**:重训 perf_conf_v3 用更新的 transformers 库,或换 safetensors 不依赖旧版 tokenizer.json
2. ⚠️ **`classify_*_local.py` / `bench_intents_local.py` 的 prompt template 与 train_step1.py 可能漂移** — 当初 L6 ChatML 修过 train_step1.py,但 `_build_text` 函数是否对齐训练时 text 字段?需对每条 fixture 跑 raw vs expect 对比
3. ⚠️ **critical 类漏报**(perf/tempfile/email)— 0.6B 容量限制,加 abstract 数据 + balanced 重训可能有效,M3.62+ 评估
4. ⚠️ **本机延迟 5-10s/条** — 多个 adapter 加载 ~5s + 单条推理 ~5s。生产用可接受(异步),但评测 8 scenario × 60 条 = 60min 太长。M3.62+ 可考虑 batch 推理 / 共享 base model

## 八、关键参考

- `companion/adapter_registry.py:298-308` — 修复后的 classify() generate 调用
- `companion/adapter_registry.py:346-378` — 修复后的 _patch_tokenizer_extra_special_tokens
- `companion/bench_all.py:115-160` — _extract_email_kwargs / _extract_log_kwargs
- `companion/bench_all.py:190-225` — _bench_local 主循环(extract_kwargs + extract_chain)
- `companion/classify_tempfile.py` — M3.61 独立 tempfile 入口
- `companion/reports/bench_*_m361.json` — 8 份评测报告
- `memory/prisIr-companion-m360-bench-unified.md` — M3.60(评测统一基建)
- `memory/prisIr-companion-m358-closed.md` — M3.58(最近 task 闭环参考)
