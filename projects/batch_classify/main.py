#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 batch_classify CLI 入口(2026-09-24)
#
# 通用 wrapper:扫目录/文件/日志行 → 调任意 spec → 出 5 类风险分布报告
#
# 用法(用户视角):
#   python main.py --root "D:/Users/Admin/Temp" --specs disk_cleanup,tempfile
#   python main.py --file "D:/path/to/some.log" --spec log
#   python main.py --text "rm -rf C:\Windows\System32" --spec safety
#   python main.py --root "D:/projects" --spec disk_cleanup --output "report.csv" --format csv
#   python main.py --root "D:/logs" --spec log --since "24h" --output "report.md" --format md
#   python main.py --root "D:/Users/Admin/Temp" --specs disk_cleanup,tempfile --dry-run
#
# 输出:CSV / JSON / Markdown 三选一(--format)
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "src"))

from src.batch_classify import (
    iter_root,
    run_batch,
    format_output,
)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="batch_classify",
        description=(
            "通用 wrapper:扫目录/文件/文本 → 任意 spec → 5 类风险分布报告。\n"
            "多 spec 联合:--specs disk_cleanup,tempfile 时取最高风险。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python main.py --root D:/Temp --specs disk_cleanup --limit 10 --dry-run\n"
            "  python main.py --file app.log --spec log --format csv --output report.csv\n"
            "  python main.py --text 'rm -rf C:/Windows' --spec safety\n"
            "  python main.py --root D:/logs --spec log --since 24h --format md -o r.md\n"
            "\n"
            "可用 spec(--list 查看):\n"
            "  safety / tempfile / disk_cleanup / email / log / intents / perf_conf / task\n"
        ),
    )

    inp = ap.add_argument_group("输入(三选一)")
    inp.add_argument("--root", help="扫描目录(递归)")
    inp.add_argument("--file", help="扫描单个文件")
    inp.add_argument("--text", help="扫描单条文本")

    ap.add_argument("--spec", "--scenario", dest="spec",
                    help="单个 spec 名(可用 --list 查看所有)")
    ap.add_argument("--specs", help="多个 spec,逗号分隔(联合时取最高风险)")

    flt = ap.add_argument_group("过滤(仅--root 生效)")
    flt.add_argument("--since", help="时间窗,如 24h / 7d / 30m(只取 mtime 在窗口内)")
    flt.add_argument("--ext", nargs="+", default=None,
                     help="扩展名白名单,如 .log .tmp")
    flt.add_argument("--limit", type=int, default=None,
                     help="最多扫多少文件(默认无限)")

    out = ap.add_argument_group("输出")
    out.add_argument("--format", choices=["csv", "json", "md", "markdown"],
                     default="csv", help="输出格式(默认 csv)")
    out.add_argument("--output", "-o", help="输出文件路径(默认 stdout)")
    out.add_argument("--dry-run", action="store_true",
                     help="只跑前 5 条(快速预览)")

    ap.add_argument("--list", action="store_true",
                    help="列出所有可用 spec 后退出")

    return ap


def _print_specs() -> None:
    """列所有可用 spec。"""
    from adapter_registry import list_scenarios
    scenarios = list_scenarios()
    print(f"已注册 spec({len(scenarios)} 个):")
    for s in scenarios:
        print(f"  - {s}")


def _resolve_specs(args) -> list[str]:
    """从 --spec / --specs 拿 spec 列表,二选一。"""
    specs: list[str] = []
    if args.spec:
        specs.append(args.spec.strip())
    if args.specs:
        specs.extend(s.strip() for s in args.specs.split(",") if s.strip())
    # 去重保序
    seen = set()
    out = []
    for s in specs:
        if s and s not in seen:
            out.append(s)
            seen.add(s)
    return out


def main(argv=None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.list:
        try:
            _print_specs()
        except Exception as e:  # noqa: BLE001
            print(f"列 spec 失败: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        return 0

    specs = _resolve_specs(args)
    if not specs:
        print("ERROR: 必须传 --spec 或 --specs", file=sys.stderr)
        ap.print_help()
        return 1

    # 至少一个输入源
    sources = [bool(args.root), bool(args.file), bool(args.text)]
    if sum(sources) == 0:
        print("ERROR: 必须传一个输入 --root / --file / --text", file=sys.stderr)
        ap.print_help()
        return 1
    if sum(sources) > 1:
        print("ERROR: --root / --file / --text 三选一,不要同时传多个",
              file=sys.stderr)
        return 1

    # 准备 inputs
    inputs: list[tuple[str, str]] = []
    root_desc = ""
    if args.root:
        root_desc = args.root
        try:
            paths = list(iter_root(
                args.root, since=args.since, limit=args.limit,
                extensions=args.ext))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: 扫目录失败 {args.root}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return 2
        if not paths:
            print(f"WARNING: 目录 {args.root} 下没找到符合过滤条件的文件",
                  file=sys.stderr)
            return 1
        inputs = [("path", str(p)) for p in paths]
        root_desc = f"`{args.root}`  ({len(inputs)} 文件"
        if args.limit:
            root_desc += f", limit={args.limit}"
        if args.since:
            root_desc += f", since={args.since}"
        root_desc += ")"
    elif args.file:
        if not Path(args.file).exists():
            print(f"ERROR: 文件不存在: {args.file}", file=sys.stderr)
            return 2
        inputs = [("file", args.file)]
        root_desc = f"`{args.file}`"
    else:  # args.text
        inputs = [("text", args.text)]
        root_desc = f"文本 ({len(args.text)} 字)"

    # 跑批
    print(f"[batch_classify] spec={specs} 输入={len(inputs)} 条 "
          f"dry_run={args.dry_run}", file=sys.stderr)

    def _on_progress(done: int, total: int) -> None:
        if done % 5 == 0 or done == total:
            print(f"  progress: {done}/{total}", file=sys.stderr)

    t0 = time.time()
    try:
        results = run_batch(
            inputs, specs, dry_run=args.dry_run,
            on_progress=_on_progress)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 跑批失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    dt = time.time() - t0
    print(f"[batch_classify] 完成 {len(results)} 条, 耗时 {dt:.1f}s, "
          f"avg {dt / len(results) * 1000:.0f}ms/条", file=sys.stderr)

    # 格式化输出
    text = format_output(results, args.format, root_desc=root_desc)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"[batch_classify] 写入 {out_path}", file=sys.stderr)
    else:
        # 写到 stdout(strip 输出,避免混入 stderr 进度)
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())