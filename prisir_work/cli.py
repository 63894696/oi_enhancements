"""prisir_work/cli.py — `python -m prisir_work` 的子命令扩展。

主入口仍是 `python -m prisir_work [port]`(起服务)。本模块提供 `video` 子命令:

    python -m prisir_work.cli video "帮我做个 9:16 短视频,主题是 PrisirAI"
    python -m prisir_work.cli video --exec "上传 C:/v.mp4 到 YouTube,标题 X"
    python -m prisir_work.cli video --cap video.info --body '{"input":"C:/v.mp4"}'

子命令选项:
  --exec              真发(默认 dry_run,只 parse + fill_defaults,不打网络)
  --cap CID           跳过自然语言,直接走指定 capability
  --body JSON         直接传 body(配合 --cap)
  --json              输出 JSON(默认人类可读)
  --no-confirm        skip confirm_callback,真发(只在 --exec 时生效)

设计:CLI 不启 PrisirWork 服务,直接复用 endpoints._REGISTRY 的 handler 调
     port_registry.proxy_post,跟 HTTP 入口等价。这样 CLI 既可独立测试,
     又跟主服务保持单一执行路径。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m prisir_work.cli",
        description="PrisirWork 命令行(主入口在 `python -m prisir_work` 起服务,"
                    "这里是 video/YouTube 自然语言入口)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_video = sub.add_parser(
        "video",
        help="自然语言 → 视频/YouTube 能力(agent 代执行入口)",
    )
    p_video.add_argument("query", nargs="?", default="",
                       help="自然语言描述,例: 帮我做个 9:16 短视频,主题是 X"
                            "(--cap 时可省)")
    p_video.add_argument("--exec", dest="do_exec", action="store_true",
                       help="真发(默认 dry_run 只 parse)")
    p_video.add_argument("--no-confirm", dest="no_confirm", action="store_true",
                       help="skip confirm_callback(--exec 时生效)")
    p_video.add_argument("--cap", dest="cap_override",
                       help="跳过 parse_intent,直接走该 capability id")
    p_video.add_argument("--body", dest="body_json",
                       help="配合 --cap,直接传 JSON body")
    p_video.add_argument("--json", dest="json_output", action="store_true",
                       help="JSON 输出")

    p_help = sub.add_parser("capabilities",
                            help="列出所有可调的视频/YouTube 能力")
    p_help.add_argument("--json", dest="json_output", action="store_true")
    return p


def _confirm_callback_auto(intent: Any) -> bool:
    """CLI 默认 confirm 策略:缺必填 → False;齐全 → True(可 --no-confirm 跳过)"""
    return not bool(intent.missing)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "capabilities":
        from . import capability
        caps = capability.list_capabilities()
        video_caps = [c for c in caps
                      if c["id"].startswith("video.")
                      or c["id"].startswith("youtube.")]
        if args.json_output:
            print(json.dumps(video_caps, ensure_ascii=False, indent=2))
        else:
            for c in video_caps:
                print(f"  {c['id']:22s} [{c['risk']}] {c['title']}")
        return 0

    if args.cmd == "video":
        from . import agent_natural_video as anv

        # 1) 解析意图
        if args.cap_override:
            # 跳 parse,直接构造 intent
            from .agent_natural_video import IntentResult
            try:
                body = json.loads(args.body_json) if args.body_json else {}
            except json.JSONDecodeError as e:
                print(f"❌ --body JSON 解析失败: {e}", file=sys.stderr)
                return 2
            intent = IntentResult(
                ok=True, capability=args.cap_override, args=body,
                missing=[], confidence=1.0)
        elif not args.query:
            print("❌ 需要 query 或 --cap", file=sys.stderr)
            return 2
        else:
            intent = anv.parse_intent(args.query)
            if not intent.ok:
                if args.json_output:
                    print(json.dumps({"ok": False, "error": intent.error},
                                     ensure_ascii=False))
                else:
                    print(f"❌ {intent.error}", file=sys.stderr)
                return 1

        # 2) fill_defaults
        body = anv.fill_defaults(intent.capability, intent.args)

        if not args.do_exec:
            # dry_run — 只解析,不打网络
            if args.json_output:
                print(json.dumps({
                    "ok": True, "preview": True,
                    "intent": intent.to_dict(),
                    "body": body,
                    "missing": intent.missing,
                }, ensure_ascii=False, indent=2))
            else:
                print(f"🔍 命中能力: {intent.capability}  "
                      f"(confidence={intent.confidence})")
                print(f"📋 待补全参数: {intent.missing or '无'}")
                print(f"📦 最终 body:")
                print(json.dumps(body, ensure_ascii=False, indent=2))
                print()
                print("(加 --exec 真发;否则仅预览)")
            return 0

        # 3) 真发 — confirm + execute
        confirm_cb = None if args.no_confirm else _confirm_callback_auto
        result = anv.execute(args.query, dry_run=False,
                             confirm_callback=confirm_cb)

        if args.json_output:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"✅ 能力: {result.intent.capability}")
            print(f"📦 body: {json.dumps(result.body, ensure_ascii=False)}")
            print(f"📊 result: {json.dumps(result.result, ensure_ascii=False, indent=2)}")
            if result.error:
                print(f"⚠ error: {result.error}")
        return 0 if result.ok else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())