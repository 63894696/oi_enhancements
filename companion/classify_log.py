#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_log.py — M3.49 + M3.45.1 日志分类推理入口(2026-09-23)
#
# 目的:
#   - 对单条日志做 5 类风险分级 + 给 action(alert/review/keep/drop)
#   - 取代对 Jev API 的依赖
#   - 输出 Jev 兼容 schema {risk, jailbreak, action, confidence}
#
# 用法(CLI):
#   python classify_log.py --source nginx --level error \
#     --content "2026/09/23 14:23:01 [error] 1234#0: upstream timed out"
#   python classify_log.py --file logs.jsonl      # {"source","level","content"} 每行一条
#   python classify_log.py --stdin < logs.jsonl
#
# 输出(JSON 一行一条):
#   {"source":"nginx","level":"error","risk":"high","action":"review",
#    "risk_conf":0.85,"action_conf":0.91,"raw":"...","latency_ms":2418}
#
# Python 嵌入:
#   from classify_log import classify_log
#   out = classify_log(adapter, source, level, content)
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _build_text(source: str, ts: str, level: str, content: str) -> str:
    """跟 data_prep_log._build_text 一致 — adapter 训练时见的形态。"""
    return (f"日志来源: {source}\n"
            f"时间戳: {ts}\n"
            f"级别: {level}\n"
            f"内容: {content}\n"
            f"问: 这条日志应该如何分类与处理?")


_PARSE_PAT = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_output(raw: str) -> tuple[Optional[str], Optional[bool], Optional[str],
                                      Optional[float], Optional[float], Optional[float]]:
    """抽 (risk, jb_bool, action, risk_conf, jb_conf, action_conf)。
    复读:只取第一段 Safety: 后停止。"""
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


def classify_log(adapter, source: str, level: str, content: str,
                 ts: str = "2026/09/23 14:00:00") -> dict:
    """对单条日志分类。

    adapter = LoadedAdapter 实例(从 get_adapter("log_conf") 取)。
    返回字段:source / level / risk / action / jailbreak / risk_conf / action_conf /
            jb_conf / raw / tokens / latency_ms / parse_fail。
    """
    text = _build_text(source, ts, level, content)
    t0 = time.time()
    res = adapter.classify(text)  # max_new_tokens=40 (B1 修正后)
    dt_ms = int((time.time() - t0) * 1000)
    raw = res["raw"]
    parsed = _parse_output(raw)
    risk, jb, action, rc, jc, ac = parsed
    return {
        "source": source,
        "level": level,
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


def _load_logs(args) -> list[dict]:
    """汇总 CLI 三种输入 → list[dict]。"""
    items: list[dict] = []
    if args.source is not None:
        items.append({
            "source": args.source,
            "ts": args.ts,
            "level": args.level,
            "content": args.content,
        })
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append({
                "source": d.get("source", "unknown"),
                "ts": d.get("ts", "2026/09/23 14:00:00"),
                "level": d.get("level", "info"),
                "content": d.get("content", ""),
            })
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append({
                "source": d.get("source", "unknown"),
                "ts": d.get("ts", "2026/09/23 14:00:00"),
                "level": d.get("level", "info"),
                "content": d.get("content", ""),
            })
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="log_conf 推理入口")
    ap.add_argument("--source", help="日志来源(nginx/postgresql/kernel/python/systemd/docker)")
    ap.add_argument("--level", default="info", help="日志级别")
    ap.add_argument("--content", default="", help="日志内容")
    ap.add_argument("--ts", default="2026/09/23 14:00:00", help="时间戳")
    ap.add_argument("--file", help="日志 JSONL 文件(每行一条)")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 JSONL")
    ap.add_argument("--scenario", default="log_conf",
                    choices=["log", "log_conf"],
                    help="用哪个 adapter(log 不带 confidence,debug 用)")
    cli_args = ap.parse_args()

    items = _load_logs(cli_args)
    if not items:
        print("ERROR: 没传日志(可用 --source / --file / --stdin)", file=sys.stderr)
        return 1

    from adapter_registry import get_adapter
    adapter = get_adapter(cli_args.scenario)
    print(f"[1/2] 加载 {len(items)} 条日志,跑 {cli_args.scenario} ...",
          file=sys.stderr)
    for it in items:
        try:
            r = classify_log(adapter, it["source"], it["level"],
                             it["content"], it["ts"])
            print(json.dumps(r, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({
                "source": it["source"], "level": it["level"],
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())