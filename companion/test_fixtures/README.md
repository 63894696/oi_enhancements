# companion/test_fixtures/

M3.60(2026-09-23)引入的**共享评测题集**目录。目的:

- **消除 bench_intents / bench_intents_local 50 条硬编码两遍漂移风险**
- **Jev 真测题集 1:1 对齐**:`intents_jev_compat.json` / `task_jev_compat.json` / `safety_jev_compat.json` 既是 Jev 跑的题,也是本地 adapter 跑的题,**保证公平对比**
- **结构化场景独立 test set**:`perf_independent.json` / `disk_cleanup_independent.json` / `tempfile_independent.json` / `email_independent.json` / `log_independent.json` 与训练集不重叠(30 抽 + 30-50 手造)

## 目录

| 文件 | 条数 | 用途 |
|------|------|------|
| `intents_jev_compat.json` | 50 | Jev vs 本地 intents adapter 真测 |
| `task_jev_compat.json` | 59 | Jev(fastlane regex)vs 本地 task adapter 真测 |
| `safety_jev_compat.json` | 30 | safety vs Jev raw safety 类能力真测 |
| `perf_independent.json` | 80 | 本地 perf adapter 评测(独立 holdout)|
| `disk_cleanup_independent.json` | 60 | 本地 disk_cleanup adapter 评测 |
| `tempfile_independent.json` | 60 | 本地 tempfile adapter 评测 |
| `email_independent.json` | 60 | 本地 email adapter 评测 |
| `log_independent.json` | 60 | 本地 log adapter 评测 |

## 字段约定

详见 [schema.md](schema.md)。所有 fixture 统一字段:

```json
{
  "id": "intents.chat.001",
  "scenario": "intents",
  "expect": "chat",
  "text": "...",
  "tags": ["..."],
  "source": "synthetic" | "real" | "jev_baseline" | "holdout_split",
  "notes": "..."
}
```

## 使用

```python
from test_fixtures import load_fixture

cases = load_fixture("intents_jev_compat.json")
# 或:
cases = load_fixture("intents_jev_compat")  # 自动加 .json
```

CLI 探查:

```python
python -c "from test_fixtures import list_fixtures, load_fixture; print(list_fixtures()); print(f'intents: {len(load_fixture(\"intents_jev_compat\"))}')"
```

## 题集变更规范

1. **不要删除已有条目** — 这会改变历史 bench 报告的含义
2. **加新条目** — 末尾加;`id` 用 `<scenario>.<class>.<seq>` 格式
3. **改 expect** — 改前先确认没有 bench 报告依赖这个 expect;若有,在 notes 里说明
4. **改 text** — 同上,且改完要重跑所有引用此 fixture 的 bench
5. **fixture_path + fixture_sha 在 bench_all.py 报告里留痕** — 跑完报告有完整可复现性

## 与训练集重叠防御

对结构化场景的 `holdout_split` 条目(从 data_*.jsonl 抽的 30 条),自动验证:

```python
import json
train = [json.loads(l) for l in open("data_disk_cleanup.jsonl", encoding="utf-8")]
test = load_fixture("disk_cleanup_independent.json")
overlap = sum(1 for c in test if c.get("source") == "holdout_split" and c["text"] in {t["text"] for t in train})
assert overlap == 0, f"holdout 与训练集重叠 {overlap} 条"
```

## 历史

- M3.60 引入(2026-09-23)
- 改前:bench_intents + bench_intents_local 50 条硬编码两份 + 5 个结构化场景用 jsonl 80/20
- 改后:8 个 fixture 文件 + 1 个统一 load_fixture API + 1 个 bench_all.py 综合入口