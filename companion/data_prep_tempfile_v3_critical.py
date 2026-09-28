"""data_prep_tempfile_v3_critical.py — M3.64 tempfile_conf_v3 critical 增强数据生成(2026-09-23)

目的:
  - M3.61 评测 tempfile_conf_v1 ACC 45%(critical 类漏报),根因类似 perf v2:
    baseline 290 条 critical 仅 20 条(7%),0.6B 模型对 critical 抽象模式学习不够
  - 本脚本生成 tempfile_conf_v3 训练集,在 baseline(data_tempfile.jsonl)基础上:
    1. 补全 _action 字段(critical→keep, high→delete, medium→review, low/safe→delete)
    2. 新增 ~80 条 abstract critical 模式,专注于:
       - 密钥/证书文件(.pem/.key/.pfx/.p12/.keystore/.jks)在常见误报位置
       - dotenv/credentials 关键配置文件
       - 钱包/数据库凭证(.wallet/.dat/.sqlite 在金融/医疗目录)
    3. confidence 重标:同 perf_v3 一致

输入:
  companion/data/data_tempfile.jsonl  (baseline 290 条)
输出:
  companion/data/data_tempfile_v3.jsonl  (~370 条 base)
  companion/data/data_tempfile_v3_conf.jsonl  (~370 条 conf)
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

RISK_TO_ACTION = {
    "safe": "delete",
    "low": "delete",
    "medium": "review",
    "high": "delete",
    "critical": "keep",
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
# Abstract critical patterns — M3.64 tempfile 新增
# ============================================================
ABSTRACT_CRITICAL_PATTERNS: list[dict] = []

# Pattern 1: 密钥/证书文件在常见误报位置(node_modules/dist/build)
for ext in [".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"]:
    for parent in [
        "node_modules\\my-lib\\dist\\cert",
        "node_modules\\crypto\\test\\fixtures",
        "build\\static\\assets",
        ".next\\cache\\webpack",
        "dist\\tmp\\ssl",
        "venv\\Lib\\site-packages\\requests\\certs",
        "__pycache__\\..\\certificates",
    ]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"文件路径: Users\\Developer\\projects\\app\\{parent}\\server_cert{ext}"
                f"\n扩展名: {ext}"
                f"\n大小: 4KB"
                f"\n年龄: 365 天"
                f"\n问: 这个文件是否可以安全清理?"
            ),
            "tag": f"key_cert_{ext.replace('.','')}_{parent.split(chr(92))[0][:8]}",
        })

# Pattern 2: dotenv / credentials 关键配置
for config_name in [".env.production", ".env.local", "credentials.json",
                    "service-account.json", "gha-creds.p12", "aws_credentials",
                    ".npmrc_token", ".pypirc"]:
    for parent in [
        "Users\\Developer\\projects\\app",
        "Users\\CI\\workspace\\pipeline",
        "Users\\Admin\\Documents\\projects\\backend",
    ]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"文件路径: {parent}\\{config_name}"
                f"\n扩展名: {'(无)' if config_name.startswith('.') else config_name.split('.')[-1]}"
                f"\n大小: 1KB"
                f"\n年龄: 60 天"
                f"\n问: 这个文件是否可以安全清理?"
            ),
            "tag": f"dotenv_{config_name[:8]}_{parent.split(chr(92))[-1][:6]}",
        })

# Pattern 3: 钱包/凭证(.wallet/.dat/.sqlite)在金融/医疗目录
for db in ["wallet.dat", "keystore.dat", "blockchain.sqlite", "patient_records.db",
           "medical_history.sqlite"]:
    for parent in [
        "Users\\Admin\\AppData\\Roaming\\Bitcoin",
        "Users\\Doctor\\Documents\\EHR",
        "Users\\Admin\\Documents\\Finance\\ledger",
    ]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"文件路径: {parent}\\{db}"
                f"\n扩展名: {db.split('.')[-1]}"
                f"\n大小: {200 if 'sqlite' in db or 'db' in db else 50}KB"
                f"\n年龄: 180 天"
                f"\n问: 这个文件是否可以安全清理?"
            ),
            "tag": f"wallet_db_{db.split('.')[0][:6]}",
        })

# Pattern 4: SSH / GPG 密钥文件
for ssh_path in [".ssh\\id_ed25519", ".ssh\\id_rsa", ".gnupg\\private-keys-v1.d\\ABC.key",
                 ".gnupg\\openpgp-revocs.d\\DEF.rev"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"文件路径: Users\\Administrator\\{ssh_path}"
            f"\n扩展名: {'(无)' if '.' not in ssh_path.split(chr(92))[-1] else ssh_path.split('.')[-1]}"
            f"\n大小: 3KB"
            f"\n年龄: 730 天"
            f"\n问: 这个文件是否可以安全清理?"
        ),
        "tag": f"ssh_{ssh_path.split(chr(92))[-1].replace('.','')[:8]}",
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
    ap = argparse.ArgumentParser(description="M3.64 tempfile_conf_v3 critical 数据生成")
    ap.add_argument("--baseline", default="data/data_tempfile.jsonl")
    ap.add_argument("--out-base", default="data/data_tempfile_v3.jsonl")
    ap.add_argument("--out-conf", default="data/data_tempfile_v3_conf.jsonl")
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