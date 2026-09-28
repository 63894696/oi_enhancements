#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_email.py — M3.48 邮件分类训练集准备(2026-09-23)
#
# 目的:
#   - 邮件分类:5 类风险等级 + 3 个 action 建议
#   - 复用 disk_cleanup 的 5 类分级模式,但语义不同:
#     safe     — 通讯/通知类,留着不占内存
#     low      — 订阅/促销,可定期清
#     medium   — 工作邮件,需 reply 或 archive
#     high     — 重要账单/合同,keep
#     critical — 安全告警/密码重置/银行类,keep + verify
#
#   - 3 action:delete(可清)/archive(留档)/reply(需回复)
#
# 与前面 M3.46/M3.47 的差别:
#   - 数据源:合成(规则生成器)— 本机没有可用邮件数据集
#   - 合成模式:From + Subject + Body snippet + 时间特征
#   - 5 类分级规则:看发件人域名 + 主题关键词 + 链接特征
#
# 决策依据:
#   safe     — 内部系统通知(本人->本人的提醒)、收据类(银行/支付宝)单向通知
#   low      — 营销/促销/订阅/邀请函(LinkedIn/微博等社交平台通知)
#   medium   — 工作邮件(项目/客户/同事,有 reply/forward 历史)
#   high     — 账单/合同/订单(Amazon/京东/淘宝订单确认)、税务通知
#   critical — 安全告警(密码重置、新设备登录)、银行大额变动
#
# 用法:
#   python data_prep_email.py --output data_email.jsonl [--limit 600]
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Iterator

# ------------------------------------------------------------
# 5 类规则(从发件人 + 主题 + body 片段联合判定)
# ------------------------------------------------------------

# critical:安全告警、银行、密码
CRITICAL_DOMAINS = {
    "security@", "noreply@accounts.", "alert@", "no-reply@accounts.",
    "bank-alert@", "service@alipay", "service@paypal",
    "verify@", "support@apple.com", "security-noreply@",
}
CRITICAL_SUBJECTS = [
    "密码重置", "新设备登录", "异常登录", "您的账户安全",
    "大额交易提醒", "Password reset", "New device sign-in",
    "Suspicious activity", "Verify your identity",
    "您的账户存在风险", "Account locked",
]
# low:营销/促销/订阅
LOW_DOMAINS = {
    "marketing@", "promo@", "newsletter@", "noreply@newsletter",
    "deals@", "offers@", "promotions@", "info@", "campaigns@",
    "no-reply@", "noreply@", "service@bilibili", "service@zhihu",
    "service@huxiu", "service@netflix", "service@spotify",
    "service@alibaba", "service@douyin", "noreply@", "mail@",
    "auto-mail@", "system@", "noreply@xiaomi",
}
# 知名"分发/媒体/营销平台"域名(整段匹配)
LOW_DOMAIN_SUFFIXES = [
    "bilibili.com", "zhihu.com", "huxiu.com", "netflix.com",
    "spotify.com", "alibaba.com", "douyin.com", "youtube.com",
    "producthunt.com", "medium.com", "substack.com", "163.com",
    "126.com", "qq.com", "weibo.com", "xiaomi.com",
]
LOW_SUBJECTS = [
    "% OFF", "限时优惠", "新品上市", "特卖", "discount",
    "Sale", "Promo", "Newsletter", "Weekly digest",
    "邀请函", "网课报名", "限时活动", "社群", "邀请您",
    "限时", "5 折", "上新", "上新了", "限免",
]
# medium:工作邮件
MEDIUM_DOMAINS = {
    "@github.com", "@gitlab.com", "@jira.", "@slack.com",
    "@notion.so", "@linear.app", "@figma.com",
}
# safe:系统通知/收据(单向,无需回复)
SAFE_SUBJECTS = [
    "您的订单已签收", "包裹已送达", "Order delivered",
    "Backup completed", "Build successful", "Deployment finished",
    "会议提醒", "日程通知", "Reminder:",
    "签收成功", "已发货",
]

# ------------------------------------------------------------
# 模板生成(确定性,rule-based)
# ------------------------------------------------------------

SENDERS_BY_TIER: dict[str, list[str]] = {
    "critical": [
        "security@accounts.google.com",
        "noreply@accounts.microsoft.com",
        "service@alipay.com",
        "service@paypal.com",
        "alert@chase.com",
        "no-reply@apple.com",
        "support@steamcommunity.com",
        "verify@proton.me",
    ],
    "high": [
        "billing@amazon.com",
        "order@jd.com",
        "orders@taobao.com",
        "noreply@stripe.com",
        "billing@github.com",
        "tax@irs.gov",
        "statement@chase.com",
        "noreply@upwork.com",
    ],
    "medium": [
        "alice@partner-corp.com",
        "bob@client-xyz.io",
        "team@internal-startup.com",
        "notifications@github.com",
        "jira@company.atlassian.net",
        "dm@slack.com",
        "no-reply@notion.so",
        "review@gitlab.com",
        "alice.chen@partner.com",
        "pm@internal-startup.com",
    ],
    "low": [
        "marketing@netflix.com",
        "promo@spotify.com",
        "newsletter@producthunt.com",
        "deals@amazon.com",
        "offers@booking.com",
        "info@medium.com",
        "campaigns@linkedin.com",
        "no-reply@substack.com",
        "promotions@alibaba.com",
        "newsletter@huxiu.com",
        "marketing@zhihu.com",
        "service@bilibili.com",
    ],
    "safe": [
        "noreply@github.com",
        "builds@travis-ci.org",
        "deploy@internal-startup.com",
        "calendar@google.com",
        "reminders@google.com",
        "no-reply@dropbox.com",
        "noreply@icloud.com",
    ],
}

SUBJECT_TEMPLATES: dict[str, list[str]] = {
    "critical": [
        "【安全提醒】您的账户在 $location 检测到新设备登录",
        "Password reset request for your $brand account",
        "Verify your identity - $brand",
        "您的 $brand 账户存在风险,请立即确认",
        "大额交易提醒:您的 $brand 账户于 $time 支出 ¥$amount",
        "Suspicious sign-in to your $brand account from $location",
        "您的 $brand 密码已重置",
    ],
    "high": [
        "您的 $brand 订单 #$order_id 已确认",
        "Invoice #$order_id from $brand",
        "您的 $brand 月度账单已生成",
        "$brand 收据:订单 #$order_id 共 ¥$amount",
        "$brand 月度结算报告 - $date",
        "Tax statement for $brand - $date",
        "您的 $brand 订阅续费成功",
    ],
    "medium": [
        "Re: $topic 项目进展",
        "关于 $topic 的方案讨论",
        "Code review request: PR #$pr_id in $brand",
        "Meeting notes - $topic sync",
        "[JIRA] $topic bug #$pr_id 需要 review",
        "Fwd: $topic 设计稿反馈",
        "$client 客户对接 - 跟进需求",
        "Weekly report - $topic 周工作汇总",
    ],
    "low": [
        "$brand 限时 50% OFF,仅剩 $days 小时",
        "本周新片速递 - $brand",
        "您订阅的 $brand 已更新",
        "Your weekly $brand newsletter",
        "$brand 课程上线 - 早鸟价",
        "邀请您加入 $brand 社群",
        "您可能感兴趣的 $brand 活动",
        "Sale: $brand - up to $amount% off",
    ],
    "safe": [
        "您的 $brand 订单 #$order_id 已签收",
        "Build #$build_id succeeded in $brand",
        "Deployment finished: $brand -> $client",
        "Reminder: $meeting meeting at $time",
        "您的 $brand 包裹已送达",
        "Backup completed: $size GB",
        "您的 $brand 日程将于 $time 开始",
    ],
}

BODY_TEMPLATES: dict[str, list[str]] = {
    "critical": [
        "我们检测到您的账户于 $time 从 $location 登录。如非本人操作,请立即点击 https://$domain/verify 重置密码。",
        "您的账户存在异常活动。为保护账户安全,我们已临时冻结。请点击下方链接完成身份验证。",
        "您的 $brand 账户于 $time 发生 ¥$amount 交易。如非本人操作,请拨打客服热线 400-xxx-xxxx。",
    ],
    "high": [
        "您的订单 #$order_id 已确认发货,预计 $days 个工作日内送达。订单详情:$url",
        "本月度账单总额 ¥$amount,请于 $date 前完成支付。$url",
        "感谢您订阅 $brand。本期扣款 ¥$amount,续费日期 $date。",
    ],
    "medium": [
        "Hi,关于 $topic 项目,我已更新了 docs,麻烦看下第二段方案。下周一前给反馈。",
        "代码已提交到 PR #$pr_id,主要改动了 $files 个文件。请 review,有疑问直接 comment。",
        "FYI - 周三的客户会议我整理了下纪要,见附件。下一轮 sprint 我们重点做 $topic。",
        "客户 $client 要求我们在 $date 前给出方案 v2。我这边先搭了 outline,你看下方向对不对。",
    ],
    "low": [
        "全场 5 折!爆款直降 ¥100,仅限本周。立即抢购:$url",
        "本周 Top 10 新片已更新,周末休闲必备。$url",
        "我们为您精选了 $topic 相关 5 篇文章,点击查看 →",
        "$brand 早鸟价 ¥99,课程涵盖 $topics,报名截止 $date。",
    ],
    "safe": [
        "您的 $brand 订单 #$order_id 已由本人签收。感谢您的支持。",
        "Build #$build_id 成功,共 $files 个文件改动,测试通过率 100%。详情:$url",
        "提醒:$meeting 会议将于 $time 开始,会议室 $room。",
        "$brand 已完成 $size GB 数据备份,耗时 $duration 分钟。",
    ],
}

ACTION_MAP: dict[str, str] = {
    "critical": "keep",       # 留作证据
    "high": "archive",        # 入档备查
    "medium": "reply",        # 需要回复
    "low": "delete",          # 直接清
    "safe": "archive",        # 留档
}

# ------------------------------------------------------------
# 分类函数(基于规则的硬标签生成)
# ------------------------------------------------------------

def _tier_of(sender: str, subject: str, body: str) -> str:
    """返 (risk_label, action) — 跟 disk_cleanup 规则器同模式。"""
    s_lower = sender.lower()
    subj_lower = subject.lower()
    body_lower = body.lower()

    # 1. critical 优先(安全/银行)
    for d in CRITICAL_DOMAINS:
        if d in s_lower:
            return "critical", ACTION_MAP["critical"]
    for kw in CRITICAL_SUBJECTS:
        if kw.lower() in subj_lower:
            return "critical", ACTION_MAP["critical"]

    # 2. safe(系统通知/收据)
    for kw in SAFE_SUBJECTS:
        if kw.lower() in subj_lower:
            return "safe", ACTION_MAP["safe"]

    # 3. high(账单/订单)
    high_kw = ["订单", "账单", "invoice", "收据", "receipt",
               "续费", "扣款", "订阅", "statement"]
    if any(kw.lower() in subj_lower for kw in high_kw):
        return "high", ACTION_MAP["high"]
    # 知名电商发件人
    ecommerce_domains = ["@amazon.", "@jd.com", "@taobao.", "@stripe.",
                         "@alipay.", "@paypal."]
    if any(d in s_lower for d in ecommerce_domains):
        return "high", ACTION_MAP["high"]

    # 4. medium(工作邮件)
    work_kw = ["re:", "fw:", "fwd:", "code review", "pr #", "[jira]",
               "meeting", "weekly report", "方案", "反馈", "跟进", "sync"]
    if any(kw in subj_lower for kw in work_kw):
        return "medium", ACTION_MAP["medium"]
    if any(d in s_lower for d in MEDIUM_DOMAINS):
        return "medium", ACTION_MAP["medium"]

    # 5. low(营销/订阅)
    for d in LOW_DOMAINS:
        if d in s_lower:
            return "low", ACTION_MAP["low"]
    for suf in LOW_DOMAIN_SUFFIXES:
        if s_lower.endswith("@" + suf) or suf in s_lower:
            return "low", ACTION_MAP["low"]
    for kw in LOW_SUBJECTS:
        if kw.lower() in subj_lower:
            return "low", ACTION_MAP["low"]
    # body 关键字兜底(全场 5 折/抢购/优惠)
    body_low_kw = ["全场", "5 折", "抢购", "优惠", "限时", "早鸟价",
                   "Sale", "OFF", "% off"]
    if any(kw.lower() in body_lower for kw in body_low_kw):
        return "low", ACTION_MAP["low"]

    # 兜底
    return "medium", ACTION_MAP["medium"]


# ------------------------------------------------------------
# 模板参数(让样本看起来真实)
# ------------------------------------------------------------

BRANDS = ["GitHub", "Slack", "Notion", "Figma", "AWS", "Azure", "Cloudflare",
          "Stripe", "Shopify", "Linear", "Vercel", "Cloudinary", "Datadog",
          "京东", "淘宝", "拼多多", "美团", "饿了么", "滴滴", "12306",
          "招商银行", "建设银行", "工商银行", "支付宝", "微信支付"]

LOCATIONS = ["北京", "上海", "深圳", "广州", "杭州", "成都", "西安",
             "Beijing", "Shanghai", "Tokyo", "Singapore", "San Francisco"]

PROJECTS = ["用户增长", "支付系统重构", "搜索推荐", "订单中心", "客服系统",
            "Inventory sync", "Mobile redesign", "Auth flow",
            "数据看板", "AI 助手集成", "CDN 优化", "国际化"]

TOPICS_FYI = ["Q3 OKR", "Q4 规划", "技术债清理", "性能优化", "OKR 复盘",
              "design system", "tech radar", "数据迁移"]

ROOM_NAMES = ["3F-会议室 A", "Building-7 Room 201", "Standup-Channel",
              "WeWork-玻璃房", "主会议室", "Standup"]

TIME_OF_DAY = ["上午 9:30", "下午 2:00", "上午 11:15", "下午 4:45",
               "08:00 UTC", "14:30 CST", "Tuesday 10am", "周三 15:00"]


def _fill(template: str) -> str:
    """模板参数填充,让样本看起来真实。

    用 string.Template + safe_substitute — 模板里没有的占位符保持原样,
    不会因为某个 tier 模板用了不同子集而崩。
    """
    from string import Template
    rng = random.Random(hash(template) & 0xFFFFFFFF)
    mapping = {
        "brand": rng.choice(BRANDS),
        "topic": rng.choice(PROJECTS + TOPICS_FYI),
        "topics": rng.choice(PROJECTS),
        "order_id": rng.randint(100000, 999999),
        "pr_id": rng.randint(100, 9999),
        "build_id": rng.randint(1000, 99999),
        "client": rng.choice(["Acme Corp", "TechCo", "Studio-X", "某车企",
                              "某零售品牌", "FinTech Inc"]),
        "amount": rng.randint(50, 50000),
        "days": rng.choice([1, 2, 3, 5, 7]),
        "date": rng.choice(["下周一", "周五前", "月底", "next Tuesday",
                            "Sept 30", "Oct 15"]),
        "files": rng.randint(1, 12),
        "url": "https://" + rng.choice(["example.com", "internal-startup.com",
                                        "service.com"]) + f"/{rng.randint(100, 999)}",
        "domain": rng.choice(["secure-portal.com", "auth.service.com",
                              "verify.example.com"]),
        "time": rng.choice(TIME_OF_DAY),
        "location": rng.choice(LOCATIONS),
        "size": rng.randint(1, 500),
        "duration": rng.randint(2, 30),
        "meeting": rng.choice(["周会", "Sprint planning", "1:1", "客户演示",
                               "全员大会"]),
        "room": rng.choice(ROOM_NAMES),
    }
    return Template(template).safe_substitute(mapping)


def _build_text(sender: str, subject: str, body: str,
                received_hours: float, has_attachment: bool) -> str:
    """组装跟训练数据一致的 prompt 段。"""
    age_days = max(0, received_hours / 24)
    age_str = (f"{received_hours:.0f} 小时" if received_hours < 48
               else f"{age_days:.0f} 天")
    sender_lower = sender.lower()
    att = "是" if has_attachment else "否"
    return (f"发件人: {sender}\n"
            f"主题: {subject}\n"
            f"正文摘要: {body[:200]}\n"
            f"距今: {age_str}\n"
            f"附件: {att}\n"
            f"问: 这封邮件应该如何分类与处理?")


# ------------------------------------------------------------
# 多样性采样
# ------------------------------------------------------------
RISK_MIN_TARGETS = {
    "critical": 40,
    "high": 80,
    "medium": 150,
    "low": 120,
    "safe": 80,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    rng = random.Random(42)
    by_risk: dict[str, list[dict]] = {}
    for s in samples:
        by_risk.setdefault(s["risk_label"], []).append(s)

    picked: list[dict] = []
    for risk, items in by_risk.items():
        target = RISK_MIN_TARGETS.get(risk, 50)
        if len(items) <= target:
            picked.extend(items)
            continue
        # 二次抽样:按发件人域名桶平衡
        sub_buckets: dict[str, list[dict]] = {}
        for it in items:
            domain = it["_sender"].split("@")[-1].split(".")[0]
            sub_buckets.setdefault(domain, []).append(it)
        total = sum(len(v) for v in sub_buckets.values())
        kept: list[dict] = []
        for sub, sub_items in sub_buckets.items():
            share = max(int(target * len(sub_items) / total), 3)
            rng.shuffle(sub_items)
            kept.extend(sub_items[:share])
        picked.extend(kept[:target])

    rng.shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 主生成函数
# ------------------------------------------------------------
def generate(max_raw: int = 5000, seed: int = 42) -> Iterator[dict]:
    """生成候选样本(各 tier 内部随机填充模板)。"""
    rng = random.Random(seed)
    samples: list[dict] = []

    for tier in ("critical", "high", "medium", "low", "safe"):
        senders = SENDERS_BY_TIER[tier]
        subject_templates = SUBJECT_TEMPLATES[tier]
        body_templates = BODY_TEMPLATES[tier]

        per_tier = max_raw // 5
        for _ in range(per_tier):
            sender = rng.choice(senders)
            subject = _fill(rng.choice(subject_templates))
            body = _fill(rng.choice(body_templates))
            received_hours = rng.choice([
                rng.uniform(0.5, 24),    # 今天
                rng.uniform(24, 72),    # 1-3 天
                rng.uniform(72, 168),   # 3-7 天
                rng.uniform(168, 720),  # 一周-一个月
                rng.uniform(720, 2160),  # 一个月-三个月
            ])
            has_attachment = rng.random() < 0.15
            # 用 _tier_of 双重校验(发送域 vs 主题)
            risk, action = _tier_of(sender, subject, body)
            # 强制对得上(因为模板本身就分 tier 了)
            samples.append({
                "_sender": sender,
                "_subject": subject,
                "_body": body,
                "_received_hours": round(received_hours, 1),
                "_has_attachment": has_attachment,
                "risk_label": risk,
                "_action": action,
                "_source": f"gen:{tier}",
            })
            if len(samples) >= max_raw:
                return iter(samples)
    return iter(samples)


def _cross_tier_mix(count: int = 40, seed: int = 43) -> list[dict]:
    """M3.48.1 v2:cross-tier 边界样本。

    sender 来自 tier A,subject 来自 tier B (≠ A),body 跟 sender 同 tier。
    让 _tier_of() 按"主体特征"判 tier,产生歧义样本,使 confidence teacher
    能拿到 calibration 信号(否则全部确定性标签 → softmax 接近 1.0)。

    预期效果:
      - 上版 calibration delta=0.04(无法做 threshold gating)
      - 加 40 条 mix 后 delta → 0.15+,threshold gating 可用

    mix 设计:
      - 50% critical sender + low/safe subject → 模型学"发件域 > 营销伪装"
      - 50% low/safe sender + critical subject → 模型学"主题反钓鱼/警惕"
    """
    rng = random.Random(seed)
    pairs = [
        ("critical", "low"), ("critical", "safe"),
        ("low", "critical"), ("safe", "critical"),
        ("critical", "high"),  # 钓鱼伪装成账单
        ("high", "critical"),  # 真安全告警被埋
        ("medium", "low"),    # 工作邮件 + 营销噱头
        ("low", "medium"),    # 营销 + 工作关键词
    ]
    samples: list[dict] = []
    for i in range(count):
        tA, tB = rng.choice(pairs)
        sender = rng.choice(SENDERS_BY_TIER[tA])
        subject = _fill(rng.choice(SUBJECT_TEMPLATES[tB]))
        body = _fill(rng.choice(BODY_TEMPLATES[tA]))  # body 跟 sender 同 tier
        received_hours = rng.choice([
            rng.uniform(0.5, 24),
            rng.uniform(24, 72),
            rng.uniform(72, 168),
            rng.uniform(168, 720),
        ])
        has_attachment = rng.random() < 0.15
        risk, action = _tier_of(sender, subject, body)
        samples.append({
            "_sender": sender,
            "_subject": subject,
            "_body": body,
            "_received_hours": round(received_hours, 1),
            "_has_attachment": has_attachment,
            "risk_label": risk,
            "_action": action,
            "_source": f"mix:{tA}+{tB}",
        })
    return samples


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.48 邮件分类训练集准备")
    ap.add_argument("--output", default="data_email.jsonl")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--max-raw", type=int, default=5000)
    ap.add_argument("--mix-count", type=int, default=0,
                    help="M3.48.1 v2: cross-tier mixing 边界样本数(默认 0 不开)")
    args = ap.parse_args()

    print(f"[1/5] 规则生成(最多 {args.max_raw} 条)...")
    t0 = time.time()
    samples = list(generate(max_raw=args.max_raw))
    print(f"  + 候选 {len(samples)} 条,耗时 {time.time()-t0:.1f}s")

    if args.mix_count > 0:
        print(f"[2/5] cross-tier mixing 加 {args.mix_count} 条边界样本...")
        mix = _cross_tier_mix(count=args.mix_count)
        samples.extend(mix)
        print(f"  + 混合后 {len(samples)} 条")
        by_tier_mix: dict[str, int] = {}
        for s in mix:
            by_tier_mix[s["risk_label"]] = by_tier_mix.get(s["risk_label"], 0) + 1
        print(f"  by_tier(mix): {by_tier_mix}")
    else:
        print("[2/5] cross-tier mixing 关闭(默认)")

    by_tier: dict[str, int] = {}
    for s in samples:
        by_tier[s["risk_label"]] = by_tier.get(s["risk_label"], 0) + 1
    print(f"  by_tier(总): {by_tier}")

    print(f"[3/5] 多样性平衡(各 tier cap 到 RISK_MIN_TARGETS,总 ≤ {args.limit})...")
    balanced = _balance_diversity(samples, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    print(f"[4/5] 写盘: {args.output}")
    out = Path(args.output)
    final_by_tier: dict[str, int] = {}
    final_by_action: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": _build_text(s["_sender"], s["_subject"], s["_body"],
                                    s["_received_hours"], s["_has_attachment"]),
                "risk_label": s["risk_label"],
                "jailbreak_label": False,
                "_action": s["_action"],
                "_sender": s["_sender"],
                "_subject": s["_subject"],
                "_body": s["_body"][:200],
                "_received_hours": s["_received_hours"],
                "_has_attachment": s["_has_attachment"],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            final_by_tier[s["risk_label"]] = final_by_tier.get(
                s["risk_label"], 0) + 1
            final_by_action[s["_action"]] = final_by_action.get(
                s["_action"], 0) + 1

    print(f"\n  by_tier: {final_by_tier}")
    print(f"  by_action: {final_by_action}")
    print(f"  ✅ 写盘: {out} ({out.stat().st_size/1024:.1f} KB, "
          f"{len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())