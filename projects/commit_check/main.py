#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — M3.69 commit_check CLI 入口(2026-09-24)
#
# 用法:
#   python main.py --staged
#   python main.py --diff HEAD
#   python main.py --diff HEAD~3
#   python main.py --staged --dangerous-ext .pem,.key,.pfx
#   python main.py --staged --format json
#   python main.py --staged --format md --output report.md
#   python main.py --staged --dry-run
#
# 退出码:
#   allow / ask → 0
#   deny        → 1(让 git hook 真拦)
#   其他错误    → 2
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))               # 让 src/ 可 import
# 让 companion/ 可 import:commit_check/ → projects/ → oi_enhancements/ → companion/
_COMPANION = _HERE.parent.parent / "companion"
if _COMPANION.exists() and str(_COMPANION) not in sys.path:
    sys.path.insert(0, str(_COMPANION))

from src.commit_check import (
    DEFAULT_DANGEROUS_EXT,
    check_staged, check_vs, _decide, Decision, PathRisk, IntentRisk,
)


# ------------------------------------------------------------
# 输出格式
# ------------------------------------------------------------
def _format_text(dec: Decision) -> str:
    """文本格式(默认)。"""
    lines: list[str] = []
    lines.append(f"[diff summary] {dec.diff_summary}")

    # M3.74 laya_guard 详情
    if dec.laya_result:
        lr = dec.laya_result
        avail = "loaded" if lr.get("available") else "unavailable(regex-only)"
        lines.append("")
        lines.append(
            f"[laya_guard] (M3.74 fast-path, backend={dec.backend}, "
            f"{avail})")
        lines.append(
            f"  risk={lr['risk']}  "
            f"jb={lr['jailbreak']:.2f}  "
            f"inj={lr['injection']:.2f}  "
            f"sens={lr['sensitive']:.2f}  "
            f"harm={lr['harm']:.2f}  "
            f"latency={lr['latency_ms']:.0f}ms")
        if lr.get("regex_hits"):
            lines.append(f"  regex_hits ({len(lr['regex_hits'])}):")
            for h in lr["regex_hits"][:3]:
                lines.append(f"    - {h[:80]}")
        if lr.get("reason"):
            lines.append(f"  reason: {lr['reason']}")

    if dec.paths:
        lines.append("")
        lines.append("[disk_cleanup_conf] 检查变更路径:")
        for p in dec.paths:
            tag = "  [dangerous]" if p.matched_dangerous else ""
            lines.append(f"  {p.path:50s} {p.risk}{tag}")

    if dec.intent and dec.intent.risk != "unknown":
        lines.append("")
        lines.append("[task_conf] 分析变更内容:")
        lines.append(
            f"  → task_type={dec.intent.task_type} "
            f"risk={dec.intent.risk}")

    lines.append("")
    lines.append(f"→ 决策: {dec.decision}")
    for r in dec.reasons:
        lines.append(f"   - {r}")
    if dec.suggestion:
        lines.append(f"→ 建议: {dec.suggestion}")
    return "\n".join(lines)


def _format_json(dec: Decision) -> str:
    """JSON 格式。"""
    return json.dumps(dec.to_dict(), ensure_ascii=False, indent=2)


def _format_markdown(dec: Decision) -> str:
    """Markdown 报告格式。"""
    lines: list[str] = []
    lines.append("# commit_check 报告")
    lines.append("")
    lines.append(f"- **diff summary**: {dec.diff_summary}")
    lines.append(f"- **决策**: `{dec.decision}`")
    lines.append(f"- **backend**: `{dec.backend}`")
    lines.append("")

    if dec.laya_result:
        lr = dec.laya_result
        lines.append("## laya_guard 检测 (M3.74 fast-path)")
        lines.append("")
        lines.append(f"- **risk**: `{lr['risk']}`")
        lines.append(f"- **available**: `{lr['available']}`")
        lines.append(
            f"- **jb={lr['jailbreak']:.2f}  inj={lr['injection']:.2f}  "
            f"sens={lr['sensitive']:.2f}  harm={lr['harm']:.2f}**")
        if lr.get("reason"):
            lines.append(f"- **reason**: {lr['reason']}")
        if lr.get("regex_hits"):
            lines.append(f"- **regex_hits** ({len(lr['regex_hits'])} 条):")
            for h in lr["regex_hits"][:5]:
                lines.append(f"  - `{h[:100]}`")
        lines.append("")
    lines.append("## 理由")
    for r in dec.reasons:
        lines.append(f"- {r}")
    if dec.suggestion:
        lines.append("")
        lines.append(f"## 建议")
        lines.append("")
        lines.append("```")
        lines.append(dec.suggestion)
        lines.append("```")

    if dec.paths:
        lines.append("")
        lines.append("## 路径风险明细")
        lines.append("")
        lines.append("| 路径 | 风险 | 动作 | 是否命中危险扩展 |")
        lines.append("|------|------|------|------------------|")
        for p in dec.paths:
            lines.append(
                f"| `{p.path}` | {p.risk} | {p.action or '-'} | "
                f"{'是' if p.matched_dangerous else '否'} |"
            )

    if dec.intent:
        lines.append("")
        lines.append("## 变更意图")
        lines.append("")
        lines.append(f"- task_type: `{dec.intent.task_type}`")
        lines.append(f"- risk: `{dec.intent.risk}`")
        if dec.intent.action_conf is not None:
            lines.append(f"- action_conf: `{dec.intent.action_conf:.3f}`")
    return "\n".join(lines)


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="commit_check",
        description="M3.69 Git pre-commit 风险扫描(本地 AI 双 spec 联合判)",
    )
    ap.add_argument("--staged", action="store_true",
                    help="检查 git staged diff(默认)")
    ap.add_argument("--diff", metavar="REF",
                    help="检查 vs 指定 commit(如 HEAD / HEAD~3 / abc123)")
    ap.add_argument("--dangerous-ext", default=",".join(DEFAULT_DANGEROUS_EXT),
                    help=f"危险扩展名(逗号分隔,默认 {','.join(DEFAULT_DANGEROUS_EXT)})")
    ap.add_argument("--format", choices=["text", "json", "md"], default="text",
                    help="输出格式(默认 text)")
    ap.add_argument("--output", "-o", metavar="FILE",
                    help="写到文件(默认 stdout)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑不通 git(本工具只 print 建议,默认就 dry-run)")
    ap.add_argument("--no-laya", action="store_true",
                    help="M3.74:禁用 laya_guard fast-path(强制 LoRA 双 spec)")
    args = ap.parse_args()

    # 默认行为:无 --staged/--diff 时用 --staged
    if not args.staged and not args.diff:
        args.staged = True

    dangerous_ext = tuple(
        e.strip() for e in args.dangerous_ext.split(",") if e.strip())

    try:
        if args.staged:
            dec = check_staged(dangerous_ext=dangerous_ext,
                                no_laya=args.no_laya)
        else:
            dec = check_vs(args.diff, dangerous_ext=dangerous_ext,
                            no_laya=args.no_laya)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"ERROR: {type(e).__name__}: {e}\n")
        return 2

    # 渲染
    if args.format == "json":
        out = _format_json(dec)
    elif args.format == "md":
        out = _format_markdown(dec)
    else:
        out = _format_text(dec)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        sys.stderr.write(f"[commit_check] 报告已写: {args.output}\n")
    else:
        print(out)

    # 退出码
    if dec.decision == "deny":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
