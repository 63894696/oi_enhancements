"""data_prep_log_v3_critical.py — M3.64 log_conf_v3 critical 增强数据生成(2026-09-23)

目的:
  - M3.61 评测 log_conf_v1 ACC 82%(critical 39 条占 10%),根因类似 perf v2:
    baseline 中 critical 仅 10-15%,0.6B 模型对 critical 抽象模式学习不够
  - 本脚本生成 log_conf_v3 训练集,在 baseline(data_log.jsonl)基础上:
    1. 补全 _action 字段(critical→alert, high→alert, medium→review, low→keep, safe→drop)
    2. 新增 ~80 条 abstract critical 模式,专注于:
       - Windows Event Log BSOD (KERNEL_SECURITY_CHECK_FAILURE / 0x3b 等)
       - 应用层 Traceback 链 + 数据损坏 (segfault + 数据库连接失败)
       - 内核 segfault + 关键驱动崩溃 (ndis.sys / tap0901.sys)
       - 关键服务退出 (Event ID 7034 unexpected termination)
    3. confidence 重标:同 perf_v3 一致

输入:
  companion/data/data_log.jsonl  (baseline 400 条)
输出:
  companion/data/data_log_v3.jsonl  (~480 条 base)
  companion/data/data_log_v3_conf.jsonl  (~480 条 conf)
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

RISK_TO_ACTION = {
    "safe": "drop",
    "low": "keep",
    "medium": "review",
    "high": "alert",
    "critical": "alert",
}

RISK_CONF_RANGE = {
    "critical": (0.90, 0.98),
    "high":     (0.70, 0.85),
    "medium":   (0.50, 0.70),
    "low":      (0.85, 0.97),
    "safe":     (0.85, 0.99),
}


def _add_action_and_conf(sample: dict, rng: random.Random) -> dict:
    risk = sample["risk_label"]
    sample["_action"] = RISK_TO_ACTION[risk]
    sample.setdefault("jailbreak_label", False)
    lo, hi = RISK_CONF_RANGE[risk]
    sample["_conf"] = {
        "risk": round(rng.uniform(lo, hi), 4),
        "jailbreak": round(rng.uniform(0.98, 1.0), 4),
        "action": round(rng.uniform(lo, hi), 4),
    }
    return sample


# ============================================================
# Abstract critical patterns — M3.64 log 新增
# ============================================================
ABSTRACT_CRITICAL_PATTERNS: list[dict] = []

# Pattern 1: Windows Event Log BSOD - kernel-power 41
for bugcode in ["0x0000003b", "0x00000124", "0x0000007e", "0x000000ef",
                "0x000000d1", "0x000000c2", "0x0000009f"]:
    for component in ["ndis.sys", "tcpip.sys", "tap0901.sys", "Wdf01000.sys", "Netwtw10.sys"]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"日志来源: kernel\n"
                f"时间戳: 2026/09/23 03:26:12\n"
                f"级别: critical\n"
                f"内容: BugCheckCode {bugcode}; "
                f"BugCheckParameter1=0xffffffff`c0000005 "
                f"BugCheckParameter2=0xfffff803`{random.randint(0, 0xfffffff):x}\n"
                f"WheaReport: {component} 内核态异常, NDIS compliance failure\n"
                f"问: 这条日志应该如何分类与处理?"
            ),
            "tag": f"bugcheck_{bugcode[-3:]}_{component.split('.')[0][:6]}",
        })

# Pattern 2: KERNEL_SECURITY_CHECK_FAILURE + Verifier
for n in [1, 2, 3]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"日志来源: kernel\n"
            f"时间戳: 2026/09/23 03:26:12\n"
            f"级别: critical\n"
            f"内容: KERNEL_SECURITY_CHECK_FAILURE × {n}, "
            f"Driver Verifier enabled, "
            f"recent crash dump 含 ndis!NdisMAllocateNetBufferPool + "
            f"tcpip!TcpSegmentInsertData 链\n"
            f"问: 这条日志应该如何分类与处理?"
        ),
        "tag": f"ksec_v_{n}",
    })

# Pattern 3: 内核 segfault + 关键驱动
for driver in ["ndis", "tap0901", "tcpip", "Wdf01000", "Netwtw10"]:
    for offset in [f"0x7f{''.join(random.choices('0123456789abcdef', k=8))}" for _ in range(2)]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"日志来源: kernel\n"
                f"时间戳: 2026/09/23 01:12:06\n"
                f"级别: critical\n"
                f"内容: kernel: [12345.678] segfault at 0 ip {offset} sp 0x7fff{random.randint(100000, 999999)} "
                f"in {driver}.ko+0x42 task: docker-desktop/1234\n"
                f"问: 这条日志应该如何分类与处理?"
            ),
            "tag": f"segfault_{driver[:6]}_{offset[-4:]}",
        })

# Pattern 4: 关键服务 Event ID 7034 异常退出
for service in ["wuauserv", "WinDefend", "BITS", "Schedule",
                "wscsvc", "eventlog", "LanmanServer"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"日志来源: system\n"
            f"时间戳: 2026/09/23 04:05:22\n"
            f"级别: error\n"
            f"内容: Service Control Manager: Event ID 7034 — "
            f"The {service} service terminated unexpectedly. "
            f"It has done this {random.randint(2, 10)} time(s). "
            f"The following corrective action will be taken in 5000 milliseconds: "
            f"Restart the service.\n"
            f"问: 这条日志应该如何分类与处理?"
        ),
        "tag": f"svc_7034_{service[:8]}",
    })

# Pattern 5: 应用层 Traceback + 数据库连接 + 数据损坏
for db in ["psycopg2", "mysql-connector", "sqlalchemy", "pymongo"]:
    for app in ["docker-desktop", "node.exe", "Code.exe", "msedge.exe"]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"日志来源: {app}\n"
                f"时间戳: 2026/09/23 02:23:12\n"
                f"级别: critical\n"
                f"内容: Traceback (most recent call last):\n"
                f"  File '/app/db.py', line 15, in connect\n"
                f"    self.conn = {db}.connect(**kwargs)\n"
                f"{db}.OperationalError: connection terminated by server; "
                f"data corruption detected in tables [users, transactions]\n"
                f"问: 这条日志应该如何分类与处理?"
            ),
            "tag": f"traceback_{db[:6]}_{app[:6]}",
        })


def generate_abstract_critical(n: int = 80, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    if n > len(ABSTRACT_CRITICAL_PATTERNS):
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


def load_baseline_and_patch(path: Path, rng: random.Random) -> tuple[list[dict], list[dict]]:
    base_samples = []
    conf_samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            base = dict(s)
            base_samples.append(base)
            conf = dict(s)
            _add_action_and_conf(conf, rng)
            conf_samples.append(conf)
    return base_samples, conf_samples


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.64 log_conf_v3 critical 数据生成")
    ap.add_argument("--baseline", default="data/data_log.jsonl")
    ap.add_argument("--out-base", default="data/data_log_v3.jsonl")
    ap.add_argument("--out-conf", default="data/data_log_v3_conf.jsonl")
    ap.add_argument("--n-abstract", type=int, default=80)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    print(f"[1/3] 读 baseline: {args.baseline}")
    base, conf = load_baseline_and_patch(Path(args.baseline), rng)
    print(f"  + {len(base)} 条 base / {len(conf)} 条 conf")

    print(f"[2/3] 生成 abstract critical × {args.n_abstract}")
    abstract = generate_abstract_critical(args.n_abstract, seed=args.seed)
    print(f"  + {len(abstract)} 条 abstract")

    base_total = base + abstract
    conf_total = conf + abstract

    Path(args.out_base).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_base, "w", encoding="utf-8") as f:
        for s in base_total:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  → {args.out_base} ({len(base_total)} 条)")

    with open(args.out_conf, "w", encoding="utf-8") as f:
        for s in conf_total:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  → {args.out_conf} ({len(conf_total)} 条)")

    from collections import Counter
    ct = Counter(s["risk_label"] for s in base_total)
    print(f"\nbase 分布: {dict(ct)}")
    print(f"critical 占比: {ct['critical']/len(base_total):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())