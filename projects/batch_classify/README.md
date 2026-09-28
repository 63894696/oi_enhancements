# batch_classify

通用 wrapper:扫目录 / 文件 / 文本 → 调任意 spec → 出 5 类风险分布报告。

把已经训好的 16 个 LoRA adapter 包成一个**独立可跑**的 CLI 工具,无网络依赖,纯本地推理。

## 为什么

`companion/adapter_registry.py` 里有 16 个已经训好的 LoRA adapter(safety / tempfile / disk_cleanup / email / log / intents / perf_conf / task 等)。每个 spec 都有自己专门的 `classify_*.py` 入口,但它们的形态不一致,不便于组合 / 集成。

`batch_classify` 提供一个**通用入口**:
- 同一段逻辑跑任意 spec
- 多 spec 联合时取最高风险(顶配取最高风险,argmax over critical > high > medium > low > safe)
- 三种输入源 + 三种输出格式

Phase 1:这个 CLI 单跑可用。Phase 2:7 个 sibling 项目(`log_triage` / `perf_alert` / `safe_exec` / `cleanup_suggest` / `email_triage` / `intent_router` / `commit_check`)会基于此 wrapper 复用。

## 安装

零依赖安装。本仓库已自带:
- `companion/adapter_registry.py`(16 个 spec 注册表)
- `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/`(base 模型)
- `D:/prisir-train-assets/trained/<spec>/adapter/`(16 个 LoRA 适配器)

需要 Python 3.10+,`torch` + `transformers` + `peft` 已装好(同 companion/)。

## 使用

### 1. 扫目录所有文件(多 spec 联合)

```bash
python main.py --root "D:/Users/Admin/Temp" --specs disk_cleanup,tempfile
```

### 3. 扫文本(单条)

```bash
python main.py --text "rm -rf C:\Windows\System32" --spec safety
```

### 4. 输出 CSV 报告

```bash
python main.py --root "D:/projects" --spec disk_cleanup --output "report.csv" --format csv
```

### 5. 输出 Markdown 报告(带 5 类分布 + top critical 文件)

```bash
python main.py --root "D:/logs" --spec log --since "24h" --output "report.md" --format md
```

### 6. dry-run(只跑前 5 条)

```bash
python main.py --root "D:/Users/Admin/Temp" --specs disk_cleanup,tempfile --dry-run
```

### 7. 看可用 spec

```bash
python main.py --list
```

## 全部参数

| 参数 | 说明 |
|------|------|
| `--root` | 扫描目录(递归),与 `--file` / `--text` 互斥 |
| `--file` | 扫描单个文件,与 `--root` / `--text` 互斥 |
| `--text` | 扫描单条文本,与 `--root` / `--file` 互斥 |
| `--spec NAME` | 单个 spec 名 |
| `--specs NAME1,NAME2` | 多个 spec(逗号分隔),联合时取最高风险 |
| `--since 24h` | 时间窗,可选 `s/m/h/d`,只取 mtime 在窗口内的文件(仅 `--root`) |
| `--ext .log .tmp` | 扩展名白名单(仅 `--root`) |
| `--limit N` | 最多扫 N 个文件 |
| `--format csv` | 输出格式:`csv` / `json` / `md`(`markdown` 同 `md`) |
| `--output / -o FILE` | 输出文件路径,默认 stdout |
| `--dry-run` | 只跑前 5 条 |
| `--list` | 列出所有可用 spec 后退出 |

`--help` 有完整 epilog + 示例。

## 可用 spec(2026-09-24)

| spec 名 | schema | 用途 |
|---------|--------|------|
| `safety` | safety | 风险等级 + jailbreak(对自然语言消息) |
| `tempfile` | tempfile | 临时文件 5 类 + delete/review/keep |
| `disk_cleanup` | disk_cleanup | Win 系统子目录清理 |
| `email` | email | 邮件 5 类 + delete/archive/reply/keep |
| `log` | log | 日志 5 类 + alert/review/keep/drop |
| `intents` | intents | 聊天意图 5 类 |
| `perf_conf` / `perf_conf_v2` / `perf_conf_v3` | perf | 本机性能快照(boot_s/bugcheck/NIC 等) |
| `task` | task | 任务分类 6 类 |

带 `_conf` 后缀的版(Jev 风格 calibrated prob):`safety_conf` / `tempfile_conf` / `disk_cleanup_conf` / `email_conf` / `log_conf` / `intents_conf`。

**推荐用 `_conf` 系列**:有 confidence 输出 → 5 类分布非 one-hot,更平滑。

`python main.py --list` 是当前真理源。

## 输出示例

### CSV (`--format csv`)

```csv
item_id,item_type,spec,safe,low,medium,high,critical,top_risk,action,risk_conf,jailbreak,parse_fail,size_bytes,latency_ms
D:/a/temp.log,path,disk_cleanup,0.85,0.0375,0.0375,0.0375,0.0375,safe,Keep,0.85,no,False,1024,2418
D:/b/secrets.pem,path,disk_cleanup,0.05,0.05,0.1,0.3,0.5,critical,Alert,0.5,no,False,2048,2612
```

### Markdown (`--format md`)

```markdown
# Batch Classify Report

**扫描**: `D:/Users/Admin/Temp`  (5234 文件)
**spec**: disk_cleanup, tempfile

## 5 类分布

| 等级 | 文件数 | 占比 | 体积 |
|------|--------|------|------|
| safe | 3124 | 60.0% | 0.3 GB |
| low | 1234 | 24.0% | 0.5 GB |
| medium | 654 | 12.5% | 0.3 GB |
| high | 156 | 3.0% | 0.1 GB |
| critical | 66 | 1.0% | 0.02 GB |

## critical / high 文件 (top 20 / 共 222 条)

| item | risk | safe | low | medium | high | critical |
|------|------|------|-----|--------|------|----------|
| `D:/b/secrets.pem` | critical | 0.05 | 0.05 | 0.10 | 0.30 | 0.50 |
...
```

## 已知限制

- **每个 spec 独立加载**:同时跑多 spec 时,第一个 adapter 加载完后第二个才加载(顺序)。可优化但 Phase 1 不做。
- **parse_fail 不等于错**:模型偶尔复读 / 输出顺序错 → `_parse` 取第一段后仍可能 None。top_risk 会标 `parse_fail`。
- **5 类概率是合成的**:从 `(risk, risk_conf)` 推导 → 把 mass 摊到 top_risk,其余 4 类均分 `(1 - conf) / 4`。不是真正的 softmax,但够排序用。
- **LoRA 加载时间**:首次跑一个 spec 需 ~5-10s 加载 base + adapter,后续 1-3s / 条。
- **不修改 `companion/` 下任何代码** — 零侵入集成,通过 `sys.path.insert` 复用 `adapter_registry`。
- **路径处理**:Windows 路径含反斜杠 `\` 时,部分 adapter (disk_cleanup) 训练时见过 `\` 分隔,这是有意为之(M3.47)。

## 设计

- **核心 1 文件** `src/batch_classify.py`:解析器 + 概率分布 + 格式化 + 联合多 spec
- **入口 1 文件** `main.py`:argparse + 调 core
- **0 个 unit test**:只写 e2e(`tests/test_e2e.py`),符合任务约束
- **零侵入**:`sys.path.insert` 进 `companion/`,复用 `adapter_registry.get_adapter(name)`

### 联合多 spec 算法

```python
top_risk = max(results, key=lambda r: order[r.risk])  # critical > high > ...
distribution = avg([r.distribution for r in results])
```

### 5 类分布合成(没有 softmax)

```python
if risk_conf:
    dist[risk] = risk_conf
    dist[others] = (1 - risk_conf) / 4
else:
    dist[risk] = 1.0  # one-hot
```

## 文件树

```
batch_classify/
├── README.md                 # 本文件
├── main.py                   # CLI 入口
├── src/
│   ├── __init__.py
│   └── batch_classify.py     # 核心:扫描 + 解析 + 5 类分布 + 联合
└── tests/
    └── test_e2e.py           # e2e 测试(7 个用例)
```

## 跑测试

```bash
python tests/test_e2e.py
```

会跑 7 个用例:`--help` / `--list` / dry-run / CSV 输出 / Markdown 输出 / 多 spec 联合 / `--text` safety,需要 ~3-10 分钟(每个 spec 首次加载 ~10s)。