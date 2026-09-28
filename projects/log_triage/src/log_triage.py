#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# src/log_triage.py — M3.69 log_triage 核心逻辑(2026-09-24)
#
# 功能:
#   - 扫 Windows Event Log(System/Application 或任意 channel)或 .log 文件
#   - 过滤时间窗(--since "24h" / "7d" / "1h")
#   - 调 classify_log(adapter) 拿 5 类分布 + confidence + action
#   - 输出 text / json / markdown 报告
#
# 零侵入:
#   - 不修改 companion/ 任何代码
#   - 只 import:get_adapter(classify_log 内部),psutil/pywin32 在 imports 阶段 try
#
# 失败模式:
#   - 没有 pywin32 → _read_windows_event_log 抛 ImportError,main() 友好降级到 .log 文件
#   - 权限不足 → 捕 PermissionError,降级
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ------------------------------------------------------------
# companion 路径(sibling project,不污染对方)
# ------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent.parent.parent  # oi_enhancements/
_COMPANION = _PROJECT / "companion"
if str(_COMPANION) not in sys.path:
    sys.path.insert(0, str(_COMPANION))

from classify_log import classify_log  # noqa: E402
from adapter_registry import get_adapter  # noqa: E402


# ------------------------------------------------------------
# M3.75 — laya_guard / laya triage 可选 fast-path
# ------------------------------------------------------------
# laya triage_questions 设计的语义是 customer support ticket,
# 不是 log severity 5 类。但它输出的 is_urgent / frustration / intent
# 对"事件是否紧急 / 是否用户明显在抱怨"仍有信号价值。
#
# 设计:
#   1. laya 跑一遍 content(text 字段)
#   2. 翻译层把 laya 答案映成 5 类 urgency(log 语义)
#   3. 与 LoRA 风险并列写入 event dict,**不覆盖**(只作"副观察")
#
# 翻译层(经验阈值):
#   - is_urgent ≥ 0.7 + frustration ≥ 2.0  → critical
#   - is_urgent ≥ 0.5 + frustration ≥ 1.0  → high
#   - is_urgent ≥ 0.4                       → medium
#   - intent = "technical_help" + is_urgent  → +1 档
#   - 其它                                    → safe / low
# ------------------------------------------------------------
_PROJECTS = _PROJECT / "projects"   # oi_enhancements/projects/
_LAYA_GUARD_DIR = _PROJECTS / "laya_guard" / "src"
if _LAYA_GUARD_DIR.exists() and str(_LAYA_GUARD_DIR) not in sys.path:
    sys.path.insert(0, str(_LAYA_GUARD_DIR))

try:
    from laya_guard import guard as _laya_guard_fn  # noqa: E402
    _LAYA_GUARD_OK = True
except Exception:  # noqa: BLE001
    _laya_guard_fn = None
    _LAYA_GUARD_OK = False

# laya Router 单例 cache(M3.75 用 triage_questions)
_TRIAGE_ROUTER = None
_TRIAGE_QS = None
_TRIAGE_LOADED = False


def _ensure_triage_loaded():
    """懒加载 laya Router + triage_questions preset。"""
    global _TRIAGE_ROUTER, _TRIAGE_QS, _TRIAGE_LOADED
    if _TRIAGE_LOADED:
        return _TRIAGE_ROUTER, _TRIAGE_QS
    try:
        from laya import Router, triage_questions  # noqa: F401
    except ImportError as e:
        print(f"[log_triage] laya import 失败: {e}", file=sys.stderr)
        _TRIAGE_LOADED = True
        return None, None
    try:
        _TRIAGE_ROUTER = Router(default="multilingual", preload=False)
        _TRIAGE_QS = triage_questions()
    except Exception as e:  # noqa: BLE001
        print(f"[log_triage] laya triage 加载失败: {e}", file=sys.stderr)
        _TRIAGE_ROUTER = None
        _TRIAGE_QS = None
    _TRIAGE_LOADED = True
    return _TRIAGE_ROUTER, _TRIAGE_QS


def _map_triage_to_severity(answers: dict) -> str:
    """把 laya triage_questions() 答案映射成 5 类 log severity。

    Args:
        answers: laya router.predict 返回的 answers dict

    Returns:
        "safe" / "low" / "medium" / "high" / "critical"
    """
    is_urgent = answers.get("is_urgent", {}).get("noul", 0.0) or 0.0
    frustration = answers.get("frustration", {}).get("score", 0.0) or 0.0
    intent = answers.get("intent", {}).get("choice", "")
    intent_prob = answers.get("intent", {}).get("answer_confidence", 0.0) or 0.0

    # critical: urgency + frustration 都极高
    if is_urgent >= 0.7 and frustration >= 2.0:
        return "critical"
    # high: 单维度很高
    if is_urgent >= 0.5 and frustration >= 1.0:
        return "high"
    # medium: 有点急
    if is_urgent >= 0.4:
        return "medium"
    # medium+: technical_help 且 urgent 抬一档
    if intent == "technical_help" and intent_prob >= 0.7 and is_urgent >= 0.2:
        return "medium"
    if intent == "cancellation" and intent_prob >= 0.5:
        return "medium"
    # low / safe
    if is_urgent >= 0.15 or frustration >= 0.5:
        return "low"
    return "safe"


def _laya_triage_event(content: str, no_laya: bool = False) -> dict:
    """laya triage_questions 跑单条 log event。

    Args:
        content: 事件的 content / desc 字段
        no_laya: True 则跳过 laya 推理(完全 fallback 到 LoRA)

    Returns:
        dict 含 9 字段:
          - ok: bool                (laya 成功推理)
          - available: bool         (laya 模块是否加载)
          - risk: 5 类              (laya 翻译的 5 类 severity)
          - is_urgent: 0-1          (laya 原始)
          - frustration: 0-3        (laya 原始)
          - intent: choice          (laya 原始)
          - intent_prob: 0-1        (laya intent 置信)
          - latency_ms
          - error: str | None
    """
    if no_laya or not _LAYA_GUARD_OK:
        return {
            "ok": False,
            "available": _LAYA_GUARD_OK,
            "risk": None,
            "is_urgent": None,
            "frustration": None,
            "intent": None,
            "intent_prob": None,
            "latency_ms": 0,
            "error": "no_laya" if no_laya else "laya_guard not loaded",
        }
    router, qs = _ensure_triage_loaded()
    if router is None or qs is None:
        return {
            "ok": False,
            "available": False,
            "risk": None,
            "is_urgent": None,
            "frustration": None,
            "intent": None,
            "intent_prob": None,
            "latency_ms": 0,
            "error": "triage_questions not loaded",
        }
    t0 = time.time()
    try:
        # triage_questions 的 placeholder key 是 "message"
        state = {"message": content[:800]}  # 截断,免 prompt 爆长
        res = router.predict(state, qs)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers", {})
        if not answers:
            return {
                "ok": False,
                "available": True,
                "risk": None,
                "is_urgent": None,
                "frustration": None,
                "intent": None,
                "intent_prob": None,
                "latency_ms": round(elapsed_ms, 1),
                "error": "empty answers",
            }
        is_urgent = answers.get("is_urgent", {}).get("noul", 0.0) or 0.0
        frustration = answers.get("frustration", {}).get("score", 0.0) or 0.0
        intent = answers.get("intent", {}).get("choice", None)
        intent_prob = answers.get("intent", {}).get("answer_confidence", 0.0) or 0.0
        risk = _map_triage_to_severity(answers)
        return {
            "ok": True,
            "available": True,
            "risk": risk,
            "is_urgent": round(is_urgent, 4),
            "frustration": round(frustration, 4),
            "intent": intent,
            "intent_prob": round(intent_prob, 4),
            "latency_ms": round(elapsed_ms, 1),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000
        return {
            "ok": False,
            "available": True,
            "risk": None,
            "is_urgent": None,
            "frustration": None,
            "intent": None,
            "intent_prob": None,
            "latency_ms": round(elapsed_ms, 1),
            "error": f"{type(e).__name__}: {e}",
        }


# ------------------------------------------------------------
# 严重度档位(order matters — for --min-severity filtering)
# ------------------------------------------------------------
SEVERITY_ORDER = ["safe", "low", "medium", "high", "critical"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Action 推荐(给 text/MD 报告里的小图标用)
_ACTION_ICONS = {
    "alert": "🔴🔴🔴",
    "review": "🔴",
    "keep": "⚠️",
    "drop": "",
}
_SEVERITY_ICONS = {
    "critical": "🔴🔴🔴",
    "high": "🔴",
    "medium": "⚠️",
    "low": "",
    "safe": "",
}


# ============================================================
# 时间窗解析
# ============================================================
_SINCE_RE = re.compile(r"^(\d+)\s*([hdw])$", re.IGNORECASE)


def parse_since(since: str, now: Optional[dt.datetime] = None) -> dt.datetime:
    """解析 "24h" / "7d" / "1w" → 起始时间(UTC, naive)。

    "24h" 表示过去 24 小时,即 now - 24h。
    """
    now = now or dt.datetime.now()
    m = _SINCE_RE.match(since.strip())
    if not m:
        raise ValueError(
            f"--since 格式不对: {since!r}  (期望 '24h' / '7d' / '1w')")
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "h":
        delta = dt.timedelta(hours=n)
    elif unit == "d":
        delta = dt.timedelta(days=n)
    elif unit == "w":
        delta = dt.timedelta(weeks=n)
    else:
        raise ValueError(f"不支持的单位: {unit!r}")
    return now - delta


# ============================================================
# 源 1: Windows Event Log
# ============================================================
def _windows_eventlog_available() -> bool:
    """探测 pywin32 / win32evtlog 是否可用(不抛异常)。"""
    try:
        import win32evtlog  # noqa: F401
        return True
    except ImportError:
        return False


def _read_windows_event_log(channel: str, since: dt.datetime,
                            max_events: int = 5000) -> list[dict]:
    """读 Windows Event Log 某 channel 的事件。

    返回 list of dict:{source, ts, level, content, record_id}
    level 取自 EventLog Type(Information/Warning/Error/Critical/Success Audit)

    异常:
        ImportError — 缺 pywin32
        PermissionError — 权限不足
        OSError — 其他 Windows API 失败
    """
    import win32evtlog
    import win32evtlogutil  # noqa: F401  部分版本需要
    import win32con

    # 试图打开 channel(可能 PermissionError)
    hand = win32evtlog.OpenEventLog(None, channel)

    flags = (win32evtlog.EVENTLOG_BACKWARDS_READ
             | win32evtlog.EVENTLOG_SEQUENTIAL_READ)
    total = 0
    out: list[dict] = []
    try:
        while total < max_events:
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            if not events:
                break
            for ev in events:
                total += 1
                # 时间过滤(ev.TimeGenerated 是 pywintypes.datetime / tuple,
                # 在不同 pywin32 版本里两种都见过)
                tg = ev.TimeGenerated
                if hasattr(tg, "year"):  # pywintypes.datetime → 转原生
                    ev_time = dt.datetime(
                        tg.year, tg.month, tg.day,
                        tg.hour, tg.minute, tg.second,
                    )
                else:
                    ev_time = dt.datetime(*tg[:6])
                if ev_time < since:
                    return out  # 列表是倒序,遇到早于 since 就直接停
                # 等级(EventLog Type 数字 → 文字)
                t = ev.EventType
                if t == win32con.EVENTLOG_ERROR_TYPE:
                    level = "error"
                elif t == win32con.EVENTLOG_WARNING_TYPE:
                    level = "warning"
                elif t == win32con.EVENTLOG_INFORMATION_TYPE:
                    level = "info"
                elif t == win32con.EVENTLOG_AUDIT_SUCCESS:
                    level = "audit_success"
                elif t == win32con.EVENTLOG_AUDIT_FAILURE:
                    level = "audit_failure"
                else:
                    level = "unknown"

                # 内容
                try:
                    content = " | ".join(str(s) for s in ev.StringInserts or [])
                except Exception:
                    content = f"(event {ev.RecordNumber})"
                if not content:
                    content = f"(event {ev.RecordNumber}, no inserts)"

                cat = ev.EventCategory
                src = ev.SourceName
                desc = f"EventID={ev.EventID} Category={cat} " + content

                out.append({
                    "source": src,
                    "ts": ev_time.strftime("%Y/%m/%d %H:%M:%S"),
                    "level": level,
                    "content": desc[:600],  # 截断,免 prompt 爆长
                    "record_id": int(ev.RecordNumber),
                    "channel": channel,
                    "event_id": int(ev.EventID & 0xFFFF),
                })
                if len(out) >= max_events:
                    return out
    finally:
        try:
            win32evtlog.CloseEventLog(hand)
        except Exception:
            pass
    return out


# ============================================================
# 源 2: .log 文件
# ============================================================
# 通用 .log 行格式尝试:优先 JSON,再 <ts> <level> <msg>,再否则纯文本
_TS_PATTERNS = [
    # 2026/09/24 12:34:56
    re.compile(r"^(\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)"),
    # 24/Sep/2026:12:34:56
    re.compile(r"^(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2})"),
]
_LEVEL_PATTERNS = [
    re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL|CRIT|EMERG(?:ENCY)?)\b"),
]


def _parse_line(line: str) -> dict:
    """把一行 .log 文本解析成 {ts, level, content}。失败用整行当 content。"""
    line = line.rstrip("\r\n")
    ts = "unknown"
    level = "info"
    content = line

    # 先试 JSON
    s = line.strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            d = json.loads(s)
            return {
                "ts": d.get("ts", d.get("timestamp", d.get("time", "unknown"))),
                "level": d.get("level", d.get("severity", "info")),
                "content": d.get("content", d.get("msg", d.get("message", ""))),
            }
        except Exception:
            pass

    # 再试正则
    for pat in _TS_PATTERNS:
        m = pat.search(line)
        if m:
            ts = m.group(1).replace(",", ".").replace("T", " ")
            break
    for pat in _LEVEL_PATTERNS:
        m = pat.search(line)
        if m:
            level = m.group(1).lower()
            if level in ("err",):
                level = "error"
            elif level in ("warn", "warning"):
                level = "warning"
            elif level in ("crit", "critical"):
                level = "critical"
            elif level in ("emerg", "emergency"):
                level = "emergency"
            break
    return {"ts": ts, "level": level, "content": content}


def _read_log_file(path: Path, since: dt.datetime,
                   max_events: int = 5000) -> list[dict]:
    """读 .log 文件,按 mtime 兜底过滤。

    不会精确按行内 ts 过滤(只按文件 mtime)— 适合 24h 大文件兜底。
    """
    if not path.exists():
        raise FileNotFoundError(f"日志文件不存在: {path}")
    # mtime 过滤
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
    if mtime < since:
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            parsed = _parse_line(line)
            out.append({
                "source": path.name,
                "ts": parsed["ts"],
                "level": parsed["level"],
                "content": parsed["content"][:600],
            })
            if len(out) >= max_events:
                break
    return out


# ============================================================
# 分类(联合 log + log_conf)
# ============================================================
def _classify_events(events: list[dict], spec: str = "log_conf",
                     no_laya: bool = False) -> list[dict]:
    """对每条 event 跑 classify_log,附加 risk/action/conf 字段。

    M3.75 — 同时跑 laya triage_questions(),附加 laya_result 字段
            (不覆盖 LoRA 风险,只作"副观察";2 套并存供下游用)
    """
    adapter = get_adapter(spec)
    out = []
    for ev in events:
        try:
            res = classify_log(
                adapter,
                source=ev["source"],
                level=ev["level"],
                content=ev["content"],
                ts=ev["ts"],
            )
        except Exception as e:  # noqa: BLE001
            res = {
                "source": ev["source"],
                "level": ev["level"],
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
        # M3.75 — laya triage fast-path(在 LoRA 后跑,只作副观察)
        laya_res = _laya_triage_event(ev["content"], no_laya=no_laya)
        # 合并原 event 字段 + classify 结果 + laya_result
        merged = {**ev, **res, "laya_result": laya_res}
        out.append(merged)
    return out


# ============================================================
# 5 类分布统计
# ============================================================
def _distribution(classified: list[dict]) -> dict[str, int]:
    """统计 5 类分布,缺 risk 的算 parse_fail。"""
    dist = {s: 0 for s in SEVERITY_ORDER}
    parse_fail = 0
    for ev in classified:
        risk = (ev.get("risk") or "").lower().strip()
        if risk in dist:
            dist[risk] += 1
        else:
            parse_fail += 1
    return {"dist": dist, "parse_fail": parse_fail, "total": len(classified)}


# ============================================================
# 排序 + 过滤
# ============================================================
def _ts_key(ev: dict) -> str:
    """按时间排序(已知格式 "YYYY/MM/DD HH:MM:SS")。"""
    return ev.get("ts", "") or ""


def _filter_min_severity(classified: list[dict],
                         min_severity: str) -> list[dict]:
    """只保留 ≥ min_severity 的事件(按风险等级)。"""
    min_rank = SEVERITY_RANK.get(min_severity, 0)
    out = []
    for ev in classified:
        risk = (ev.get("risk") or "").lower().strip()
        if risk not in SEVERITY_RANK:
            continue  # parse_fail 不显示
        if SEVERITY_RANK[risk] >= min_rank:
            out.append(ev)
    return out


# ============================================================
# 输出格式
# ============================================================
def _format_text(classified: list[dict], dist: dict,
                 spec: str, since: dt.datetime,
                 show: list[dict], min_sev: str) -> str:
    """text 输出(给人看的 CLI 报告)。"""
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"[log_triage] spec={spec} since={since_str}",
        f"[log_triage] 总事件: {dist['total']}  parse_fail: {dist['parse_fail']}",
        "",
        "5 类分布:",
    ]
    for sev in reversed(SEVERITY_ORDER):  # critical 先
        n = dist["dist"][sev]
        pct = (n * 100 / dist["total"]) if dist["total"] else 0
        note = {
            "critical": "🔴🔴🔴 已上报 incident",
            "high": "🔴 已触发 watchdog",
            "medium": "⚠️ 推送 perf_guard 告警",
            "low": "入库",
            "safe": "丢弃",
        }[sev]
        lines.append(f"  {sev:9s} {n:5d} ({pct:5.1f}%)  {note}")

    lines.append("")
    lines.append(f"待办(≥ {min_sev},按时间倒序,最多 {len(show)} 条):")
    lines.append("")
    for ev in show:
        sev = (ev.get("risk") or "?").lower()
        sev_icon = _SEVERITY_ICONS.get(sev, "?")
        ts = ev.get("ts", "?")
        # 仅取 HH:MM
        if " " in ts:
            ts_short = ts.split(" ", 1)[1][:5]
        else:
            ts_short = ts[:5]
        src = ev.get("source", "?")
        content = ev.get("content", "")[:80]
        action = ev.get("action", "")
        action_str = f" → {action}" if action else ""
        lines.append(
            f"  [{ts_short}] {sev:9s} {sev_icon} "
            f"{src}: {content}{action_str}")
    return "\n".join(lines)


def _format_json(classified: list[dict], dist: dict,
                 spec: str, since: dt.datetime,
                 show: list[dict], min_sev: str) -> str:
    return json.dumps({
        "spec": spec,
        "since": since.isoformat(sep=" "),
        "total": dist["total"],
        "parse_fail": dist["parse_fail"],
        "distribution": dist["dist"],
        "min_severity": min_sev,
        "events": show,
    }, ensure_ascii=False, indent=2)


def _format_markdown(classified: list[dict], dist: dict,
                     spec: str, since: dt.datetime,
                     show: list[dict], min_sev: str) -> str:
    """Markdown 报告(给 weekly report 之类用)。"""
    today = dt.date.today().isoformat()
    lines = [
        f"# Log Triage Report — {today}",
        "",
        f"**扫描时间窗**: {since.strftime('%Y-%m-%d %H:%M:%S')} → "
        f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**总事件**: {dist['total']} 条  **parse_fail**: {dist['parse_fail']} 条",
        f"**spec**: {spec}",
        "",
        "## 5 类分布",
        "",
        "| 等级 | 事件数 | 占比 | 动作 |",
        "|------|--------|------|------|",
    ]
    actions = {
        "critical": "🔴🔴🔴 上报 incident",
        "high": "🔴 触发 watchdog",
        "medium": "⚠️ 推送 perf_guard",
        "low": "入库",
        "safe": "丢弃",
    }
    for sev in reversed(SEVERITY_ORDER):
        n = dist["dist"][sev]
        pct = (n * 100 / dist["total"]) if dist["total"] else 0
        lines.append(f"| {sev} | {n} | {pct:.1f}% | {actions[sev]} |")
    lines.append("")
    lines.append(f"## 待办事件(≥ {min_sev},按时间倒序)")
    lines.append("")
    lines.append("| 时间 | 等级 | 来源 | 内容 | action |")
    lines.append("|------|------|------|------|--------|")
    for ev in show:
        sev = (ev.get("risk") or "?").lower()
        ts = ev.get("ts", "?")
        if " " in ts:
            ts_short = ts.split(" ", 1)[1][:8]
        else:
            ts_short = ts[:8]
        src = ev.get("source", "?")
        content = (ev.get("content", "") or "")[:80].replace("|", "\\|")
        action = ev.get("action", "") or "-"
        lines.append(f"| {ts_short} | {sev} | {src} | {content} | {action} |")
    lines.append("")
    lines.append("---")
    lines.append(f"_生成时间: {dt.datetime.now().isoformat(sep=' ', timespec='seconds')}_")
    return "\n".join(lines)