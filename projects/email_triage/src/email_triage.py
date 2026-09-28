#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# email_triage.py — M3.69 邮件紧急度筛核心库(2026-09-24)
#
# 目的:
#   - 从 .eml 目录或 IMAP 拉邮件 → 用 companion 上 email_conf adapter 联合判 5 类风险
#   - 输出可读文本 / JSON / Markdown 三种格式
#   - 零侵入 companion/(只 import,不修改)
#
# 设计:
#   - fetch_emails(source) → list[email_dict]
#   - classify_emails(emails, spec="email_conf") → 同 list 加 risk/action/conf
#   - format_text/md/json(emails, top_n=None) → string
#   - 联合 email + email_conf:两边都跑,取更高风险 + 各自 conf
from __future__ import annotations

import email
import email.policy
import imaplib
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

# ------------------------------------------------------------
# 路径:把 companion/ 加到 sys.path 以便 import adapter_registry / classify_email
# ------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_COMPANION_DIR = (
    Path("C:/Users/Administrator/oi_enhancements/companion")
    if Path("C:/Users/Administrator/oi_enhancements/companion").exists()
    else _PROJECT_ROOT.parent / "companion"
)
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))


# ------------------------------------------------------------
# 5 类风险等级排序(数值越大越紧急)
# ------------------------------------------------------------
_RISK_ORDER = {
    "safe": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
    None: -1,  # parse_fail 时
}
_RISK_EMOJI = {
    "critical": "[CRITICAL]",
    "high": "[HIGH]",
    "medium": "[MEDIUM]",
    "low": "[LOW]",
    "safe": "[SAFE]",
    None: "[???]",
}


# ------------------------------------------------------------
# 时间窗解析
# ------------------------------------------------------------
_SINCE_RE = re.compile(r"^\s*(\d+)\s*([hd])\s*$", re.IGNORECASE)


def parse_since(since: str) -> float:
    """'24h' / '7d' / '30m' → 小时数(浮点)。

    支持单位:h(小时)/ d(天)/ m(分钟)。无法解析抛 ValueError。
    """
    if since is None:
        return 24.0  # 默认 24h
    m = _SINCE_RE.match(str(since))
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "h":
            return float(n)
        if unit == "d":
            return float(n * 24)
    # 兼容 '30m'
    m2 = re.match(r"^\s*(\d+)\s*m\s*$", str(since), re.IGNORECASE)
    if m2:
        return float(int(m2.group(1))) / 60.0
    raise ValueError(
        f"--since 格式无法解析: {since!r} (示例: 24h / 7d / 30m)")


# ------------------------------------------------------------
# EML 解析
# ------------------------------------------------------------
def _decode_header_value(value: str) -> str:
    """简化版 email.header 解码,处理 =?utf-8?b?...?= / =?gb2312?...?= 等。"""
    if not value:
        return ""
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _msg_get_address(msg: Message, header: str) -> str:
    v = msg.get(header, "")
    if not v:
        return ""
    # 取 <...> 之前或整个串
    m = re.search(r"<([^>]+)>", v)
    if m:
        return m.group(1).strip()
    return v.strip()


def _msg_get_body(msg: Message) -> str:
    """取 plain text 正文(优先 text/plain,fallback text/html 去标签)。"""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    return part.get_content()
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_content()
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    pass
        return ""
    try:
        return msg.get_content()
    except Exception:
        return ""


def _msg_get_date(msg: Message) -> Optional[datetime]:
    s = msg.get("Date", "")
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def parse_eml_file(path: Path) -> dict:
    """读一个 .eml 文件 → {sender, subject, body, date, has_attachment, path}。

    解析失败抛 ValueError,上层捕获做降级。
    """
    raw = Path(path).read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = _msg_get_address(msg, "From") or "(unknown)"
    subject = _decode_header_value(msg.get("Subject", ""))
    body = _msg_get_body(msg) or ""
    date = _msg_get_date(msg)
    has_attachment = any(
        part.get_filename() for part in msg.walk() if not part.is_multipart())
    return {
        "sender": sender,
        "subject": subject,
        "body": body[:500],  # 截断,跟 data_prep_email 训练时一致
        "date": date,
        "has_attachment": has_attachment,
        "path": str(path),
        "source": "eml",
    }


def fetch_eml_dir(eml_dir: Path, since_hours: float) -> list[dict]:
    """递归扫 .eml 文件,按 Date 头过滤 since。

    无 Date 头时默认保留(避免漏掉不可解析的邮件)。
    """
    eml_dir = Path(eml_dir)
    if not eml_dir.exists():
        raise FileNotFoundError(f"eml 目录不存在: {eml_dir}")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    results: list[dict] = []
    errors: list[tuple[str, str]] = []
    for path in sorted(eml_dir.rglob("*.eml")):
        try:
            d = parse_eml_file(path)
            d["error"] = None
            # 时间过滤:有 Date 才过滤
            if d["date"] is not None and d["date"] < cutoff:
                continue
            results.append(d)
        except Exception as e:  # noqa: BLE001
            errors.append((str(path), f"{type(e).__name__}: {e}"))
            # 仍然加一条 error 占位,前端能看到
            results.append({
                "sender": "(parse error)",
                "subject": path.name,
                "body": "",
                "date": None,
                "has_attachment": False,
                "path": str(path),
                "source": "eml",
                "error": f"{type(e).__name__}: {e}",
            })
    return results, errors


# ------------------------------------------------------------
# IMAP 拉取
# ------------------------------------------------------------
def fetch_imap(server: str, user: str, password: str,
               since_hours: float,
               folder: str = "INBOX",
               port: int = 993,
               timeout: int = 30) -> tuple[list[dict], list[tuple[str, str]]]:
    """SSL IMAP 拉取 SINCE 邮件 → list[email_dict]。

    返 (results, errors)。失败抛 RuntimeError。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    sincedate = cutoff.strftime("%d-%b-%Y")

    results: list[dict] = []
    errors: list[tuple[str, str]] = []

    try:
        M = imaplib.IMAP4_SSL(server, port=port, timeout=timeout)
        M.login(user, password)
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
        raise RuntimeError(f"IMAP 认证失败 {server}: {e}") from e
    except socket.error as e:
        raise RuntimeError(f"IMAP 连接失败 {server}: {e}") from e

    try:
        typ, _ = M.select(folder)
        if typ != "OK":
            raise RuntimeError(f"IMAP 选 mailbox 失败: {folder}")

        typ, data = M.search(None, f'SINCE {sincedate}')
        if typ != "OK":
            raise RuntimeError(f"IMAP SINCE 搜索失败")

        msg_nums = data[0].split() if data and data[0] else []
        for num in msg_nums:
            try:
                typ, msg_data = M.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    errors.append((num.decode(), "fetch 返回空"))
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                sender = _msg_get_address(msg, "From") or "(unknown)"
                subject = _decode_header_value(msg.get("Subject", ""))
                body = _msg_get_body(msg) or ""
                date = _msg_get_date(msg)
                has_attachment = any(
                    part.get_filename() for part in msg.walk()
                    if not part.is_multipart())
                results.append({
                    "sender": sender,
                    "subject": subject,
                    "body": body[:500],
                    "date": date,
                    "has_attachment": has_attachment,
                    "uid": num.decode(errors="replace"),
                    "source": "imap",
                    "error": None,
                })
            except Exception as e:  # noqa: BLE001
                errors.append((str(num), f"{type(e).__name__}: {e}"))
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass

    return results, errors


# ------------------------------------------------------------
# 分类推理(联合 email + email_conf)
# ------------------------------------------------------------
def _classify_one(adapter, sender: str, subject: str, body: str,
                  received_hours: float, has_attachment: bool) -> dict:
    """调 companion/classify_email.classify_email 拿一次分类结果。

    出错时返 risk=None / parse_fail=True 的占位 dict,不抛。
    """
    from classify_email import classify_email
    try:
        return classify_email(
            adapter, sender, subject, body,
            received_hours=received_hours,
            has_attachment=has_attachment,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "sender": sender,
            "subject": subject,
            "risk": None,
            "action": None,
            "jailbreak": "no",
            "risk_conf": None,
            "action_conf": None,
            "jb_conf": None,
            "raw": "",
            "tokens": 0,
            "latency_ms": 0,
            "parse_fail": True,
            "error": f"{type(e).__name__}: {e}",
        }


def classify_emails(emails: list[dict],
                    spec_name: str = "email_conf") -> list[dict]:
    """对 list[email_dict] 跑 adapter 联合判,带时间元数据。

    每个 email 加 risk / action / risk_conf / action_conf / latency_ms /
    parse_fail 字段。received_hours 从 date 字段推算。
    """
    from adapter_registry import get_adapter
    adapter = get_adapter(spec_name)

    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for em in emails:
        # 计算 received_hours
        if em.get("date") is not None:
            try:
                dt = em["date"]
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                received_hours = max(
                    0.0, (now - dt).total_seconds() / 3600.0)
            except Exception:
                received_hours = 1.0
        else:
            received_hours = 1.0

        r = _classify_one(
            adapter,
            em.get("sender", ""),
            em.get("subject", ""),
            em.get("body", ""),
            received_hours,
            em.get("has_attachment", False),
        )
        # 合并元数据
        out.append({**em, **r})
    return out


def max_risk(emails: list[dict]) -> str:
    """返 emails 里最高风险等级字符串。"""
    best = "safe"
    for em in emails:
        r = em.get("risk")
        if _RISK_ORDER.get(r, -1) > _RISK_ORDER.get(best, -1):
            best = r or best
    return best


def filter_top(emails: list[dict], top_n: Optional[int],
               levels: tuple[str, ...] = ("critical", "high")) -> list[dict]:
    """取 top_n 个 critical+high(按时间倒序)。

    top_n=None 时返全部 critical+high(不截断)。
    """
    flagged = [e for e in emails if e.get("risk") in levels]
    flagged.sort(
        key=lambda e: e.get("date") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if top_n is None:
        return flagged
    return flagged[:top_n]


# ------------------------------------------------------------
# 输出格式化
# ------------------------------------------------------------
def _fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "??:??"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%m-%d %H:%M")
    except Exception:
        return dt.strftime("%m-%d %H:%M")


def _risk_dist(emails: list[dict]) -> dict[str, int]:
    dist = {"safe": 0, "low": 0, "medium": 0, "high": 0, "critical": 0,
            "parse_fail": 0}
    for em in emails:
        r = em.get("risk")
        if r is None:
            dist["parse_fail"] += 1
        elif r in dist:
            dist[r] += 1
        else:
            dist["parse_fail"] += 1
    return dist


def format_text(emails: list[dict],
                top_n: Optional[int] = None,
                since_label: str = "最近 24h") -> str:
    """text 输出:分布 + critical+high 待办清单。"""
    dist = _risk_dist(emails)
    total = sum(dist.values())
    lines: list[str] = []
    lines.append(f"[email_conf] 扫 {since_label} 邮件: {total} 封")
    lines.append("")
    lines.append("  5 类分布:")
    for lvl in ("safe", "low", "medium", "high", "critical"):
        n = dist[lvl]
        pct = (n / total * 100) if total else 0.0
        flag = ""
        if lvl == "high":
            flag = "   WARNING 立刻看"
        elif lvl == "critical":
            flag = "   CRITICAL 银行验证码"
        lines.append(f"    {lvl:<10} {n:>3} ({pct:5.1f}%){flag}")
    if dist["parse_fail"] > 0:
        lines.append(f"    parse_fail {dist['parse_fail']:>3}")
    lines.append("")

    flagged = filter_top(emails, top_n)
    if not flagged:
        lines.append("  待办:无 critical/high 邮件 :)")
        return "\n".join(lines)

    lines.append(f"  待办(critical + high,按时间倒序):")
    lines.append("")
    for em in flagged:
        emoji = _RISK_EMOJI.get(em.get("risk"), "[???]")
        time_str = _fmt_time(em.get("date"))
        sender = em.get("sender", "")
        subject = em.get("subject", "")
        lines.append(f"  {emoji} [{time_str}] {em.get('risk', '?'):<8} "
                     f"{sender} <{_extract_email(sender)}>")
        if subject:
            lines.append(f"                          Subject: {subject}")
        if em.get("error"):
            lines.append(f"                          (error: {em['error']})")
    return "\n".join(lines)


def _extract_email(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    if m:
        return m.group(1)
    if "@" in sender:
        return sender.strip()
    return sender


def format_json(emails: list[dict],
                top_n: Optional[int] = None,
                since_label: str = "最近 24h") -> str:
    """JSON 输出(全量或 top_n critical+high)。"""
    flagged = filter_top(emails, top_n)
    dist = _risk_dist(emails)
    out = {
        "since_label": since_label,
        "total": sum(dist.values()),
        "distribution": dist,
        "top": [
            {
                "sender": e.get("sender"),
                "subject": e.get("subject"),
                "date": e["date"].isoformat() if e.get("date") else None,
                "risk": e.get("risk"),
                "action": e.get("action"),
                "risk_conf": e.get("risk_conf"),
                "action_conf": e.get("action_conf"),
                "jailbreak": e.get("jailbreak"),
                "has_attachment": e.get("has_attachment"),
                "source": e.get("source"),
                "error": e.get("error"),
            }
            for e in flagged
        ],
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def format_md(emails: list[dict],
              top_n: Optional[int] = None,
              since_label: str = "最近 24h") -> str:
    """Markdown 输出:表格 + 分布。"""
    dist = _risk_dist(emails)
    total = sum(dist.values())
    flagged = filter_top(emails, top_n)

    lines: list[str] = []
    lines.append(f"# email_triage 报告 ({since_label})")
    lines.append("")
    lines.append(f"共 {total} 封邮件")
    lines.append("")
    lines.append("## 5 类风险分布")
    lines.append("")
    lines.append("| 等级 | 数量 | 占比 |")
    lines.append("|------|------|------|")
    for lvl in ("critical", "high", "medium", "low", "safe"):
        n = dist[lvl]
        pct = (n / total * 100) if total else 0.0
        lines.append(f"| {lvl} | {n} | {pct:.1f}% |")
    if dist["parse_fail"] > 0:
        lines.append(f"| parse_fail | {dist['parse_fail']} | - |")
    lines.append("")
    lines.append("## 待办 (critical + high)")
    lines.append("")
    if not flagged:
        lines.append("_无 critical / high 邮件 :)_")
        return "\n".join(lines)
    lines.append("| 时间 | 风险 | 发件人 | 主题 | 动作 |")
    lines.append("|------|------|--------|------|------|")
    for em in flagged:
        time_str = _fmt_time(em.get("date"))
        risk = em.get("risk") or "?"
        sender = em.get("sender", "") or ""
        subject = em.get("subject", "") or ""
        action = em.get("action") or "-"
        # markdown 转义
        sender = sender.replace("|", "\\|")
        subject = subject.replace("|", "\\|")
        lines.append(f"| {time_str} | {risk} | {sender} | {subject} | {action} |")
    return "\n".join(lines)


# ------------------------------------------------------------
# 高层入口(给 main.py 用)
# ------------------------------------------------------------
def run(source: dict, top_n: Optional[int] = 20,
        since_label: str = "最近 24h",
        spec_name: str = "email_conf",
        fmt: str = "text") -> tuple[str, dict]:
    """统一入口:source = {"eml_dir": Path} 或 {"imap": {...}}。

    返 (formatted_text, stats_dict)。
    """
    t0 = time.time()

    if "eml_dir" in source:
        since_hours = parse_since(source.get("since", "24h"))
        emails, errors = fetch_eml_dir(Path(source["eml_dir"]), since_hours)
        since_label = source.get("since", "24h")
    elif "imap" in source:
        imap_cfg = source["imap"]
        pwd = os.environ.get(imap_cfg["password_env"], "")
        if not pwd:
            raise RuntimeError(
                f"环境变量 {imap_cfg['password_env']!r} 未设,"
                f"无法 IMAP 认证")
        since_hours = parse_since(imap_cfg.get("since", "24h"))
        emails, errors = fetch_imap(
            imap_cfg["server"], imap_cfg["user"], pwd,
            since_hours,
            folder=imap_cfg.get("folder", "INBOX"),
            port=imap_cfg.get("port", 993),
            timeout=imap_cfg.get("timeout", 30),
        )
        since_label = imap_cfg.get("since", "24h")
    else:
        raise ValueError("source 必须含 eml_dir 或 imap")

    if errors:
        print(f"[warn] {len(errors)} 条解析失败", file=sys.stderr)

    classified = classify_emails(emails, spec_name=spec_name)

    if fmt == "json":
        out = format_json(classified, top_n=top_n, since_label=since_label)
    elif fmt == "md":
        out = format_md(classified, top_n=top_n, since_label=since_label)
    else:
        out = format_text(classified, top_n=top_n, since_label=since_label)

    dt = time.time() - t0
    stats = {
        "total": len(emails),
        "errors": len(errors),
        "elapsed_sec": round(dt, 2),
        "spec": spec_name,
        "max_risk": max_risk(classified),
        "distribution": _risk_dist(classified),
    }
    return out, stats