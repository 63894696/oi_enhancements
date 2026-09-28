#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_email.py — M3.48 + M3.45.1 邮件分类推理入口(2026-09-23)
#
# 目的:
#   - 对单封邮件做 5 类风险分级 + 给出 action(delete/archive/reply/keep)
#   - 返回 Jev 兼容 schema {risk, jailbreak, action, confidence},上层可直接复用
#   - 取代对 Jev API 的依赖(Jev 调 chat 消息语义,跟邮件级风险判断不匹配)
#
# 用法(CLI):
#   python classify_email.py --sender "service@bank.com" \
#     --subject "密码重置请求" --body "请点击链接重置密码..." --hours 2 --attachment
#   python classify_email.py --file emails.jsonl   # {"sender","subject","body","hours","attachment"} 每行一条
#   python classify_email.py --stdin < emails.jsonl
#
# 输出(JSON 一行一条):
#   {"risk":"critical","action":"keep","jailbreak":"no",
#    "risk_conf":0.87,"action_conf":0.91,"raw":"...","tokens":18,"latency_ms":2418}
#
# Python 嵌入:
#   from classify_email import classify_email
#   out = classify_email(adapter, sender, subject, body, hours, attachment)
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _build_text(sender: str, subject: str, body: str,
                received_hours: float, has_attachment: bool) -> str:
    """跟 data_prep_email._build_text 一致 — adapter 训练时见的形态。"""
    age_days = max(0, received_hours / 24)
    age_str = (f"{received_hours:.0f} 小时" if received_hours < 48
               else f"{age_days:.0f} 天")
    att = "是" if has_attachment else "否"
    return (f"发件人: {sender}\n"
            f"主题: {subject}\n"
            f"正文摘要: {body[:200]}\n"
            f"距今: {age_str}\n"
            f"附件: {att}\n"
            f"问: 这封邮件应该如何分类与处理?")


# 跟 bench_email.py / data_prep_email.py 同 schema
_PARSE_PAT = __import__("re").compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    __import__("re").IGNORECASE | __import__("re").MULTILINE,
)


def _parse_output(raw: str) -> tuple[Optional[str], Optional[bool], Optional[str],
                                      Optional[float], Optional[float], Optional[float]]:
    """抽 (risk, jb_bool, action, risk_conf, jb_conf, action_conf)。
    复读:只取第一段 Safety: 后停止(避免后面自由文本干扰)。"""
    idx = raw.lower().find("safety:")
    if idx < 0:
        return None, None, None, None, None, None
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = _PARSE_PAT.search(seg)
    if not m:
        return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return risk, jb, action, risk_conf, jb_conf, action_conf


def classify_email(adapter, sender: str, subject: str, body: str,
                   received_hours: float = 1.0,
                   has_attachment: bool = False) -> dict:
    """对单封邮件分类。

    adapter = LoadedAdapter 实例(从 get_adapter("email_conf") 取)。
    返回字段:sender / risk / action / jailbreak / risk_conf / action_conf /
            jb_conf / raw / tokens / latency_ms / parse_fail。
    """
    text = _build_text(sender, subject, body, received_hours, has_attachment)
    t0 = time.time()
    res = adapter.classify(text)  # max_new_tokens=40 (B1 修正后)
    dt_ms = int((time.time() - t0) * 1000)
    raw = res["raw"]
    parsed = _parse_output(raw)
    risk, jb, action, rc, jc, ac = parsed
    return {
        "sender": sender,
        "subject": subject,
        "risk": risk,
        "action": action,
        "jailbreak": "yes" if jb else "no",
        "risk_conf": rc,
        "action_conf": ac,
        "jb_conf": jc,
        "raw": raw,
        "tokens": res["tokens"],
        "latency_ms": dt_ms,
        "parse_fail": risk is None,
    }


def _load_emails(args) -> list[dict]:
    """汇总 CLI 三种输入 → list[dict]。"""
    items: list[dict] = []
    if args.sender is not None:
        items.append({
            "sender": args.sender,
            "subject": args.subject or "",
            "body": args.body or "",
            "hours": args.hours,
            "attachment": args.attachment,
        })
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append({
                "sender": d.get("sender", ""),
                "subject": d.get("subject", ""),
                "body": d.get("body", ""),
                "hours": float(d.get("hours", 1.0)),
                "attachment": bool(d.get("attachment", False)),
            })
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append({
                "sender": d.get("sender", ""),
                "subject": d.get("subject", ""),
                "body": d.get("body", ""),
                "hours": float(d.get("hours", 1.0)),
                "attachment": bool(d.get("attachment", False)),
            })
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="email_conf 推理入口")
    ap.add_argument("--sender", help="发件人")
    ap.add_argument("--subject", default="", help="主题")
    ap.add_argument("--body", default="", help="正文(>200 字截断)")
    ap.add_argument("--hours", type=float, default=1.0, help="距今小时数")
    ap.add_argument("--attachment", action="store_true", help="有附件")
    ap.add_argument("--file", help="邮件 JSONL 文件(每行一条)")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 JSONL")
    ap.add_argument("--scenario", default="email_conf",
                    choices=["email", "email_conf"],
                    help="用哪个 adapter(email 不带 confidence,debug 用)")
    cli_args = ap.parse_args()

    items = _load_emails(cli_args)
    if not items:
        print("ERROR: 没传邮件(可用 --sender / --file / --stdin)", file=sys.stderr)
        return 1

    from adapter_registry import get_adapter
    adapter = get_adapter(cli_args.scenario)
    print(f"[1/2] 加载 {len(items)} 封邮件,跑 {cli_args.scenario} ...",
          file=sys.stderr)
    for it in items:
        try:
            r = classify_email(
                adapter,
                it["sender"], it["subject"], it["body"],
                it["hours"], it["attachment"],
            )
            print(json.dumps(r, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({
                "sender": it["sender"], "subject": it["subject"],
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())