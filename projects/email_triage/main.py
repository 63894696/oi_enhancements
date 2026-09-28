#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — email_triage CLI 入口(M3.69 Phase 1)(2026-09-24)
#
# 目的:
#   - 单文件 CLI 入口,把 email_triage.run 包成 argparse
#   - 默认扫 ./inbox,支持 eml-dir / imap 两种来源
#   - 输出 text / json / md 到 stdout 或 --output 文件
#
# 用法:
#   python main.py                                    # 默认 ./inbox
#   python main.py --eml-dir "D:/mails/inbox"
#   python main.py --imap imap.gmail.com --user x@gmail.com \
#       --password-env GMAIL_PASSWORD --since 24h
#   python main.py --top 20 --format md --output report.md
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="email_triage",
        description="邮件紧急度筛(本地 LoRA,5 类风险 + critical/high 待办清单)。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python main.py --eml-dir ./inbox --format text
  python main.py --imap imap.gmail.com --user me@gmail.com \\
      --password-env GMAIL_PASSWORD --since 24h
  python main.py --top 20 --format md --output daily.md

密码安全:用 --password-env ENV_VAR(从环境变量读,不要传明文)。""",
    )
    src = ap.add_argument_group("来源(选其一)")
    src.add_argument(
        "--eml-dir", default="./inbox",
        help="本地 .eml 目录(递归,默认 ./inbox)")
    src.add_argument(
        "--imap", help="IMAP 服务器(host:port,port 可省,默认 993)")
    src.add_argument(
        "--user", help="IMAP 用户名")
    src.add_argument(
        "--password-env",
        help="IMAP 密码所在环境变量名(必填 IMAP 时,不要传明文密码)")
    src.add_argument(
        "--folder", default="INBOX", help="IMAP 文件夹(默认 INBOX)")
    src.add_argument(
        "--since", default="24h",
        help="时间窗,如 24h / 7d / 30m(默认 24h)")

    out = ap.add_argument_group("输出")
    out.add_argument(
        "--format", choices=["text", "json", "md"], default="text",
        help="输出格式(默认 text)")
    out.add_argument(
        "--top", type=int, default=20,
        help="critical+high 只显示前 N 条(默认 20,0 = 不截断)")
    out.add_argument(
        "--output", "-o", help="写到文件(默认 stdout)")

    misc = ap.add_argument_group("其他")
    misc.add_argument(
        "--spec", default="email_conf",
        choices=["email", "email_conf"],
        help="用哪个 adapter(默认 email_conf,带 calibrated conf)")
    misc.add_argument(
        "--stats", action="store_true",
        help="末尾打一行 stats(stderr)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_argparser()
    args = ap.parse_args(argv)

    # 来源决定
    if args.imap:
        if not args.user or not args.password_env:
            print("ERROR: --imap 必须同时给 --user 和 --password-env",
                  file=sys.stderr)
            return 2
        host_port = args.imap.split(":")
        server = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 993
        source = {
            "imap": {
                "server": server,
                "port": port,
                "user": args.user,
                "password_env": args.password_env,
                "folder": args.folder,
                "since": args.since,
            },
        }
    else:
        source = {
            "eml_dir": args.eml_dir,
            "since": args.since,
        }

    # lazy import(避免 --help 慢)
    from src.email_triage import run

    top_n = args.top if args.top and args.top > 0 else None
    try:
        out, stats = run(source, top_n=top_n, since_label=args.since,
                         spec_name=args.spec, fmt=args.format)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        if args.stats:
            print(f"[stats] wrote {args.output} | "
                  f"total={stats['total']} errors={stats['errors']} "
                  f"elapsed={stats['elapsed_sec']}s "
                  f"max_risk={stats['max_risk']}", file=sys.stderr)
    else:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
        if args.stats:
            print(f"[stats] total={stats['total']} errors={stats['errors']} "
                  f"elapsed={stats['elapsed_sec']}s "
                  f"max_risk={stats['max_risk']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())