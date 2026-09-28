# test_fixtures Schema(2026-09-23)

每个 fixture 是 JSON list,每条 case 字段:

## 通用字段(所有 fixture 都有)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | str | ✅ | 唯一 ID,格式 `<scenario>.<class>.<seq>`,如 `intents.chat.001` |
| `scenario` | str | ✅ | 场景名:intents/task/safety/perf/disk_cleanup/tempfile/email/log |
| `expect` | str | ✅ | 期望分类标签 |
| `text` | str | ✅ | 输入文本(adapter 推理直接用) |
| `tags` | list[str] | ❌ | 标签数组,辅助调试(选填) |
| `source` | str | ✅ | 数据来源:`synthetic` 手造 / `real` 真实 / `jev_baseline` 来自 Jev 评测 / `holdout_split` 从训练集抽 holdout |
| `notes` | str | ❌ | 备注(原 bench 来源、改动记录) |

## 场景特定字段

### intents / task / safety(`expect` 是分类标签)

```json
{"id": "intents.chat.001", "scenario": "intents",
 "expect": "chat", "text": "今天天气真好呀",
 "tags": ["greeting", "weather"], "source": "jev_baseline",
 "notes": "原始在 bench_intents.py:48"}
```

`expect` 范围:
- intents: chat / code / search / tool_call / roleplay
- task: code_call / code_qa / creative / long / fast / general
- safety: safe / low / medium / high / critical(分级)

### perf(`expect` 是 risk;含 sample 字段)

```json
{"id": "perf.critical.001", "scenario": "perf",
 "expect": "critical",
 "text": "...", // 实际可能空,perf 用 sample 字段
 "sample": {
   "ts": "2026-09-23T12:00:00Z",
   "cpu": {"pct": 95, "count": 6, "freq_mhz": 3696},
   "memory": {"used_pct": 92, "used_gb": 29, "total_gb": 32, "available_gb": 3},
   "net": {"n_total": 9, "n_up": 5, "nics": [
     {"nic": "ProtonVPN TUN", "isup": False},
     {"nic": "ProtonVPN TAP", "isup": False}
   ]},
   "system": {"uptime_s": 800, "user": "Administrator"},
   "crash": {"bugcheck_count": 1, "kp41_count": 1}
 },
 "tags": ["ndis_bsod", "TAP_disconnected"], "source": "real",
 "notes": "M3.51 S5 ndis 合成 fingerprint"}
```

`expect` 范围: safe / low / medium / high / critical

### disk_cleanup / tempfile(`expect` 是 risk_label;含 `_action` / 路径字段)

```json
{"id": "dc.h.001", "scenario": "disk_cleanup",
 "expect": "high", "risk_label": "high",
 "_action": "delete",
 "text": "文件路径: Windows\\SoftwareDistribution\\Download\\edb.log\n扩展名: .log\n大小: 24MB\n年龄: 30 天\n问: 这个 Windows 系统文件是否可以安全清理?",
 "tags": ["WinSxS", "old", "review-typical"],
 "source": "holdout_split",
 "notes": "从 data_disk_cleanup.jsonl 抽出,seed=42"}
```

`expect` 范围: safe / low / medium / high / critical
`_action` 范围: delete / review / keep

### email(`expect` 是 risk_label)

```json
{"id": "email.spam.001", "scenario": "email",
 "expect": "medium", "risk_label": "medium",
 "_action": "delete",
 "text": "发件人: newsletter@producthunt.com\n主题: 您可能感兴趣的...\n...",
 "tags": ["spam", "marketing"], "source": "holdout_split",
 "notes": "从 data_email.jsonl 抽出"}
```

### log(`expect` 是 risk_label;含日志来源/级别/内容)

```json
{"id": "log.warn.001", "scenario": "log",
 "expect": "medium", "risk_label": "medium",
 "_action": "review",
 "text": "日志来源: python\n时间戳: 2026/09/23 15:11:10\n级别: warning\n内容: warnings.warn(...)\n问: 这条日志应该如何分类与处理?",
 "tags": ["deprecation_warning"], "source": "holdout_split",
 "notes": "从 data_log.jsonl 抽出"}
```

## version

每次 fixture 改都更新:

```json
[
  {
    "_meta": {
      "version": "1.0",
      "last_updated": "2026-09-23",
      "n_cases": 50,
      "notes": "从 bench_intents.py 抽出 + id 重命名"
    }
  },
  {"id": "...", ...}
]
```

(可选,默认不放)