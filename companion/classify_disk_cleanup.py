#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_disk_cleanup.py — M3.47 + M3.45.1 集成入口(2026-09-23)
#
# 目的:
#   - 当用户问"该不该删 C:\Windows\..."时,本地 disk_cleanup_conf 立刻分类
#   - 取代对 Jev API 的依赖(Jev 调的是 chat 消息语义,跟文件级风险判断不匹配)
#   - 返回 Jev 兼容 schema {risk, jailbreak, action, confidence},上层 decide_block 可直接复用
#
# 用法:
#   python classify_disk_cleanup.py "C:\Windows\Temp\foo.log"
#   python classify_disk_cleanup.py --file paths.txt
#   python classify_disk_cleanup.py --stdin  < paths.txt
#
# 输出(JSON 一行一条):
#   {"path": "...", "risk": "medium", "action": "review",
#    "jailbreak": "no", "risk_conf": 0.83, "action_conf": 0.91, "raw": "...", "latency_ms": 1234}
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _build_text(path: str) -> str:
    """组装跟训练数据一样的 prompt 段(供 adapter 推理)。"""
    p = Path(path)
    try:
        stat = p.stat()
        size_kb = stat.st_size / 1024
        size_str = (f"{size_kb:.0f}KB" if size_kb < 1024
                    else f"{size_kb/1024:.1f}MB")
        age_days = (time.time() - stat.st_mtime) / 86400
    except (OSError, FileNotFoundError):
        size_str = "?"
        age_days = 0
    parts = p.parts
    if len(parts) >= 5:
        ctx = "\\".join(parts[1:4])
        rel = "\\".join(parts[4:])
        loc = f"{ctx}\\{rel}"
    else:
        loc = str(p)
    return (f"文件路径: {loc}\n"
            f"扩展名: {p.suffix.lower() or '(无)'}\n"
            f"大小: {size_str}\n"
            f"年龄: {age_days:.0f} 天\n"
            f"问: 这个 Windows 系统文件是否可以安全清理?")


def _parse_output(raw: str) -> tuple[Optional[str], Optional[str], Optional[str],
                                      Optional[float], Optional[float], Optional[float]]:
    """从 adapter 输出抽 (risk, jb, action, risk_conf, jb_conf, action_conf)。
    复读:只取第一段。
    """
    import re
    PAT = re.compile(
        r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
        r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
        r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
        re.IGNORECASE | re.MULTILINE,
    )
    idx = raw.lower().find("safety:")
    if idx < 0:
        return None, None, None, None, None, None
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = PAT.search(seg)
    if not m:
        return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return risk, jb, action, risk_conf, jb_conf, action_conf


def classify_one(adapter, path: str) -> dict:
    """对单条路径分类。adapter = LoadedAdapter 实例。"""
    text = _build_text(path)
    t0 = time.time()
    res = adapter.classify(text)  # 用 adapter 默认 max_new_tokens=40
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
    ap = argparse.ArgumentParser(description="disk_cleanup_conf 推理入口")
    ap.add_argument("paths", nargs="*", help="Windows 文件路径(可多个)")
    ap.add_argument("--file", help="含路径列表的文件")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读路径(每行一条)")
    args = ap.parse_args()

    paths: list[str] = list(args.paths)
    if args.file:
        paths.extend(Path(args.file).read_text(encoding="utf-8").splitlines())
    if args.stdin:
        paths.extend(line.strip() for line in sys.stdin if line.strip())
    if not paths:
        print("ERROR: 没传路径(可用 positional / --file / --stdin)", file=sys.stderr)
        return 1

    # 延迟加载 adapter
    from adapter_registry import get_adapter
    adapter = get_adapter("disk_cleanup_conf")

    print(f"[1/2] 加载 {len(paths)} 条路径,跑 disk_cleanup_conf ...", file=sys.stderr)
    for p in paths:
        if not p.strip():
            continue
        try:
            r = classify_one(adapter, p)
            print(json.dumps(r, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"path": p, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())