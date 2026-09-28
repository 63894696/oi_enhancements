---
name: prisIr-companion-m360-bench-unified
description: M3.60 评测统一 + Jev 真测 / 结构化独立 test set 闭环 2026-09-23
metadata:
  type: project
---

# M3.60 评测统一 + Jev 真测 / 结构化独立 test set 闭环(2026-09-23)

## 一、为什么做

用户原问:"是否还有可对模型训练方面的场景?是否可以用训练的模型与jev做相同的测试题集?"

评测体系问题:
- **Jev 真测对比**:只有 intents(M3.50)+ 部分 task(走 fastlane regex)有
- **结构化 7 类**(perf/disk_cleanup/tempfile/email/log):**无独立 test set**,用 `data_*.jsonl` 80/20 切 — 训练集已见过 80%,holdout 可信度低
- bench_intents_local.py + bench_intents.py 的 50 条 TEST_CASES **完全硬编码两遍**(同源,极易漂移)

## 二、设计方案

### 1. 新建 `companion/test_fixtures/` 共享题集目录
- `intents_jev_compat.json` (50) / `task_jev_compat.json` (59) / `safety_jev_compat.json` (30)
- `perf_independent.json` (60) / `disk_cleanup_independent.json` (60) / `tempfile_independent.json` (60) / `email_independent.json` (60) / `log_independent.json` (60)

### 2. 结构化场景独立 test set(60 条/类)
- 30 条 holdout_split(seed=42,从训练集抽,**训练时排除** — 见 manifest)
- 30 条手造(从 Windows 真实路径 / 邮件 / 日志 / perf 快照合成)

### 3. 改 bench_*.py 全部走 fixture
- `bench_intents.py` / `bench_intents_local.py` — 共享 `intents_jev_compat.json`(消除两遍硬编码漂移)
- `bench_task_local.py` — 走 `task_jev_compat.json`(59 条 + LONG_SAMPLE)
- `bench_disk_cleanup.py` / `bench_email.py` / `bench_tempfile.py` — 加 `--test-set` 选项

### 4. 新建 `companion/bench_all.py` 综合评测入口
- 一键跑全 8 scenario + Jev 真测对比
- `--scenarios` / `--no-jev` / `--output` / `--scenarios-only` 选项
- SCENARIOS 配置:fixture 名 / local_module / local_fn / call_pattern / jev_compatible

## 三、关键 bug 与修复

| Bug | 修复 |
|------|--------|
| holdout_split 抽 30 条与训练集 100% 重叠(原版本只抽不排除) | 加 `_holdout_train_exclude.json` manifest,标记训练时应排除的 30 条;验证 `overlap_with_remaining_train=0` |
| bench_intents_local.py / bench_intents.py 50 条硬编码两遍 | 两份均改 `from test_fixtures import load_fixture("intents_jev_compat")`,**drift 风险消除** |
| bench_all.py 调 `classify_intents(adapter, text)` 但接口是 `classify_intents(text)` | 加 `call_pattern: "text_only" \| "adapter_first"` 字段,各 scenario 独立标注 |
| bench_all.py 抽 `actual` 时只查 `risk_label`,intents 返回 dict 含 `intent` key | 加 `intent` / `label` / `choice` fallback chain |

## 四、验证结果

| 项 | 结果 |
|------|--------|
| `python test_fixtures/gen_independent_fixtures.py` | 5 个独立 test set 各 60 条全生成 ✓ |
| `overlap_with_remaining_train` | 5 个场景全部 = 0(holdout 真独立)✓ |
| `python -c "from bench_intents_local import TEST_CASES"` | 50 条全加载,id 是 `intents.chat.001` 等 fixture id ✓ |
| `python -c "from bench_task_local import TEST_CASES"` | 59 条,6 类齐全 ✓ |
| `python bench_all.py --scenarios-only` | 8 scenario 配置合理 ✓ |
| `python bench_intents_local.py --output ...` | 50 条全跑 ✓ |
| `python bench_all.py --scenarios intents --no-jev` | 走完整管线 ✓ |
| ⚠️ 准确率 0% / parse_fail 1.0 | 既有 transformers `extra_special_tokens` API 不兼容问题(与 M3.60 无关),adapter 路径全部异常 |

## 五、关键文件改动清单

| 文件 | 改动 |
|------|--------|
| `companion/test_fixtures/__init__.py` | FIXTURES 索引 + load_fixture API |
| `companion/test_fixtures/README.md` | 用途 + 题集变更规范 |
| `companion/test_fixtures/schema.md` | 各 JSON 字段规范 |
| `companion/test_fixtures/intents_jev_compat.json` | 50 条 jev baseline |
| `companion/test_fixtures/task_jev_compat.json` | 59 条(含 LONG_SAMPLE) |
| `companion/test_fixtures/safety_jev_compat.json` | 30 条 5 类 × 6 条 |
| `companion/test_fixtures/{perf,disk_cleanup,tempfile,email,log}_independent.json` | 各 60 条(30 holdout + 30 synthetic) |
| `companion/test_fixtures/{scenario}_holdout_train_exclude.json` | 训练时排除 manifest |
| `companion/test_fixtures/_holdout_manifest.json` | 汇总 |
| `companion/test_fixtures/gen_independent_fixtures.py` | 一次性生成 5 个独立 test set |
| `companion/bench_intents.py` | 删硬编码 50 条 → 读 fixture |
| `companion/bench_intents_local.py` | 删硬编码 50 条 → 读 fixture |
| `companion/bench_task_local.py` | 删硬编码 70 条 → 读 fixture,保留 `_LEGACY_TEST_CASES()` fallback |
| `companion/bench_disk_cleanup.py` / `bench_email.py` / `bench_tempfile.py` | 加 `--test-set` 选项 |
| `companion/bench_all.py` | **新建** ~280 行综合评测入口 |
| `memory/prisIr-companion-m360-bench-unified.md` | 本文件 |
| `MEMORY.md` | 加索引行 |

## 六、决策点

| 决策 | 选项 | 决定 |
|------|------|------|
| 结构化场景题集来源 | 抽训练集 / 手造 / 混合 | 30 抽 + 30 手造(混合) |
| Jev 真测范围 | 全场景 / 仅适用场景 | 仅 intents/task/safety(结构化场景 Jev 不适用,基线无意义) |
| fixture 字段 schema | 各场景独立 / 统一 | 统一通用字段 + 场景特定扩展字段(`sample` / `_action` / `risk_label`) |
| fixture 漂移防御 | 加版本号 / 报告留 fixture_sha | 报告留 fixture_path + fixture_sha,无版本号(变更规范靠 README) |
| bench_all adapter 加载 | 默认全跑 / 默认 dry-run | 默认全跑但 dry-run 模式 `--scenarios-only` 可用 |

## 七、未解决 / 留给 M3.61+

- ⚠️ transformers `extra_special_tokens` API 不兼容导致 adapter 加载 100% 失败(M3.60 范围外,需重训/重制 tokenizer config)
- ⚠️ tempfile scenario 暂用 `classify_email` placeholder,待 M3.61 写独立 `classify_tempfile.py`
- ⚠️ Jev 真测 task 场景尚未做(fastlane regex baseline 算一种 Jev-like,但不是真 LLM),需 M3.61+ 接入

## 八、关键参考

- `companion/bench_intents.py:36-150` — fixture 优先 + _LEGACY fallback
- `companion/bench_task_local.py:79-83` — fixture + _LEGACY pattern
- `companion/bench_disk_cleanup.py:206-228` — `--test-set` 选项
- `companion/bench_all.py:36-89` — SCENARIOS 配置
- `companion/bench_all.py:99-152` — _bench_local 含 call_pattern 处理
- `companion/test_fixtures/gen_independent_fixtures.py:53-110` — holdout_split(带 manifest 排除)
- `companion/test_fixtures/README.md` — 题集变更规范
- `memory/prisIr-companion-m359-admin-state.md` — M3.59(上一个里程碑)
- `memory/prisIr-companion-m358-closed.md` — M3.58(本方案的最近参考)
