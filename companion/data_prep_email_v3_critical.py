"""data_prep_email_v3_critical.py — M3.64 email_conf_v3 critical 增强数据生成(2026-09-23)

目的:
  - M3.61 评测 email_conf_v1 ACC 63%(critical 漏报),根因:
    baseline 400 条 critical 仅 33 条(8%),0.6B 模型对 critical 抽象模式学习不够
  - 本脚本生成 email_conf_v3 训练集,在 baseline(data_email.jsonl)基础上:
    1. 补全 _action 字段(critical→keep, high→delete, medium→reply, low→archive, safe→archive)
    2. 新增 ~80 条 abstract critical 模式,专注于:
       - 银行/支付/信用卡欺诈识别
       - 验证码/密码重置钓鱼
       - 账户冻结/异常登录钓鱼
       - 退订/取消订阅确认
    3. confidence 重标:同 perf_v3 一致

输入:
  companion/data/data_email.jsonl  (baseline 400 条)
输出:
  companion/data/data_email_v3.jsonl  (~480 条 base)
  companion/data/data_email_v3_conf.jsonl  (~480 条 conf)
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

RISK_TO_ACTION = {
    "safe": "archive",
    "low": "archive",
    "medium": "reply",
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
# Abstract critical patterns — M3.64 email 新增
# ============================================================
ABSTRACT_CRITICAL_PATTERNS: list[dict] = []

_DAYS_CHOICE = [1, 3, 7, 14, 28, 55, 90]


def _days_str(rng: random.Random) -> str:
    return f"{rng.choice(_DAYS_CHOICE)} 天"


def _six_digit(rng: random.Random) -> str:
    return str(rng.randint(100000, 999999))


# Pattern 1: 银行/支付钓鱼(账户冻结 + 验证链接)
for sender in ["service@paypal.com", "secure@chase-bank.com",
               "alerts@citi-online.com", "noreply@stripe-verify.com",
               "security@hsbc-online.com"]:
    for subject in [
        "Verify your identity - Suspicious Activity Detected",
        "Account suspended - action required within 24 hours",
        "Confirm your account - unusual sign-in from Lagos",
        "Your card has been locked - verify now",
    ]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"发件人: {sender}\n"
                f"主题: {subject}\n"
                f"正文摘要: 您的账户存在异常活动。为保护账户安全,我们已临时冻结。"
                f"请点击下方链接完成身份验证以解除冻结。链接有效期 24 小时。\n"
                f"距今: {_days_str(random.Random(0))}\n"
                f"附件: 否\n"
                f"问: 这封邮件应该如何分类与处理?"
            ),
            "tag": f"bank_phish_{sender.split('@')[1][:6]}_{subject[:8]}",
        })

# Pattern 2: 验证码/密码重置钓鱼
for sender in ["noreply@accounts.google.com", "no-reply@appleid.apple.com",
               "support@github-verify.com", "security@dropbox-mail.com"]:
    for subject in [
        "Verification code: 6-digit code",
        "Reset your password - action required",
        "Two-factor authentication code",
    ]:
        ABSTRACT_CRITICAL_PATTERNS.append({
            "risk_label": "critical",
            "template": (
                f"发件人: {sender}\n"
                f"主题: {subject}\n"
                f"正文摘要: 您的账户正在进行身份验证。"
                f"请使用验证码 {_six_digit(random.Random(0))} 完成登录。"
                f"如非本人操作,请点击下方链接立即冻结账户。\n"
                f"距今: 1 天\n"
                f"附件: 否\n"
                f"问: 这封邮件应该如何分类与处理?"
            ),
            "tag": f"verify_phish_{sender.split('@')[1][:6]}",
        })

# Pattern 3: 账户冻结 / 异常登录
for sender in ["security@accounts.microsoft.com", "alert@facebookmail.com",
               "noreply@linkedin-security.com", "team@twitter-notify.com"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"发件人: {sender}\n"
            f"主题: Unusual sign-in to your account from China\n"
            f"正文摘要: 我们检测到来自 China 的异常登录尝试。"
            f"如果不是您本人操作,您的账户已被临时冻结。"
            f"请点击下方链接确认身份并解冻账户。\n"
            f"距今: 28 天\n"
            f"附件: 否\n"
            f"问: 这封邮件应该如何分类与处理?"
        ),
        "tag": f"signin_phish_{sender.split('@')[1][:6]}",
    })

# Pattern 4: 退订 / 取消订阅确认
for sender in ["newsletter@medium.com", "team@substack-mail.com",
               "billing@notion.so", "service@figma-mail.com"]:
    ABSTRACT_CRITICAL_PATTERNS.append({
        "risk_label": "critical",
        "template": (
            f"发件人: {sender}\n"
            f"主题: Confirm your subscription cancellation\n"
            f"正文摘要: 我们收到您的取消订阅请求。"
            f"请点击下方链接在 24 小时内确认取消,否则您的账户将被永久删除。\n"
            f"距今: 7 天\n"
            f"附件: 否\n"
            f"问: 这封邮件应该如何分类与处理?"
        ),
        "tag": f"unsubscribe_phish_{sender.split('@')[1][:6]}",
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
    ap = argparse.ArgumentParser(description="M3.64 email_conf_v3 critical 数据生成")
    ap.add_argument("--baseline", default="data/data_email.jsonl")
    ap.add_argument("--out-base", default="data/data_email_v3.jsonl")
    ap.add_argument("--out-conf", default="data/data_email_v3_conf.jsonl")
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