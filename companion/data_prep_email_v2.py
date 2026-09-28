#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_email_v2.py — M3.66+ 扩 email 数据(2026-09-24)

补强 email 数据集的两大弱项:
  - critical 33→120(银行/验证码/密码重置 — 安全相关漏报风险最高)
  - high 73→120(账单/合同/订单 — 重要但不够覆盖)
  - 其它类略补,保持平衡

合成策略:从 data_prep_email.py 现有 SUBJECT_TEMPLATES + SENDERS 复用,
加更多 subject/body 模板变体。
"""
from __future__ import annotations
import json
import random
from pathlib import Path


CRITICAL_SENDERS = [
    "security@accounts.google.com",
    "noreply@accounts.microsoft.com",
    "service@alipay.com",
    "service@paypal.com",
    "alert@chase.com",
    "no-reply@apple.com",
    "support@steamcommunity.com",
    "verify@proton.me",
    "no-reply@bankofamerica.com",
    "noreply@wellsfargo.com",
    "alert@citi.com",
    "noreply@stripe.com",
    "security-noreply@github.com",
    "noreply@anthropic.com",
    "verify@openai.com",
    "noreply@discord.com",
    "no-reply@binance.com",
    "security@coinbase.com",
    "noreply@okta.com",
    "noreply@1password.com",
]

CRITICAL_SUBJECTS = [
    "【安全提醒】您的账户在 $location 检测到新设备登录",
    "Password reset request for your $brand account",
    "Verify your identity - $brand",
    "您的 $brand 账户存在风险,请立即确认",
    "大额交易提醒:您的 $brand 账户于 $time 支出 ¥$amount",
    "Suspicious sign-in to your $brand account from $location",
    "您的 $brand 密码已重置",
    "【紧急】您的 $brand 账户已被锁定,请立即验证",
    "New device sign-in alert: $brand from $location",
    "您的 $brand 信用卡在 $location 消费 $amount",
    "Someone accessed your $brand account",
    "Confirm your $brand account email change",
    "Your $brand password was changed",
    "Two-factor authentication disabled for $brand",
    "API key revoked for $brand",
    "Unusual activity on your $brand account",
    "您的 $brand 钱包出现异常转账",
    "Withdrawal request for $amount from your $brand account",
]

CRITICAL_BODY_TEMPLATES = [
    "We detected a new sign-in to your Chrome. $location at $time. If this wasn't you, secure your account.",
    "您的账户在 $time 出现新设备登录。地点:$location。如非本人操作,请立即冻结账户。",
    "A password reset was requested for $brand. If you didn't make this request, contact us immediately.",
    "您的 $brand 账户检测到异常活动。请在 24 小时内确认身份,否则账户将被冻结。",
    "$amount has been deducted from your $brand account at $time. Transaction ID: $txn_id.",
]

HIGH_SENDERS = [
    "billing@amazon.com",
    "order@jd.com",
    "orders@taobao.com",
    "noreply@stripe.com",
    "billing@github.com",
    "tax@irs.gov",
    "statement@chase.com",
    "noreply@upwork.com",
    "billing@netflix.com",
    "orders@meituan.com",
    "orders@ele.me",
    "billing@dropbox.com",
    "orders@aliexpress.com",
    "billing@figma.com",
    "noreply@adobe.com",
    "billing@notion.so",
    "orders@uber.com",
    "receipts@lyft.com",
    "billing@anthropic.com",
    "billing@openai.com",
]

HIGH_SUBJECTS = [
    "您的 $brand 订单 #$order_id 已确认",
    "Invoice #$order_id from $brand",
    "您的 $brand 月度账单已生成",
    "$brand 收据:订单 #$order_id 共 ¥$amount",
    "$brand 月度结算报告 - $date",
    "Tax statement for $brand - $date",
    "您的 $brand 订阅续费成功",
    "Receipt for your $brand purchase - $date",
    "您的 $brand 订单已发货,运单号 #$tracking",
    "Order shipped: $brand #$order_id",
    "您的 $brand Pro 会员续费 ¥$amount",
    "$brand Workspace upgrade confirmed",
    "您的 $brand 团队订阅已升级",
    "Payment received: $brand invoice #$order_id",
    "您的 $brand 月度用量报告 - $date",
]

BRANDS = [
    "Amazon", "Google", "Microsoft", "Apple", "京东", "淘宝",
    "GitHub", "GitLab", "Notion", "Figma", "Adobe", "Stripe",
    "Netflix", "Spotify", "YouTube Premium", "Dropbox", "iCloud",
    "Anthropic", "OpenAI", "ChatGPT Plus", "Claude Pro",
    "GitHub Copilot", "JetBrains", "1Password",
]
LOCATIONS = ["北京", "上海", "深圳", "杭州", "San Francisco", "Tokyo", "London", "Berlin", "Singapore", "Paris"]
AMOUNTS = [f"{rng}" for rng in [99, 199, 299, 499, 999, 1499, 2999, 4999, 9999]]
ORDER_IDS = [f"{i:08d}" for i in range(10000000, 10000020)]
TXN_IDS = [f"txn_{i:06d}" for i in range(100000, 100020)]
TRACKING_IDS = [f"YT{i:010d}" for i in range(1000000000, 1000000010)]
TIMES = ["今天 14:23", "yesterday at 3:42 AM", "10 minutes ago", "1 hour ago", "昨天 22:18", "3 days ago"]
DATES = [f"2025-09-{i:02d}" for i in range(1, 30)] + [f"2025-10-{i:02d}" for i in range(1, 5)]


def fill_template(t: str, rng: random.Random) -> str:
    return (t.replace("$brand", rng.choice(BRANDS))
              .replace("$location", rng.choice(LOCATIONS))
              .replace("$amount", rng.choice(["99", "199", "499", "999", "1499", "2999", "4999"]))
              .replace("$time", rng.choice(TIMES))
              .replace("$date", rng.choice(DATES))
              .replace("$order_id", rng.choice(ORDER_IDS))
              .replace("$txn_id", rng.choice(TXN_IDS))
              .replace("$tracking", rng.choice(TRACKING_IDS)))


def gen_email(rng: random.Random, tier: str) -> dict:
    if tier == "critical":
        sender = rng.choice(CRITICAL_SENDERS)
        subject = fill_template(rng.choice(CRITICAL_SUBJECTS), rng)
        body = fill_template(rng.choice(CRITICAL_BODY_TEMPLATES), rng)
    else:  # high
        sender = rng.choice(HIGH_SENDERS)
        subject = fill_template(rng.choice(HIGH_SUBJECTS), rng)
        body = f"Your {subject.split('#')[0].strip()} details are in your account dashboard."

    return {
        "id": f"email_v2.{tier}.{rng.randint(0,99999):05d}",
        "text": f"邮件发件人: {sender}\n邮件主题: {subject}\n邮件正文摘要: {body}",
        "risk_label": tier,
        "_action": "keep" if tier == "critical" else "review",
    }


def main() -> int:
    rng = random.Random(42)
    # 目标:critical 120 + high 120 = 240 合成
    samples = []
    for i in range(120):
        samples.append(gen_email(rng, "critical"))
    for i in range(120):
        samples.append(gen_email(rng, "high"))
    rng.shuffle(samples)

    out_path = Path(__file__).parent / "data" / "data_email_v2.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    label_counts = {}
    for s in samples:
        label_counts[s["risk_label"]] = label_counts.get(s["risk_label"], 0) + 1
    print(f"✅ email v2: {len(samples)} samples → {out_path}")
    print(f"   分布: {label_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())