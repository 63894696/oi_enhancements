#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_tempfile.py — M3.61 临时文件分类(本地小模型)(2026-09-23)
#
# 目的:
#   - 接 adapter_registry.get_adapter("tempfile") + tempfile_conf
#   - 与 classify_email / classify_disk_cleanup 同结构(adapter, path_str) → dict
#   - 输出 {risk, action, jailbreak, risk_conf, action_conf, jb_conf, raw, tokens, latency_ms}
#
# 设计:
#   - 5 类风险:safe/low/medium/high/critical
#   - 3 类 action:delete/review/keep
#   - build_text 格式:file_path + size + age + git_status 紧凑单行(对齐训练数据形态)
#
# 用法:
#   from classify_tempfile import classify_tempfile
#   out = classify_tempfile(adapter, "node_modules/.cache/jest/abc.json|2MB|30 天|npm 依赖")
#   # {'risk': 'medium', 'action': 'delete', ...}
#
#   CLI:
#     python classify_tempfile.py "node_modules/.cache/x.js|2MB|30 天|npm"
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from adapter_registry import get_adapter  # noqa: E402

# 与训练数据 schema 对齐(同 safety 5 类 + 3 action)
RISK_LABELS = {"safe", "low", "medium", "high", "critical"}
ACTION_LABELS = {"delete", "review", "keep"}


def _build_text(path: str) -> str:
    """组装训练/推理 prompt 段。M3.61 对齐 train_step1 tempfile schema。

    Args:
        path: 文件路径(完整或相对),已包含扩展名/大小/年龄/git 信息(由调用方拼接)。
              训练时训练集每条已带 `路径: ... | 大小: ... | 年龄: ... | git: ...` 字段,
              所以这里直接当 body 透传。
    """
    return f"文件路径: {path}\n问: 这个临时文件是否可以安全删除?"


def _parse_output(raw: str) -> tuple[Optional[str], Optional[bool], Optional[str],
                                      Optional[float], Optional[float], Optional[float]]:
    """从 raw 抽 (risk, jailbreak, action, risk_conf, jb_conf, action_conf)。"""
    import re
    # 截到第二个 Safety 之前
    idx = raw.lower().find("safety:")
    if idx < 0:
        return None, None, None, None, None, None
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = re.search(
        r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
        r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
        r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
        seg, re.IGNORECASE | re.MULTILINE,
    )
    if not m:
        return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return risk, jb, action, risk_conf, jb_conf, action_conf


def classify_tempfile(adapter, path: str, use_conf: bool = True) -> dict:
    """单条临时文件分类。adapter = LoadedAdapter 实例。"""
    name = "tempfile_conf" if use_conf else "tempfile"
    # 若调用方传的 adapter 不是 tempfile 系的,重新拿
    if adapter.spec.name not in ("tempfile", "tempfile_conf"):
        adapter = get_adapter(name)
    text = _build_text(path)
    t0 = time.time()
    res = adapter.classify(text, max_new_tokens=40)
    dt_ms = int((time.time() - t0) * 1000)
    raw = res["raw"]
    parsed = _parse_output(raw)
    risk, jb, action, rc, jc, ac = parsed
    return {
        "path": path,
        "risk": risk,
        "action": action,
        "jailbreak": "yes" if jb else "no",
        "risk_conf": rc,
        "action_conf": ac,
        "jb_conf": jc,
        "raw": raw,
        "tokens": res["tokens"],
        "latency_ms": dt_ms,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="tempfile / tempfile_conf 推理入口")
    ap.add_argument("paths", nargs="*",
                    help="文件路径(可多个,含 路径|大小|年龄|git 描述)")
    ap.add_argument("--file", help="含路径列表的文件")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读路径(每行一条)")
    ap.add_argument("--no-conf", action="store_true",
                    help="用 base adapter(不带 confidence)")
    args = ap.parse_args()

    use_conf = not args.no_conf
    name = "tempfile_conf" if use_conf else "tempfile"
    adapter = get_adapter(name)

    paths: list[str] = list(args.paths)
    if args.file:
        paths.extend(Path(args.file).read_text(encoding="utf-8").splitlines())
    if args.stdin:
        paths.extend(sys.stdin.read().splitlines())
    paths = [p.strip() for p in paths if p.strip()]

    if not paths:
        ap.print_help()
        return 1

    results: list[dict] = []
    for p in paths:
        results.append(classify_tempfile(adapter, p, use_conf=use_conf))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
