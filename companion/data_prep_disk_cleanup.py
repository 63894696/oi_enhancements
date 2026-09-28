#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_disk_cleanup.py — M3.47 场景 2:C 盘系统清理数据集准备(2026-09-23)
#
# 与 data_prep_tempfile.py 的差别:
#   - 场景不同:不是用户级临时文件,而是 Windows 系统级清理对象
#     (WinSxS 旧组件、DISM 报告、Installer 缓存、DriverStore、Prefetch 等)
#   - 路径专属:C:\Windows\ 下多个安全子目录
#   - 5 类标注更细分:
#     safe     — Prefetch 日志(系统自动回收)
#     low      — 旧的 DISM 报告、LogFiles(可清)
#     medium   — SoftwareDistribution\Download、Installer\$PatchCache$(可清但系统会重建)
#     high     — DriverStore\Temp(驱动安装残留,慎清)
#     critical — CBS.log、DISM 跟踪日志、系统组件备份(删了影响故障排查)
#
# 用法:
#   python data_prep_disk_cleanup.py --output data_disk_cleanup.jsonl [--limit 400]
#
# 决策依据(对应 disk cleanup 工具的规则):
#   safe = 系统定期自动清理(30 天内必清)
#   low  = 文本日志/报告,出问题排查用,平时无用
#   medium = 系统级缓存,删了下次会重建
#   high = 残留/中间产物,删了需手动恢复
#   critical = 关键备份/索引,删了影响系统修复
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Iterator

# ------------------------------------------------------------
# 系统清理专用根目录
# ------------------------------------------------------------
DEFAULT_SCAN_ROOTS = [
    # Windows 系统目录(已在上次硬排除中精确白名单)
    Path("C:/Windows/Temp"),
    Path("C:/Windows/Logs"),
    Path("C:/Windows/SoftwareDistribution/Download"),
    Path("C:/Windows/ServiceProfiles/LocalService/AppData/Local/Temp"),
    Path("C:/Windows/System32/LogFiles"),
    Path("C:/Windows/Prefetch"),
    Path("C:/Windows/debug"),
    Path("C:/Windows/Installer/$PatchCache$"),
    Path("C:/Windows/.old"),
    Path("C:/Windows/WinSxS/Backup"),
    # 额外的 driver store / installer cache
    Path("C:/Windows/Installer"),
    Path("C:/Windows/System32/catroot2"),
    Path("C:/Windows/System32/DriverStore/Temp"),
    Path("C:/Windows/System32/LogFiles/WU"),
    Path("C:/Windows/Logs/WindowsUpdate"),
    Path("C:/Windows/Logs/CBS"),
    Path("C:/Windows/Logs/DISM"),
    Path("C:/Windows/Logs/DPX"),
]

# ------------------------------------------------------------
# 5 类分级规则(场景 2 专用)
# ------------------------------------------------------------

# safe:系统自动回收,删了无副作用
SAFE_DIR_PATTERNS = [
    "prefetch",  # 系统 30 天内必清
]
SAFE_NAME_PARTS = [
    "setupapi",  # setupapi.dev.log 旧的
]
# 某些扩展名本身在 windows 目录 = safe(临时调试输出)
SAFE_EXTS = {".etl.old", ".log.old", ".tmp", ".bak"}

# low:日志/报告,出问题才查
LOW_DIR_PATTERNS = [
    "logs",
    "logfiles",
    "windowsupdate",
    "cbs",
    "dism",
    "dpx",
]
LOW_EXTS = {".log", ".etl", ".xml"}

# medium:缓存,删了下次重建
MEDIUM_DIR_PATTERNS = [
    "softwaredistribution/download",  # Windows Update 下载包
    "$patchcache$",                   # 旧版本 msi 备份
    "installer",                      # msi 安装缓存
]
MEDIUM_EXTS = {".cab", ".msi", ".msp", ".tmp"}

# high:中间产物,删了需手动恢复
HIGH_DIR_PATTERNS = [
    "driverstore/temp",
    "catroot2",
]
HIGH_EXTS = {".cat", ".inf", ".pnf"}

# critical:系统组件索引/关键备份
CRITICAL_DIR_PATTERNS = [
    "winsxs/backup",
    ".old",
    "servicing/backup",
    "servicing/packages",
]
CRITICAL_EXTS = {".mum", ".manifest"}


def _path_parts_lower(path: Path) -> set[str]:
    return {p.lower() for p in path.parts}


def classify(path: Path) -> tuple[str, str]:
    """返 (risk_label, action)。

    DIR_PATTERNS 支持两种形式:
    - 单段名如 'prefetch':parts 里有该段即匹配
    - 多段路径如 'winsxs/backup':parts 里连续两段依次匹配
    """
    parts_lower = [p.lower() for p in path.parts]
    name = path.name.lower()

    def _has_pattern(pat: str) -> bool:
        """单段名 = parts_lower 里出现;多段 / 分隔 = 连续子路径。"""
        segs = pat.split("/")
        if len(segs) == 1:
            return segs[0] in parts_lower
        # 滑动窗口查连续子序列
        n = len(segs)
        for i in range(len(parts_lower) - n + 1):
            if parts_lower[i:i + n] == segs:
                return True
        return False

    # critical 最优先
    for pat in CRITICAL_DIR_PATTERNS:
        if _has_pattern(pat):
            return "critical", "keep"
    if path.suffix.lower() in CRITICAL_EXTS:
        return "critical", "keep"

    # safe
    for pat in SAFE_DIR_PATTERNS:
        if _has_pattern(pat):
            return "safe", "delete"
    if any(p in name for p in SAFE_NAME_PARTS):
        return "safe", "delete"
    if path.suffix.lower() in SAFE_EXTS:
        return "safe", "delete"

    # high
    for pat in HIGH_DIR_PATTERNS:
        if _has_pattern(pat):
            return "high", "review"
    if path.suffix.lower() in HIGH_EXTS:
        return "high", "review"

    # medium
    for pat in MEDIUM_DIR_PATTERNS:
        if _has_pattern(pat):
            return "medium", "review"
    if path.suffix.lower() in MEDIUM_EXTS:
        return "medium", "review"

    # low
    for pat in LOW_DIR_PATTERNS:
        if _has_pattern(pat):
            return "low", "delete"
    if path.suffix.lower() in LOW_EXTS:
        return "low", "delete"

    # 兜底:未知扩展在 windows 目录 = medium
    return "medium", "review"


# ------------------------------------------------------------
# 多样性采样(直接复用 Step 2 的逻辑,简化版)
# ------------------------------------------------------------
RISK_MIN_TARGETS = {
    "safe": 30,
    "low": 80,
    "medium": 80,
    "high": 30,
    "critical": 30,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    rng = random.Random(42)
    by_risk: dict[str, list[dict]] = {}
    for s in samples:
        by_risk.setdefault(s["risk_label"], []).append(s)

    picked: list[dict] = []
    for risk, items in by_risk.items():
        target = RISK_MIN_TARGETS.get(risk, 20)
        if len(items) <= target:
            picked.extend(items)
            continue
        sub_buckets: dict[str, list[dict]] = {}
        for it in items:
            parts = Path(it["_path"]).parts
            # 取顶层目录作 rdir 区分(场景 2 全在 C:\Windows 下,用倒数第二层)
            rdir = parts[-2].lower() if len(parts) >= 5 else "_root"
            sub_buckets.setdefault(rdir, []).append(it)
        total = sum(len(v) for v in sub_buckets.values())
        kept: list[dict] = []
        for rdir, sub_items in sub_buckets.items():
            share = max(int(target * len(sub_items) / total), 5)
            rng.shuffle(sub_items)
            kept.extend(sub_items[:share])
        picked.extend(kept[:target])

    rng.shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 手 walk + 排除目录不下钻
# ------------------------------------------------------------
MAX_DEPTH = 10


def _scan_one(root: Path) -> Iterator[dict]:
    if not root.exists():
        return
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            entries = list(path.iterdir())
        except (PermissionError, OSError, NotADirectoryError):
            continue
        for child in entries:
            try:
                if child.is_dir():
                    stack.append((child, depth + 1))
                    continue
                if not child.is_file():
                    continue
                stat = child.stat()
                risk, action = classify(child)
                now = time.time()
                age_days = (now - stat.st_mtime) / 86400
                yield {
                    "_path": str(child),
                    "_ext": child.suffix.lower(),
                    "_size_bytes": stat.st_size,
                    "_age_days": round(age_days, 1),
                    "risk_label": risk,
                    "_action": action,
                    "_source": f"scan:{root}",
                }
            except (PermissionError, OSError):
                continue


# ------------------------------------------------------------
# 文本字段构造
# ------------------------------------------------------------
def _build_text(s: dict) -> str:
    p = Path(s["_path"])
    parts = p.parts
    if len(parts) >= 5:
        ctx = "\\".join(parts[1:4])
        rel = "\\".join(parts[4:])
        loc = f"{ctx}\\{rel}"
    else:
        loc = str(p)
    age = s["_age_days"]
    size_kb = s["_size_bytes"] / 1024
    size_str = (f"{size_kb:.0f}KB" if size_kb < 1024
                else f"{size_kb/1024:.1f}MB")
    return (f"文件路径: {loc}\n"
            f"扩展名: {s['_ext'] or '(无)'}\n"
            f"大小: {size_str}\n"
            f"年龄: {age:.0f} 天\n"
            f"问: 这个 Windows 系统文件是否可以安全清理?")


def main() -> int:
    ap = argparse.ArgumentParser(description="场景 2: C 盘系统清理训练集准备")
    ap.add_argument("--output", default="data_disk_cleanup.jsonl")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--max-raw", type=int, default=60000)
    ap.add_argument("--roots", nargs="+", default=None,
                    help="扫描根目录列表,默认 16 个 Windows 系统子目录")
    args = ap.parse_args()

    roots = [Path(r) for r in (args.roots or
                                [str(x) for x in DEFAULT_SCAN_ROOTS])]

    print(f"[1/4] 扫描 {len(roots)} 个 Windows 系统子目录")
    raw: list[dict] = []
    t0 = time.time()

    for root in roots:
        try:
            if not root.exists():
                print(f"  - {root} 不存在,跳过")
                continue
        except (PermissionError, OSError) as e:
            print(f"  - {root} 拒访问({type(e).__name__}),跳过")
            continue
        try:
            items = list(_scan_one(root))
            raw.extend(items)
            print(f"  + {root}: {len(items)} 条 (累计 {len(raw)})")
            if len(raw) >= args.max_raw:
                print(f"  ⚠ 达到 --max-raw,停止")
                break
        except Exception as e:
            print(f"  ✗ {root}: {type(e).__name__}")

    print(f"\n  共扫描 {len(raw)} 条,耗时 {time.time()-t0:.1f}s")

    if not raw:
        print("ERROR: 没扫到任何 Windows 系统文件")
        return 1

    print(f"[2/4] 多样性平衡(每类 cap,总 ≤ {args.limit})")
    balanced = _balance_diversity(raw, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    print(f"[3/4] 写盘: {args.output}")
    out = Path(args.output)
    by_risk: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": _build_text(s),
                "risk_label": s["risk_label"],
                "jailbreak_label": False,
                "_action": s["_action"],
                "_path": s["_path"],
                "_ext": s["_ext"],
                "_age_days": s["_age_days"],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            by_risk[s["risk_label"]] = by_risk.get(s["risk_label"], 0) + 1
    print(f"\n  by_risk: {by_risk}")
    print(f"  ✅ 写盘: {out} ({out.stat().st_size/1024:.1f} KB, "
          f"{len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())