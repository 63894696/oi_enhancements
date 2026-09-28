#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — cleanup_suggest CLI 入口 (M3.69, 2026-09-24)

用法:
    python main.py                              # 默认扫用户 Temp
    python main.py --root "D:/downloads"
    python main.py --root "D:/Users/Admin/Temp" --auto-apply --yes
    python main.py --root "D:/Temp" --format json --output report.json
    python main.py --root "D:/Temp" --dry-run --limit 50
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 项目根加入 path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from src.cleanup_suggest import (  # noqa: E402
    apply_deletion,
    load_adapters,
    print_text_report,
    run_scan_and_classify,
)


def _default_root() -> str:
    """默认扫用户的 Temp。"""
    return str(Path.home() / "AppData/Local/Temp")


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cleanup_suggest",
        description="磁盘清理建议器 — 本地 AI (tempfile + disk_cleanup_conf) 联合判定",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  %(prog)s                                  扫默认 Temp
  %(prog)s --root "D:/downloads"            扫指定目录
  %(prog)s --auto-apply --yes               自动删 safe/low
  %(prog)s --auto-apply --include-medium    自动删 safe/low/medium
  %(prog)s --format json --output r.json    JSON 输出
  %(prog)s --dry-run --limit 50             dry-run 只看前 50 个
""",
    )
    ap.add_argument("--root", default=_default_root(),
                    help=f"扫的根目录(默认 {_default_root()})")
    ap.add_argument("--limit", type=int, default=None,
                    help="最多扫 N 个文件(dry-run 时常配)")
    ap.add_argument("--auto-apply", action="store_true",
                    help="自动应用建议(删 safe/low;加 --include-medium 才删 medium)")
    ap.add_argument("--include-medium", action="store_true",
                    help="auto-apply 时一并删 medium(默认保留)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="跳过用户确认 prompt(给 --auto-apply 用)")
    ap.add_argument("--dry-run", action="store_true",
                    help="dry-run:只跑前 20 个,不输出建议(配合 --limit)")
    ap.add_argument("--format", choices=["text", "json"], default="text",
                    help="输出格式(默认 text)")
    ap.add_argument("--output", help="写到指定文件,默认 stdout")
    ap.add_argument("--no-progress", action="store_true",
                    help="关闭进度提示(stderr)")
    return ap


def _confirm_user(prompt: str) -> bool:
    """交互式 y/N 确认。"""
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)

    # 0. 参数校验
    root_path = Path(args.root)
    if not root_path.exists():
        print(f"ERROR: 路径不存在: {args.root}", file=sys.stderr)
        return 2
    if not root_path.is_dir():
        print(f"ERROR: 不是目录: {args.root}", file=sys.stderr)
        return 2

    # 1. dry-run 模式:强制 limit,只跑不输出
    if args.dry_run:
        if not args.limit:
            args.limit = 20
        print(f"[dry-run] 扫 {args.root},最多 {args.limit} 文件,不输出建议", file=sys.stderr)

    # 2. 进度回调
    show_progress = not args.no_progress and not args.dry_run
    def progress_cb(count: int) -> None:
        if show_progress and count % 100 == 0:
            print(f"  ... 已扫 {count} 文件", file=sys.stderr)

    # 3. 加载 adapter(失败时给友好提示)
    try:
        disk_adapter, tmp_adapter = load_adapters()
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 加载 model/adapter 失败: {type(e).__name__}: {e}", file=sys.stderr)
        print("提示: 确认 Qwen3Guard-Gen-0.6B 在 "
              "D:/prisir-train-assets/models/ 且 adapter 目录齐全", file=sys.stderr)
        return 3

    # 4. 扫 + 分类
    try:
        report = run_scan_and_classify(
            args.root,
            disk_adapter,
            tmp_adapter,
            limit=args.limit,
            progress_cb=progress_cb,
        )
    except (FileNotFoundError, NotADirectoryError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"ERROR: 权限不足: {e}", file=sys.stderr)
        return 4

    # 5. dry-run 模式只给一行汇总,退出
    if args.dry_run:
        msg = (f"[dry-run 完成] 扫了 {report.scanned_files} 文件 / "
              f"{report.scanned_size_bytes / 1024**2:.1f} MB, "
              f"用时 {report.elapsed_sec:.1f}s")
        if args.format == "json":
            print(json.dumps({
                "dry_run": True,
                "scanned_files": report.scanned_files,
                "scanned_size_mb": round(report.scanned_size_bytes / 1024**2, 2),
                "elapsed_sec": round(report.elapsed_sec, 2),
            }, ensure_ascii=False))
        else:
            print(msg)
        return 0

    # 6. 输出
    if args.format == "json":
        payload = report.to_dict()
        out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        # text: 先打报告,然后 human summary
        print_text_report(report)
        out_text = ""

    # 7. auto-apply 流程
    applied = False
    deleted: list = []
    failed: list = []

    if args.auto_apply:
        if not args.yes:
            # 没 --yes → 必须交互确认
            print("[!] --auto-apply 需要 --yes 或手动确认", file=sys.stderr)
            if not _confirm_user("确认要删 safe/low 文件吗? [y/N] "):
                print("已取消,无文件被删。", file=sys.stderr)
                return 0
        # 删 medium 走二次确认
        include_med = args.include_medium
        if include_med and not args.yes:
            if not _confirm_user("确认要删 medium 文件吗? [y/N] "):
                print("已跳过 medium,只删 safe/low。", file=sys.stderr)
                include_med = False
        deleted, failed = apply_deletion(
            report,
            auto_safe_low=True,
            user_confirmed_medium=include_med,
        )
        applied = bool(deleted) or bool(failed)
        report.applied = applied

        if args.format == "text":
            print(f"  [执行] 删除 {len(deleted)} 文件,失败 {len(failed)} 文件")
            if failed:
                print("  失败示例(前 5 条):")
                for f in failed[:5]:
                    print(f"    {f['path']}: {f.get('reason')}")
        elif args.format == "json":
            # 重打 JSON 含 applied + deleted count
            payload = report.to_dict()
            payload["deleted_files"] = deleted[:50]  # 防 JSON 巨大
            payload["failed_deletes"] = failed[:50]
            out_text = json.dumps(payload, ensure_ascii=False, indent=2)

    # 8. 写输出
    if args.format == "json":
        if args.output:
            Path(args.output).write_text(out_text, encoding="utf-8")
            print(f"[done] 报告写入 {args.output}", file=sys.stderr)
        else:
            print(out_text)
    else:
        if not applied:
            print("[提示] 仅生成了建议,未删文件。加 --auto-apply --yes 执行删除。")
        else:
            print("[done] 执行完毕")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())