"""data_prep_disk_cleanup_v3_critical.py — M3.64 disk_cleanup_conf_v3 critical 增强数据生成(2026-09-23)

目的:
  - M3.61 评测 disk_cleanup_conf_v1 ACC 63%(critical 类可能漏报),根因类似 perf v2:
    baseline 数据中 critical 占比仅 8-15%,0.6B 模型对 critical 抽象模式学习不够
  - 本脚本生成 disk_cleanup_conf_v3 训练集,在 baseline(data_disk_cleanup.jsonl)基础上:
    1. 补全 _action 字段(critical→keep, high→delete, medium→review, low/safe→delete)
    2. 新增 ~80 条 abstract critical 模式,专注于:
       - WinSxS/Backup 关键 manifest/mum 文件(系统组件备份)
       - 关键证书/manifest 路径(系统还原依赖)
       - 不常见扩展名组合但路径高危
    3. confidence 重标:critical 0.90-0.98, high 0.70-0.85, medium 0.50-0.70,
       low/safe 0.85-0.99(同 perf_v3 一致)

输入:
  companion/data/data_disk_cleanup.jsonl  (baseline ~203 条)
输出:
  companion/data/data_disk_cleanup_v3.jsonl  (~280 条 base)
  companion/data/data_disk_cleanup_v3_conf.jsonl  (~280 条 conf)
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

# risk → action 映射(对齐 disk_cleanup schema)
RISK_TO_ACTION = {
    "safe": "delete",
    "low": "delete",
    "medium": "review",
    "high": "delete",
    "critical": "keep",
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
    """给样本补 _action + 重生成 _conf。"""
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
# Abstract critical patterns — M3.64 disk_cleanup 新增
# ============================================================
ABSTRACT_CRITICAL_PATTERNS: list[dict] = []

# Pattern 1: WinSxS Backup 关键 manifest 文件
for component in [
    "webauthn", "memorydiagnostic", "crypt32", "wininet", "mscorlib",
    "system.identitymodel", "presentationframework", "wlanapi",
]:
    for arch in ["amd64", "wow64", "x86"]:
        for hash_seed in range(3):
            ABSTRACT_CRITICAL_PATTERNS.append({
                "risk_label": "critical",
                "template": (
                    f"文件路径: Windows\\WinSxS\\Backup\\{arch}_microsoft-windows-{component}_"
                    f"31bf3856ad364e35_10.0.19041.{6000 + hash_seed * 30}_none_{hash_seed:08x}abcdef0123.manifest"
                    f"\n扩展名: .manifest"
                    f"\n大小: 0KB"
                    f"\n年龄: {380 + hash_seed * 50} 天"
                    f"\n问: 这个 Windows 系统文件是否可以安全清理?"
                ),
                "tag": f"winsxs_{component}_{arch}_{hash_seed}",
            })

# Pattern 2: WinSxS Backup 关键 mum 文件
for component in ["networking-mpssvc", "wer", "wlan-driver", "kernel-base", "tsf-directshow"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"文件路径: Windows\\WinSxS\\Backup\\amd64_microsoft-windows-{component}_"
            f"31bf3856ad364e35_10.0.19041.789_{component[:3]}_a1b2c3d4e5.mum"
            f"\n扩展名: .mum"
            f"\n大小: 12KB"
            f"\n年龄: 420 天"
            f"\n问: 这个 Windows 系统文件是否可以安全清理?"
        ),
        "tag": f"winsxs_mum_{component[:6]}",
    })

# Pattern 3: 系统还原点关键文件
for driver in ["ndis", "tcpip", "netbt", "wfp", "fwpkclnt"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"文件路径: Windows\\System32\\restore\\{{GUID_restore_point}}\\{driver}.sys.backup"
            f"\n扩展名: .sys.backup"
            f"\n大小: 200KB"
            f"\n年龄: 90 天"
            f"\n问: 这个 Windows 系统文件是否可以安全清理?"
        ),
        "tag": f"restore_{driver}",
    })

# Pattern 4: 关键证书 .cer 在 Windows 系统目录
for cert_path in [
    "Windows\\System32\\config\\systemprofile\\AppData\\LocalLow\\Microsoft\\CryptnetUrlCache\\MetaData\\cert_abc123.cer",
    "Windows\\System32\\AppLocker\\AppLocker_{GUID}.cer",
    "Windows\\servicing\\Packages\\Microsoft-Windows-Security-Identity-{ver}.mum",
]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"文件路径: {cert_path}"
            f"\n扩展名: .cer/.mum"
            f"\n大小: 2KB"
            f"\n年龄: 365 天"
            f"\n问: 这个 Windows 系统文件是否可以安全清理?"
        ),
        "tag": f"cert_{cert_path.split(chr(92))[-1][:8]}",
    })

# Pattern 5: 关键驱动签名 catalog
for driver in ["tap0901", "ndis630", "Wdf01000", "WdfLdr"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"文件路径: Windows\\System32\\CatRoot\\{{GUID}}\\{driver}.cat"
            f"\n扩展名: .cat"
            f"\n大小: 45KB"
            f"\n年龄: 200 天"
            f"\n问: 这个 Windows 系统文件是否可以安全清理?"
        ),
        "tag": f"cat_{driver}",
    })


def generate_abstract_critical(n: int = 80, seed: int = 42) -> list[dict]:
    """从 ABSTRACT_CRITICAL_PATTERNS 采样 n 条,带 _action + _conf。"""
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
    """读 baseline,补 _action + 派生 _conf(conf 版)。"""
    base_samples = []
    conf_samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            # base 不动(原 _action 已在 baseline 里,但我们重写确保一致)
            base = dict(s)
            base_samples.append(base)
            # conf 派生:补 _action + _conf
            conf = dict(s)
            _add_action_and_conf(conf, rng)
            conf_samples.append(conf)
    return base_samples, conf_samples


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.64 disk_cleanup_conf_v3 critical 数据生成")
    ap.add_argument("--baseline", default="data/data_disk_cleanup.jsonl")
    ap.add_argument("--out-base", default="data/data_disk_cleanup_v3.jsonl")
    ap.add_argument("--out-conf", default="data/data_disk_cleanup_v3_conf.jsonl")
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