# log_triage — M3.69(2026-09-24)

把已有的 **log + log_conf** LoRA adapter 包成可独立运行的命令行程序。
扫 Windows Event Log(或 `.log` 文件)→ 5 类风险分级(safe/low/medium/high/critical)
→ 输出 critical/medium 待办清单(text / json / markdown)。

**Phase 1 单跑可用。Phase 2 才与 PrisirAI 整合。**

## 资产(已存在,不重写)

| 路径 | 说明 |
|------|------|
| `companion/classify_log.py` | `classify_log(adapter, source, level, content, ts)` 接口 |
| `companion/adapter_registry.py` | `get_adapter(name)`,已注册 `log` + `log_conf` |
| `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/` | base model |
| `D:/prisir-train-assets/trained/log/adapter/` | log adapter |
| `D:/prisir-train-assets/trained/log_conf/adapter/` | log_conf adapter |

## 安装

需要 `pywin32` 才支持 Windows Event Log(可选;`.log` 文件流无需):

```bash
pip install pywin32
```

`psutil` 已依赖 `companion/` 环境,`log_triage` 不直接 import 它,但同环境应有。

## 用法

```bash
# 默认扫最近 24h System + Application 日志
python main.py

# 扫具体 channel
python main.py --channel "System" --since "24h"

# 扫 7 天 + 输出 Markdown 报告
python main.py --since "7d" --output "reports/log_weekly_$(date +%Y%m%d).md" --format md

# 扫文件(任何 .log 文件,按文件 mtime 过滤)
python main.py --file "D:/logs/app.log" --spec log_conf

# 只看 critical + top 20
python main.py --min-severity critical --top 20

# 跳过 LoRA(快速事件数预览)
python main.py --no-classify

# JSON 输出(便于管道)
python main.py --file app.log --format json
```

## 参数

| 参数 | 默认 | 备注 |
|------|------|------|
| `--channel NAME` | `["System", "Application"]` | 可重复传,Windows Event Log channel |
| `--file PATH` | - | `.log` 文件(任意格式,优先 JSON 行,其次 `<ts> [LEVEL] <msg>`,再否则纯文本) |
| `--since` | `24h` | `Nh` / `Nd` / `Nw` |
| `--spec` | `log_conf` | `log`(无 confidence)或 `log_conf`(带 probability) |
| `--min-severity` | `safe` | `safe` / `low` / `medium` / `high` / `critical` |
| `--top N` | `0`(全部) | 最多列 N 条待办 |
| `--format` | `text` | `text` / `json` / `md` |
| `--output PATH` | stdout | 输出文件 |
| `--no-classify` | off | 跳过 LoRA,只统计 |
| `--max-events` | `2000` | 防卡(读 Event Log 单次上限) |

## 输出示例(text)

```
[log_triage] spec=log_conf since=2026-09-23 17:47:00
[log_triage] 总事件: 2847  parse_fail: 12

5 类分布:
  critical     12 ( 0.4%)  🔴🔴🔴 已上报 incident
  high         49 ( 1.7%)  🔴 已触发 watchdog
  medium      198 ( 7.0%)  ⚠️ 推送 perf_guard 告警
  low         432 (15.2%)  入库
  safe       2156 (75.7%)  丢弃

待办(≥ safe,按时间倒序,最多 50 条):

  [14:02] critical 🔴🔴🔴 test-app: KERNEL PANIC: out of memory... → alert
  [13:17] high     🔴       test-app: Database connection failed... → alert
  ...
```

## 输出示例(Markdown)

```markdown
# Log Triage Report — 2026-09-24

**扫描时间窗**: 2026-09-23 17:47:00 → 2026-09-24 17:47:00
**总事件**: 2847 条  **parse_fail**: 12 条
**spec**: log_conf

## 5 类分布

| 等级 | 事件数 | 占比 | 动作 |
|------|--------|------|------|
| critical | 12 | 0.4% | 🔴🔴🔴 上报 incident |
...
```

## 失败模式

| 现象 | 行为 |
|------|------|
| 没装 `pywin32` | `--channel` 模式报 friendly error,提示用 `--file` |
| 权限不足读 channel | 该 channel 跳过 + stderr 警告,继续跑其他 channel |
| `--file` 不存在 | exit code 2 + stderr 提示 |
| LoRA 推理抛异常 | 单条事件标 `parse_fail=True`,不影响其他事件 |
| `--since` 格式不对 | exit code 2 + 提示期望格式 |

## 设计要点

- **零侵入**:`main.py` + `src/log_triage.py` 完全独立,不修改 `companion/` 任何代码
- **离线可用**:无网络调用,只用本地 LoRA adapter
- **时间过滤**:Windows Event Log 用 `ev.TimeGenerated` 精确过滤;.log 文件按文件 mtime 兜底
- **parse_fail 容错**:adapter 解析失败的事件不计入 5 类分布(单独统计 `parse_fail` 字段)

## 项目结构

```
log_triage/
├── README.md
├── main.py                # CLI 入口(argparse + emit)
├── src/
│   ├── __init__.py
│   └── log_triage.py      # 核心:read + classify + format
└── tests/
    └── test_e2e.py        # 用 50 行 fake.log 跑通 5 个场景
```

## 测试

```bash
python tests/test_e2e.py
```

会跑通:
1. `--help` 输出完整
2. text 格式跑通
3. JSON 输出 + 文件写出 + 字段验证
4. Markdown 输出 + 文件写出
5. `--min-severity=high` 过滤生效

(约 3-5 分钟:log_conf LoRA 推理 50 条事件 + 5 次子进程)

## 已知限制

- **log_conf parse_fail ~50%**:M3.49 L6 ChatML 重训后仍有 50% 解析失败(M3.61 bench 实测)
  这是 0.6B 模型的能力上限。log_triage 把这些事件单独计入 `parse_fail` 字段,不影响其他事件分类。
- **Event Log 按时间倒序读**:遇到第一条早于 `--since` 的事件就停(因 `EVENTLOG_BACKWARDS_READ`),高效但要求 channel 在 `--since` 后确实有事件。
- **mtime 兜底过滤**:`.log` 文件按文件修改时间过滤,不按行内时间戳。适合 append-only 日志;滚动归档的 log 用 `--since 30d` 之类会拉太多行,自行截断。

## 不要做(Phase 2 再考虑)

- 不要写 unit tests(只写 e2e)
- 不要 Web UI
- 不要调外部 API
- 不要改 `companion/` 任何代码
- 不要重训 LoRA
- 不要 PrisirAI 整合(Phase 2)