#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 log_triage CLI 入口(2026-09-24)
#
# 用法:
#   python main.py                                 # 默认扫最近 24h System+Application
#   python main.py --channel "System" --since "7d"
#   python main.py --file "D:/logs/app.log" --format md --output report.md
#   python main.py --file app.log --spec log_conf --min-severity high
#   python main.py --top 20
#
# 设计:
#   - 离线跑(只用本地 LoRA adapter,无外部 API)
#   - Windows Event Log 没装 pywin32 自动降级到 .log 模式(友好报错)
#   - 不会修改 companion/ 任何代码
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from log_triage import (  # noqa: E402
    parse_since,
    _windows_eventlog_available,
    _read_windows_event_log,
    _read_log_file,
    _classify_events,
    _distribution,
    _filter_min_severity,
    _format_text,
    _format_json,
    _format_markdown,
    SEVERITY_ORDER,
)


def _read_events(args, since: dt.datetime) -> list[dict]:
    """根据 args 选源,读事件。失败抛 RuntimeError 由 main 捕获。"""
    if args.file:
        return _read_log_file(Path(args.file), since)
    # 默认 Event Log
    if not _windows_eventlog_available():
        raise RuntimeError(
            "Windows Event Log 不可用(本机缺 pywin32)。"
            "  → pip install pywin32  或用 --file 读 .log 文件"
        )
    events: list[dict] = []
    for ch in args.channel:
        try:
            ch_events = _read_windows_event_log(ch, since)
        except PermissionError as e:
            print(f"[warn] channel={ch} 权限不足,跳过: {e}", file=sys.stderr)
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[warn] channel={ch} 读取失败: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        events.extend(ch_events)
    return events


def _sort_and_trim(classified: list[dict], top: int) -> list[dict]:
    """按时间倒序,只留 top 条(默认 0 = 全部)。"""
    sorted_evs = sorted(classified, key=lambda e: e.get("ts", ""), reverse=True)
    if top and len(sorted_evs) > top:
        return sorted_evs[:top]
    return sorted_evs


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="log_triage",
        description="M3.69 log_triage — Windows Event Log / .log 文件 24h triager "
                    "(本地 LoRA 推理,离线可用)",
    )
    ap.add_argument("--channel", action="append", default=None,
                    help="Windows Event Log channel(如 System / Application)。"
                         " 可重复传。默认 = [System, Application]")
    ap.add_argument("--file", help=".log 文件路径(任意 .log 文件,按 mtime 过滤)")
    ap.add_argument("--since", default="24h",
                    help='时间窗, "24h" / "7d" / "1w" (默认 24h)')
    ap.add_argument("--spec", default="log_conf",
                    choices=["log", "log_conf"],
                    help="用哪个 adapter(log 不带 conf,debug 用;默认 log_conf)")
    ap.add_argument("--min-severity", default="safe",
                    choices=SEVERITY_ORDER,
                    help="只显示 ≥ 该等级的事件(默认 safe = 全部)")
    ap.add_argument("--top", type=int, default=0,
                    help="最多列 N 条待办(默认 0 = 全部)")
    ap.add_argument("--output", "-o", help="输出文件路径(不指定则 stdout)")
    ap.add_argument("--format", choices=["text", "json", "md"], default="text",
                    help="输出格式(默认 text)")
    ap.add_argument("--no-classify", action="store_true",
                    help="跳过 LoRA 推理,只统计事件数(快速预览)")
    ap.add_argument("--no-laya", action="store_true",
                    help="M3.75:禁用 laya triage 副观察层(只用 LoRA 5 类)")
    ap.add_argument("--max-events", type=int, default=2000,
                    help="单次最多读多少事件(默认 2000,防卡)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # 默认 channel 列表
    if args.channel is None and not args.file:
        args.channel = ["System", "Application"]

    try:
        since = parse_since(args.since)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    # 1) 读事件
    try:
        events = _read_events(args, since)
    except FileNotFoundError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    if not events:
        print(f"[log_triage] 时间窗 {args.since} 内无事件(spec={args.spec})",
              file=sys.stderr)
        # 即便 0 事件也输出空报告(让脚本可被链式调用)
        empty = {
            "spec": args.spec,
            "since": since.isoformat(sep=" "),
            "total": 0, "parse_fail": 0,
            "distribution": {s: 0 for s in SEVERITY_ORDER},
            "min_severity": args.min_severity,
            "events": [],
        }
        if args.format == "json":
            _emit("{}", args)
        else:
            _emit(f"[log_triage] 0 events\n", args)
        return 0

    # 截断
    if args.max_events and len(events) > args.max_events:
        print(f"[info] 事件 {len(events)} 超过 max_events={args.max_events},"
              f" 截断", file=sys.stderr)
        events = events[: args.max_events]

    # 2) 分类
    if args.no_classify:
        classified = [{**ev, "risk": None, "action": None,
                       "jailbreak": "no", "risk_conf": None,
                       "parse_fail": True,
                       "laya_result": None} for ev in events]
    else:
        classified = _classify_events(events, spec=args.spec,
                                      no_laya=args.no_laya)

    # 3) 过滤 + 排序
    show = _filter_min_severity(classified, args.min_severity)
    show = _sort_and_trim(show, args.top)

    # 4) 分布
    dist_obj = _distribution(classified)

    # 5) 输出
    if args.format == "json":
        out = _format_json(classified, dist_obj, args.spec,
                           since, show, args.min_severity)
    elif args.format == "md":
        out = _format_markdown(classified, dist_obj, args.spec,
                               since, show, args.min_severity)
    else:
        out = _format_text(classified, dist_obj, args.spec,
                           since, show, args.min_severity)
    _emit(out, args)
    return 0


def _emit(text: str, args) -> None:
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"[log_triage] 写入 {args.output} ({len(text)} 字节)",
              file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    raise SystemExit(main())