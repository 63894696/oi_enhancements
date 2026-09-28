#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 perf_alert CLI 入口(2026-09-24)
#
# 用法:
#   python main.py --interval 60                        # 前台常驻,60s 采一次
#   python main.py --interval 30 --output logs/perf_alert.jsonl
#   python main.py --interval 30 --output logs/p.jsonl --ws ws://localhost:8765
#   python main.py --watchdog                           # 联动 watchdog kill_mode(模拟)
#   python main.py --dry-run --iterations 5             # 跑 5 次不出告警
#   python main.py --once                               # 采一次 + 评 + 退出
#
# 设计:
#   - 离线跑(仅本地 LoRA 推理,无外部 API)
#   - 不会修改 companion/ 任何代码
#   - Phase 1 watchdog kill_mode = print 模拟,Phase 2 真接
#   - WebSocket 无 server 时优雅跳过
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from perf_alert import (  # noqa: E402
    run_loop,
    sample_once,
    format_log_line,
    format_json_line,
    should_alert,
    alert_actions,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="perf_alert",
        description="M3.69 perf_alert — 本地性能快照实时告警"
                    "(本地 LoRA 推理,离线可用)",
    )
    ap.add_argument("--interval", type=int, default=60,
                    help="采样间隔秒(默认 60)")
    ap.add_argument("--spec", default="perf_conf_v3",
                    choices=["perf_conf", "perf_conf_v2", "perf_conf_v3"],
                    help="用哪个 adapter(默认 perf_conf_v3,critical 78%% ACC)")
    ap.add_argument("--output", "-o",
                    help="JSONL 输出文件(append,记录每次采样 + 评)")
    ap.add_argument("--ws",
                    help="WebSocket URL(如 ws://localhost:8765),"
                         " 无 server 时优雅跳过")
    ap.add_argument("--watchdog", action="store_true",
                    help="critical 时模拟触发 watchdog kill_mode=kill"
                         " (Phase 1 仅 print 模拟)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑采样 + 评,不出告警动作")
    ap.add_argument("--iterations", type=int, default=0,
                    help="最多跑 N 次(0 = 不限,默认 0)")
    ap.add_argument("--once", action="store_true",
                    help="采一次 + 评 + 退出(测试用)")
    return ap


def _run_once(args) -> int:
    """采一次 + 评 + 退出。"""
    try:
        sample, result = sample_once(spec=args.spec)
    except Exception as e:  # noqa: BLE001
        print(f"[perf_alert] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # 1) 文本行
    print(format_log_line(sample, result))
    # 2) 风险 + 动作
    print(f"  risk      : {result.get('risk')}",
          f"(conf={result.get('risk_conf')})")
    print(f"  action    : {result.get('action')}",
          f"(conf={result.get('action_conf')})")
    print(f"  latency   : {result.get('latency_ms')} ms",
          f"  parse_fail={result.get('parse_fail')}")
    # 3) 告警动作(默认不打;--watchdog 才模拟)
    if should_alert(result) and not args.dry_run:
        for ln in alert_actions(sample, result, watchdog=args.watchdog,
                                ws_url=args.ws):
            print(ln)

    # 4) 写 output 文件
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(format_json_line(sample, result) + "\n")
        print(f"[perf_alert] 写入 {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.once:
        return _run_once(args)

    output_path = Path(args.output) if args.output else None
    return run_loop(
        interval=args.interval,
        spec=args.spec,
        dry_run=args.dry_run,
        max_iter=args.iterations,
        watchdog=args.watchdog,
        ws_url=args.ws,
        output_path=output_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
