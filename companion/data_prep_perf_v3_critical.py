"""data_prep_perf_v3_critical.py — M3.62 perf_conf_v3 critical 增强数据生成(2026-09-23)

目的:
  - M3.61 评测 perf_conf_v2 ACC 31.7%(critical 0/10 漏报),根因是 0.6B 模型对 critical
    抽象模式学习不够 + v2.jsonl 数据缺 _action 字段,模型根本没学到 Action 输出
  - 本脚本生成 perf_conf_v3 训练集,在 v2 基础上:
    1. **补全 _action 字段**(critical→alert, high→alert, medium→review, low→keep, safe→keep)
       解决 train_step1 build_target 时 action=None 的问题
    2. **新增 ~150 条 abstract critical 模式**,专注于:
       - boot_s ≤ 60 + bugcount ≥ 1 → critical
       - ndis_compliance_detected + disconnected_tap ≥ 1 → critical
       - 0x3b ACCESS_VIOLATION + offset 0x...e61b7 → critical (用户机器特有 fingerprint)
       - KERNEL_SECURITY_CHECK_FAILURE + Driver Verifier enabled → critical
       - tap0901.sys BSOD stack trace → critical
    3. **confidence 重标**:critical 0.90-0.98, high 0.70-0.85, medium 0.50-0.70,
       low/safe 0.85-0.99(让 confidence 与风险等级正相关,calibration 可用)

输入:
  /workspace/companion/data/data_perf_v2.jsonl  (1045 条)
  /workspace/companion/data/data_perf_v2_conf.jsonl  (1045 条 conf 版)

输出:
  /workspace/companion/data/data_perf_v3.jsonl  (~1200 条 base)
  /workspace/companion/data/data_perf_v3_conf.jsonl  (~1200 条 conf)

训练命令(GPU 实例内):
  python train_step1.py --data data_perf_v3.jsonl --schema perf --epochs 4 \
      --base-model Qwen/Qwen3Guard-Gen-0.6B --output outputs/qwen3guard-perf-conf-v3
  python train_step1.py --data data_perf_v3_conf.jsonl --schema perf --epochs 4 \
      --base-model Qwen/Qwen3Guard-Gen-0.6B --output outputs/qwen3guard-perf-conf-v3-c

为什么独立脚本(不改 data_prep_perf.py):
  - 不影响 M3.51 perf_conf v2 已训产物(bench 可重现)
  - v3 是实验性重训,若 ACC 不升可快速回退 v2
  - 单独审计 v3 abstract 数据对 ACC 提升的贡献
"""
from __future__ import annotations
import argparse
import json
import random
import re
from pathlib import Path

# risk → action 映射(同 data_prep_perf.ACTION_MAP)
RISK_TO_ACTION = {
    "safe": "keep",
    "low": "keep",
    "medium": "review",
    "high": "alert",
    "critical": "alert",
}

# risk → conf 范围(对齐 Jev 风格 calibration)
RISK_CONF_RANGE = {
    "critical": (0.90, 0.98),
    "high":     (0.70, 0.85),
    "medium":   (0.50, 0.70),
    "low":      (0.85, 0.97),
    "safe":     (0.85, 0.99),
}


def _add_action_and_conf(sample: dict, rng: random.Random) -> dict:
    """给样本补 _action + 重生成 _conf(risk + jailbreak)。"""
    risk = sample["risk_label"]
    sample["_action"] = RISK_TO_ACTION[risk]
    # jailbreak_label perf 场景恒为 False,但训练时要一致
    sample.setdefault("jailbreak_label", False)
    lo, hi = RISK_CONF_RANGE[risk]
    sample["_conf"] = {
        "risk": round(rng.uniform(lo, hi), 4),
        "jailbreak": round(rng.uniform(0.98, 1.0), 4),  # 几乎恒为 1
        "action": round(rng.uniform(lo, hi), 4),
    }
    return sample


# ============================================================
# Abstract critical patterns — M3.62 新增(~150 条)
# ============================================================
# 每个 pattern = 一个 (template_text, fingerprint_keywords) 对
# 训练时随机填充 fingerprint 生成 N 条

ABSTRACT_CRITICAL_PATTERNS: list[dict] = []

# Pattern 1: boot_within_60s_bugcheck — 用户机器 5 次 BSOD 都是这种
for boot_s in [5, 12, 18, 24, 33, 45, 58]:
    for bugcount in [1, 2, 3, 5]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"性能采样: 时间戳 03:26 UTC, 网络: 6 up / 3 down, "
                f"CPU 空闲温度 20°C 频率 3.7GHz, BugCheck fingerprint: "
                f"[boot {boot_s}s 内 bugcheck 第 {bugcount} 次, 0x3b ACCESS_VIOLATION "
                f"+ offset 0x...e61b7 + tap0901.sys 9.0.0.9 触发, NDIS compliance failure]"
                f"\n问: 这个性能快照的风险等级和推荐处理动作?"
            ),
            "tag": f"boot_bug_{boot_s}s_{bugcount}c",
        })

# Pattern 2: ndis_compliance_detected + disconnected_tap
for tap_n in [1, 2, 3]:
    for bugcount in [0, 1, 2]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"性能采样: NDIS compliance failure + {tap_n} 个 TAP miniport disconnected, "
                f"7 天内 BugCheck 累计 {bugcount} 次, tap0901.sys 9.0.0.9 未过合规检查"
                f"\n问: 这个性能快照的风险等级和推荐处理动作?"
            ),
            "tag": f"ndis_tap_{tap_n}_bug_{bugcount}",
        })

# Pattern 3: KERNEL_SECURITY_CHECK_FAILURE + Driver Verifier
for verifier in ["Driver Verifier enabled", "Verifier exclude 缺失"]:
    for ksec_count in [1, 2, 3]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"性能采样: KERNEL_SECURITY_CHECK_FAILURE × {ksec_count}, {verifier}, "
                f"recent crash dump 含 ndis!NdisMAllocateNetBufferPool + "
                f"tcpip!TcpSegmentInsertData 链"
                f"\n问: 这个性能快照的风险等级和推荐处理动作?"
            ),
            "tag": f"ksec_v_{ksec_count}",
        })

# Pattern 4: tap0901.sys BSOD stack trace + 用户机器特有
for offset in ["0xfffff801`8e3ce61b7", "0xfffff803`a1e3ce61b7", "0xfffff805`c4e3ce61b7"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"性能采样: BSOD stack trace — ndis!NdisMSendNetBufferObjects+0x1b "
            f"+ tap0901!TapDeviceSend+0x42 + offset {offset}, "
            f"3 个 TAP miniport disconnected ≥7 天, 0x3b ACCESS_VIOLATION"
            f"\n问: 这个性能快照的风险等级和推荐处理动作?"
        ),
        "tag": f"tap_bsod_{offset[-4:]}",
    })

# Pattern 5: 高 CPU + OOM 复合 critical
for cpu in [98, 100]:
    for oom in ["swap 6GB", "page fault in nonpaged area ×3", "工作集 28GB / 32GB"]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"性能采样: CPU 持续 {cpu}% × 5 分钟, {oom}, "
                f"KERNEL_POWER 41 事件触发, thermal throttling 已停用"
                f"\n问: 这个性能快照的风险等级和推荐处理动作?"
            ),
            "tag": f"cpu_oom_{cpu}_{oom[:6]}",
        })

# Pattern 6: 0xc0000005 ACCESS_VIOLATION + 用户态 app
for app in ["docker-desktop", "node.exe", "msedge.exe", "Code.exe"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"性能采样: 0xc0000005 ACCESS_VIOLATION in {app} + 0x3b "
            f"+ page fault in nonpaged area, dump 显示 ndis.sys 内核态栈"
            f"\n问: 这个性能快照的风险等级和推荐处理动作?"
        ),
        "tag": f"av_app_{app[:6]}",
    })


def generate_abstract_critical(n: int = 150, seed: int = 42) -> list[dict]:
    """从 ABSTRACT_CRITICAL_PATTERNS 采样 n 条,带 _action + _conf。"""
    rng = random.Random(seed)
    if n > len(ABSTRACT_CRITICAL_PATTERNS):
        # 不足就重复采样
        idx = [rng.randrange(len(ABSTRACT_CRITICAL_PATTERNS)) for _ in range(n)]
    else:
        idx = rng.sample(range(len(ABSTRACT_CRITICAL_PATTERNS)), n)
    out = []
    for i in idx:
        p = ABSTRACT_CRITICAL_PATTERNS[i]
        s = {
            "text": p["template"],
            "risk_label": p["risk_label"],
            "jailbreak_label": False,
        }
        _add_action_and_conf(s, rng)
        out.append(s)
    return out


def load_v2_and_patch(v2_path: Path, rng: random.Random) -> list[dict]:
    """读 v2 数据,补 _action + _conf。"""
    samples = []
    with open(v2_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            _add_action_and_conf(s, rng)
            samples.append(s)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.62 perf_conf_v3 critical 数据生成")
    ap.add_argument("--v2-base", default="/workspace/companion/data/data_perf_v2.jsonl")
    ap.add_argument("--v2-conf", default="/workspace/companion/data/data_perf_v2_conf.jsonl")
    ap.add_argument("--out-base", default="/workspace/companion/data/data_perf_v3.jsonl")
    ap.add_argument("--out-conf", default="/workspace/companion/data/data_perf_v3_conf.jsonl")
    ap.add_argument("--n-abstract", type=int, default=150,
                    help="abstract critical pattern 采样数")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    # 1. 读 v2 base + 补字段
    print(f"[1/3] 读 v2 base: {args.v2_base}")
    base = load_v2_and_patch(Path(args.v2_base), rng)
    print(f"  + {len(base)} 条 base")

    # 2. 读 v2 conf(已有 _conf,只补 _action)
    print(f"[2/3] 读 v2 conf: {args.v2_conf}")
    conf_samples = []
    with open(args.v2_conf, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            s["_action"] = RISK_TO_ACTION[s["risk_label"]]
            s.setdefault("jailbreak_label", False)
            conf_samples.append(s)
    print(f"  + {len(conf_samples)} 条 conf")

    # 3. 加 abstract critical
    print(f"[3/3] 生成 abstract critical × {args.n_abstract}")
    abstract = generate_abstract_critical(args.n_abstract, seed=args.seed)
    print(f"  + {len(abstract)} 条 abstract")

    base_total = base + abstract
    conf_total = conf_samples + abstract  # conf 与 base 共享 abstract 子集

    # 写
    Path(args.out_base).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_base, "w", encoding="utf-8") as f:
        for s in base_total:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  → {args.out_base} ({len(base_total)} 条)")

    with open(args.out_conf, "w", encoding="utf-8") as f:
        for s in conf_total:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  → {args.out_conf} ({len(conf_total)} 条)")

    # 风险分布统计
    from collections import Counter
    ct = Counter(s["risk_label"] for s in base_total)
    print(f"\nbase 分布: {dict(ct)}")
    print(f"critical 占比: {ct['critical']/len(base_total):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())