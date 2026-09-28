# perf_alert (M3.69)

本地性能快照实时告警 CLI — 把已有本地 AI 模型做成可独立运行的前台守护程序。

**零侵入**(`companion/` 一行不动)+ **离线可用**(纯本地 LoRA)+ **Phase 1 单跑**(Phase 2 才与 PrisirAI 整合)。

---

## 它做什么

每 N 秒采一次本机性能(CPU / 内存 / NIC / TAP / 进程 top / BugCheck)→
用本地 `perf_conf_v3` adapter 评 5 类风险 →
`medium/high/critical` 时输出告警 / `--watchdog` 模拟触发 watchdog kill_mode / `--ws` 推 WebSocket。

## 用法

```bash
# 前台跑,60s 采样
python main.py --interval 60

# 写文件 + WebSocket 推送
python main.py --interval 30 --output logs/perf_alert.jsonl --ws ws://localhost:8765

# 联动 watchdog kill_mode(模拟)
python main.py --watchdog

# dry-run 只打 5 次,不出告警
python main.py --dry-run --iterations 5

# 一次跑就退出(测试)
python main.py --once
```

## 输出示例

```
[perf_alert] interval=60s  spec=perf_conf_v3  watchdog=off

[10:23:00] safe      CPU 12% mem 38% NIC up
[10:23:10] low       CPU 35% mem 40% NIC up
[10:23:20] medium    CPU 78% mem 72% NIC up  ⚠️
[10:23:30] high      CPU 95% mem 88% TAP down(3) 🔴
 → 触发 high alert
 → 建议: 关注 top 进程 / 内存释放
[10:23:40] critical  CPU 99% mem 91% TAP down(3) 🚨
 → 触发 critical alert
 → TAP disconnected: tap0901, tapprotonvpn, vktap
 → 建议: 同步 process_numa 检查 top CPU 进程亲和性
```

## 架构

```
main.py (argparse + dispatch)
  └─→ src/perf_alert.py
        ├─ perf_collector.collect_one()      ← 从 companion/ 复用
        ├─ classify_perf.classify_perf()     ← 从 companion/ 复用
        ├─ adapter_registry.get_adapter()    ← 从 companion/ 复用
        └─ format_log_line / alert_actions   ← 自实现(轻量)
```

**复用资产**(不重写):
- `C:/Users/Administrator/oi_enhancements/companion/perf_collector.py`
- `C:/Users/Administrator/oi_enhancements/companion/classify_perf.py`
- `C:/Users/Administrator/oi_enhancements/companion/adapter_registry.py`

**不引入 `perf_guard.py`** — 它有 admin 联动 / 黑名单 / Companion WS 推送等副作用;
Phase 1 只需要 "采 + 评 + 简单告警" 三件事,自己实现约 80 行更可控。

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--interval` | 60 | 采样间隔秒 |
| `--spec` | `perf_conf_v3` | adapter 名(`perf_conf` / `perf_conf_v2` / `perf_conf_v3`) |
| `--output` | — | JSONL 输出文件(append) |
| `--ws` | — | WebSocket URL,无 server 时优雅跳过 |
| `--watchdog` | off | critical 时模拟 `[WATCHDOG] kill_mode=kill` |
| `--dry-run` | off | 不出告警动作 |
| `--iterations` | 0 | 最多跑 N 次(0 = 不限) |
| `--once` | off | 采一次 + 评 + 退出(测试用) |

## 测试

```bash
python tests/test_e2e.py
```

3 case:`--help` / `--once` 真采 / `--dry-run --iterations 3`。

## 已知限制 (Phase 1)

- `torch` CPU only → 单次推理 ~2.5s,采样间隔建议 ≥ 30s
- WebSocket 无 server 时静默 skip(`open_timeout=1.5s`)
- watchdog 仅 print 模拟,不真改 PrisirAI 配置(Phase 2 接)
- `--once` 模式不写日志到 `companion/data/`,仅写 `--output` 指定文件

## 不做的事

- 不写 unit tests(只 e2e)
- 不写 Web UI
- 不调外部 API
- 不改 `companion/` 任何代码
- 不重训 LoRA
- 不做 PrisirAI 整合(Phase 2)
- 不真触发 watchdog(只 print 模拟)
