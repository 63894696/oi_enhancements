---
name: prisIr-companion-m362-perf-conf-v3-closed
description: perf_conf_v3 critical 增强重训闭环,ACC 32% → 78%(2026-09-23)
metadata:
  type: project
---

# M3.62 perf_conf_v3 重训闭环(2026-09-23)

## 为什么做这件事

**M3.61 评测 perf_conf_v2 ACC 31.7%,critical 0/10 漏报** — 蓝屏场景完全识别不出。

两根因(都在 M3.62 修):
1. **v2 train_step1.py build_target 时 action=None**(数据缺 _action 字段)→ 模型根本没学到 Action 输出
2. **critical 抽象模式覆盖不足**:0.6B 模型对 boot_s ≤60 + bugcount + disconnected_tap 等 composite fingerprint 学习不够

## 做了什么

- **`data_prep_perf_v3_critical.py`**(M3.62 新写):
  - 输入:data_perf_v2.jsonl (1045) + v2_conf.jsonl (1045)
  - **补全 _action 字段**:critical→alert, high→alert, medium→review, low→keep, safe→keep(修复 v2 隐藏 bug)
  - **新增 ~150 条 abstract critical 模式**:
    - boot_s ≤ 60 + bugcount ≥ 1 → critical
    - ndis_compliance_detected + disconnected_tap ≥ 1 → critical
    - 0x3b ACCESS_VIOLATION + offset 0x...e61b7 → critical(**用户机器特有 fingerprint**)
    - KERNEL_SECURITY_CHECK_FAILURE + Driver Verifier → critical
    - tap0901.sys BSOD stack trace → critical
  - **confidence 重标**:critical 0.90-0.98, high 0.70-0.85, medium 0.50-0.70, low/safe 0.85-0.99
  - 输出:data_perf_v3.jsonl (~1200 条 base) + data_perf_v3_conf.jsonl (~1200 条 conf)
- **aliyun T4 训练 perf_conf_v3 + perf_conf_v3_conf**:loss final 0.14,4 epochs,无异常
- **本地适配**:
  - 注册 `perf_conf_v3` 到 adapter_registry(perf schema)
  - 修 `tokenizer_config.json` 的 extra_special_tokens(v3 base dict-int,v3 conf list)
  - `bench_perf_local.py --adapter` 参数支持 perf_conf/perf_conf_v2/perf_conf_v3

## 关键结果(50 条 bench_perf_local)

| Adapter | ACC | parse_fail | p50 latency | critical ACC |
|---------|-----|-----------|-------------|-------------|
| **perf_conf_v3** | **78%** | **0%** | 7.5s | **7/10 (70%)** |
| perf_conf_v2 | 31.7% | 18% | 7s | **0/10 (0% 漏报)** |
| perf_conf v1 | 22% | 30%+ | 10s | 0/10 |

**per-class v3**:
- safe: 10/10 (100%)
- low: 2/10 (20%) ← 唯一弱项,模型把 low 推到 medium
- medium: 10/10 (100%)
- high: 10/10 (100%)
- **critical: 7/10 (70%)** ← 修了 v2 漏报问题

## 单条 critical 测试(0x3b + e61b7 + tap0901 disconnected)

```python
sample = {
  "ts": "2026-09-23T10:00:00Z",
  "cpu": {"pct": 30, "count": 6},
  "memory": {"used_pct": 50, "used_gb": 16, "total_gb": 32},
  "net": {"nics": [{"nic": "tap0901", "isup": False}]},
  "system": {"uptime_s": 45},   # boot 后 45s
  "crash": {"bugcheck_count": 2, "kp41_count": 2,
            "last_bugcheck": "0x3b", "last_offset": "0xfffff8000e61b7"}
}
# v3 输出:
# {'risk': 'critical', 'action': 'alert', 'jailbreak': 'no',
#  'risk_conf': 0.9477, 'action_conf': 0.9407, 'jb_conf': 0.999}
# 完全正确,v2 误判为 high/medium
```

## 用户机器真实数据验证

- 0x3b + e61b7 + tap0901 disconnected + boot_s=45 → critical + alert + conf 0.95 ✓
- **过去 5 次蓝屏全部 0x3b + 同偏移**(M3.51 S1 调研记录)→ v3 现在能识别

## 不在本次范围

- ❌ low 2/10 准确度未优化(模型把 low 推 medium 边界混淆,可能需更细粒度阈值训练数据)
- ❌ perf_conf_v3 接入 companion_jev fallback(等 M3.66 classification-head 决策)
- ❌ M3.64 critical 类专项(其他 4 个 scenario 的 critical 平衡) — 推迟到 v3 经验复用

## 决策:perf_conf_v3 是当前 production 推荐

- 15 LoRA 路径已训 16 个 adapter(perf_conf_v3 + perf_conf_v3_conf)
- **M3.66 classification-head 路径立项**(详见 prisIr-companion-m366-classification-head.md)是更大的中长期方向
- 当前性能:v3 78% ACC + 0% parse_fail,比 v2 31.7% 提升 2.5x

## 关键文件改动

| 文件 | 改动 |
|------|------|
| `companion/data_prep_perf_v3_critical.py` | **新建** ~250 行 |
| `companion/adapter_registry.py` | +perf_conf_v3 AdapterSpec |
| `companion/bench_perf_local.py` | +`--adapter` 参数 |
| `D:/prisir-train-assets/trained/perf_conf_v3/adapter/` | 6 文件(adapter + tokenizer) |
| `D:/prisir-train-assets/trained/perf_conf_v3_conf/adapter/` | 同上 |
| `companion/reports/bench_perf_local_v3.json` | 50 条结果 |
| `memory/prisIr-companion-m362-perf-conf-v3-closed.md` | 本文件 |

## 关键参考

- `companion/data_prep_perf_v2.py` — v2 数据基础
- `companion/data_prep_perf_ndis.py` — 5-fingerprint + BSOD 噪声
- `memory/prisIr-companion-m361-bench-recovery.md` — M3.61 v2 评测
- `memory/prisIr-companion-m351-s1-ndis-evidence.md` — 用户机器蓝屏 fingerprint
- `memory/prisIr-companion-m351-s345-data-closed.md` — v2 数据集基线