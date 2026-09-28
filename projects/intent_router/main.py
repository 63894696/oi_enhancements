#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 intent_router CLI 入口(2026-09-24)
#
# 用法:
#   python main.py --text "清理 D 盘垃圾"
#   echo "..." | python main.py
#   python main.py --interactive
#   python main.py --text "..." --multi
#   python main.py --text "..." --run
#   python main.py --text "..." --dry-run
#   python main.py --route-map
#
# 设计:
#   - 单文件 CLI,纯 stdlib(除了 companion 两个 wrapper 依赖)
#   - 三种输入:--text / stdin / --interactive
#   - 三种输出:--format text|md|json(默认 text)
#   - --multi 输出多候选排序
#   - --run / --dry-run(本期都只 print,不真执行)
#   - 错误处理:空输入 / model 加载失败都给友好提示
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "src"))

from intent_router import (  # noqa: E402
    classify_and_route, multi_candidates, route_map,
    format_text, format_md, format_json,
)


def _read_text(args) -> Optional[str]:
    """按优先级取输入:--text > stdin > interactive(单独处理)。"""
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        # 有 stdin 重定向 / pipe
        data = sys.stdin.read().strip()
        if data:
            return data
    return None


def _interactive_loop(args) -> int:
    """交互模式:循环读 stdin 一行,直到 EOF。"""
    print("=" * 60)
    print("intent_router 交互模式 (输入文本回车,Ctrl+D/Z 退出)")
    print("输入 'quit' / 'exit' 退出")
    print("=" * 60)
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        if not line:
            continue
        if line.lower() in ("quit", "exit"):
            return 0
        try:
            result = classify_and_route(line, use_conf=not args.no_conf)
            print()
            print(format_text(result))
            if args.run and not args.dry_run:
                print()
                print("[--run 模式] 本期不真执行,仅 print 路由建议")
                print(f"如要执行: {result['route']['action']}")
        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            continue


def _format_output(result: dict, fmt: str) -> str:
    if fmt == "json":
        return format_json(result)
    if fmt == "md":
        return format_md(result)
    return format_text(result)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="intent_router",
        description="M3.69 意图分发器 — 双 spec(intents_conf + task_conf)联合路由\n"
                    "用法:\n"
                    "  python main.py --text '清理 D 盘垃圾'\n"
                    "  echo '...' | python main.py\n"
                    "  python main.py --interactive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--text", help="单条文本输入")
    ap.add_argument("--history", default="", help="上下文(可空)")
    ap.add_argument("--no-conf", action="store_true",
                    help="用 base adapter(不带 confidence)而非 conf 版")
    ap.add_argument("--no-laya", action="store_true",
                    help="禁用 laya fast-path(强制 LoRA intents_conf + task_conf)")
    ap.add_argument("--laya-floor", type=float, default=0.60,
                    help="laya 置信度阈值,>= 此值用 laya 否则回退 LoRA(默认 0.60)")
    ap.add_argument("--multi", action="store_true",
                    help="输出多候选排序(--top-k 限制数量)")
    ap.add_argument("--top-k", type=int, default=5,
                    help="多候选时取前 k 个(默认 5)")
    ap.add_argument("--format", choices=["text", "md", "json"],
                    default="text",
                    help="输出格式(默认 text)")
    ap.add_argument("--interactive", action="store_true",
                    help="交互模式(循环读 stdin)")
    ap.add_argument("--run", action="store_true",
                    help="run 模式(本期只 print 不真执行)")
    ap.add_argument("--dry-run", action="store_true",
                    help="dry-run 模式(更激进的 print only)")
    ap.add_argument("--route-map", action="store_true",
                    help="打印完整路由表并退出")
    args = ap.parse_args()

    # 路由表 dump
    if args.route_map:
        print(json.dumps(route_map(), ensure_ascii=False, indent=2))
        return 0

    # 交互模式
    if args.interactive:
        return _interactive_loop(args)

    # 取输入
    text = _read_text(args)
    if not text:
        ap.print_help()
        print("\n[错误] 必须提供输入:--text 或 stdin 或 --interactive",
              file=sys.stderr)
        return 1

    try:
        if args.multi:
            candidates = multi_candidates(text, top_k=args.top_k)
            if args.format == "json":
                print(format_json({
                    "input": text,
                    "candidates": candidates,
                }))
            else:
                print(f"输入: {text!r}\n")
                print(f"多候选排序(Top {len(candidates)}):\n")
                for i, c in enumerate(candidates, 1):
                    print(f"#{i}  {c['intent']} + {c['task']}  "
                          f"joint={c['joint_prob']:.3f}  "
                          f"spec={c['spec_short']}  "
                          f"executor={c['executor']}")
            return 0

        # 单条
        result = classify_and_route(text, history=args.history,
                                     use_conf=not args.no_conf,
                                     laya_floor=args.laya_floor,
                                     no_laya=args.no_laya)
        print(_format_output(result, args.format))

        if args.run or args.dry_run:
            print()
            if args.dry_run:
                print(f"[--dry-run] 路由建议(不执行): {result['route']['action']}")
            else:
                print(f"[--run] 本期不真执行,推荐执行: "
                      f"{result['route']['action']}")

        return 0

    except FileNotFoundError as e:
        print(f"[模型错误] {e}", file=sys.stderr)
        print("  → 确认 adapter 路径存在:", file=sys.stderr)
        print("    D:/prisir-train-assets/trained/{intents_conf,task_conf}/adapter/",
              file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"[注册表错误] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[未预期错误] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
