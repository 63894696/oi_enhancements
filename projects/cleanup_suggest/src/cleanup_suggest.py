"""cleanup_suggest 核心模块 — M3.69 (2026-09-24)

功能:
- 扫目录得到文件清单
- 双 spec 联合判定(tempfile + disk_cleanup_conf,取两者最高 risk)
- 输出 5 类风险分布 + 删除建议

设计:
- 零侵入:不修改 companion/ 下任何代码
- 离线可用:仅用本地 Qwen3Guard-Gen-0.6B + 两个 LoRA adapter
- 双 spec 联合:top_risk = max(risk_disk_cleanup, risk_tempfile)
- 安全第一:默认仅生成建议,需 --auto-apply --yes 才执行删除
- 删 file 用 send2trash (回收站),不永久删
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# companion/ 在 oi_enhancements/ 下(项目根的祖父),用于 import classify_*
# cleanup_suggest/src/cleanup_suggest.py  →  oi_enhancements/companion/
#   _HERE                                  = cleanup_suggest/src/  (module dir)
#   _HERE.parent                           = cleanup_suggest/
#   _HERE.parent.parent                    = projects/
#   _HERE.parent.parent.parent             = oi_enhancements/    ← 伴伴目录在这里
_HERE = Path(__file__).resolve().parent
_COMPANION_DIR = _HERE.parent.parent.parent / "companion"

if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))

from classify_disk_cleanup import classify_one as disk_cleanup_classify_one  # noqa: E402
from classify_tempfile import classify_tempfile as tempfile_classify_one  # noqa: E402

# 风险等级顺序(数值越大越危险)
RISK_ORDER = {"safe": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
RISK_LEVELS = ["safe", "low", "medium", "high", "critical"]
ACTION_LABELS = {"delete", "review", "keep"}


@dataclass
class FileVerdict:
    """单文件判定结果。"""
    path: str
    size_bytes: int
    age_days: float
    risk: str = "unknown"
    action: Optional[str] = None
    risk_conf: Optional[float] = None
    top_risk_source: str = "unknown"
    disk_cleanup_raw: Optional[dict] = None
    tempfile_raw: Optional[dict] = None
    parse_failed: bool = False
    error: Optional[str] = None


@dataclass
class SuggestionReport:
    """扫描+判定+建议的总报告。"""
    root: str
    scanned_files: int = 0
    scanned_size_bytes: int = 0
    parse_fail_count: int = 0
    verdicts: list = field(default_factory=list)
    distribution: dict = field(default_factory=dict)
    suggestion: dict = field(default_factory=dict)
    applied: bool = False
    deleted_files: list = field(default_factory=list)
    failed_deletes: list = field(default_factory=list)
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict:
        """输出 JSON-friendly 字典。"""
        return {
            "root": self.root,
            "scanned_files": self.scanned_files,
            "scanned_size_gb": round(self.scanned_size_bytes / 1024**3, 3),
            "parse_fail_count": self.parse_fail_count,
            "distribution": self.distribution,
            "suggestion": self.suggestion,
            "applied": self.applied,
            "deleted_files_count": len(self.deleted_files),
            "failed_deletes_count": len(self.failed_deletes),
            "elapsed_sec": round(self.elapsed_sec, 2),
        }


def _take_top_risk(risk_a: Optional[str], risk_b: Optional[str]) -> tuple[str, str]:
    """取两者中风险等级最高者。返回 (top_risk, source)。

    source: "disk_cleanup" / "tempfile" / "none"。
    若两者都 unknown/None,返回 ("unknown", "none")。
    """
    candidates = []
    if risk_a and risk_a in RISK_ORDER:
        candidates.append(("disk_cleanup", risk_a))
    if risk_b and risk_b in RISK_ORDER:
        candidates.append(("tempfile", risk_b))
    if not candidates:
        return "unknown", "none"
    candidates.sort(key=lambda x: RISK_ORDER[x[1]], reverse=True)
    return candidates[0][1], candidates[0][0]


def scan_directory(
    root: Path,
    limit: Optional[int] = None,
    progress_cb=None,
) -> list[Path]:
    """遍历 root 下所有文件路径(递归)。返回 Path 列表。

    Args:
        root: 扫描根目录。
        limit: 最多返回 N 个文件,None = 不限。
        progress_cb: 可选回调 fn(count, total_est)。total_est 不准(实际文件数未知),
                     所以只用来做心跳。
    """
    if not root.exists():
        raise FileNotFoundError(f"路径不存在: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"不是目录: {root}")

    files: list[Path] = []
    # rglob 会递归;用 iterdir + 递归避免一次 collect 全量
    try:
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    files.append(p)
                    if progress_cb and len(files) % 100 == 0:
                        progress_cb(len(files))
                    if limit is not None and len(files) >= limit:
                        break
            except (OSError, PermissionError):
                # 单个文件 stat 失败,跳过(权限/被占用等)
                continue
    except (OSError, PermissionError) as e:
        # 整目录遍历权限不足
        print(f"[WARN] 部分子目录无法访问: {e}", file=sys.stderr)
    return files


def _build_tempfile_text(path: Path) -> str:
    """拼 tempfile spec 期望的紧凑单行输入。"""
    try:
        stat = path.stat()
        size_kb = stat.st_size / 1024
        size_str = (f"{size_kb:.0f}KB" if size_kb < 1024
                    else f"{size_kb/1024:.1f}MB")
        age_days = (time.time() - stat.st_mtime) / 86400
    except (OSError, FileNotFoundError):
        size_str = "?"
        age_days = 0
    # 训练 schema 单行格式:路径|大小|年龄|git
    return f"{path}|{size_str}|{age_days:.0f} 天|未知"


def classify_one_file(
    path: Path,
    disk_cleanup_adapter,
    tempfile_adapter,
) -> FileVerdict:
    """对单个文件跑双 spec 联合判定。"""
    try:
        stat = path.stat()
        size_bytes = stat.st_size
        age_days = (time.time() - stat.st_mtime) / 86400
    except (OSError, FileNotFoundError) as e:
        return FileVerdict(
            path=str(path),
            size_bytes=0,
            age_days=0,
            error=f"stat 失败: {e}",
        )

    verdict = FileVerdict(
        path=str(path),
        size_bytes=size_bytes,
        age_days=age_days,
    )

    # disk_cleanup 判定
    try:
        d_res = disk_cleanup_classify_one(disk_cleanup_adapter, str(path))
        verdict.disk_cleanup_raw = d_res
        risk_d = d_res.get("risk")
        if risk_d and risk_d in RISK_ORDER:
            verdict.parse_failed = verdict.parse_failed or False
    except Exception as e:  # noqa: BLE001
        verdict.disk_cleanup_raw = {"error": f"{type(e).__name__}: {e}"}
        risk_d = None
        verdict.parse_failed = True

    # tempfile 判定
    try:
        t_input = _build_tempfile_text(path)
        t_res = tempfile_classify_one(tempfile_adapter, t_input)
        verdict.tempfile_raw = t_res
        risk_t = t_res.get("risk")
    except Exception as e:  # noqa: BLE001
        verdict.tempfile_raw = {"error": f"{type(e).__name__}: {e}"}
        risk_t = None
        verdict.parse_failed = True

    top_risk, source = _take_top_risk(risk_d, risk_t)
    verdict.risk = top_risk
    verdict.top_risk_source = source

    # confidence 取自 source
    if source == "disk_cleanup" and verdict.disk_cleanup_raw:
        verdict.risk_conf = verdict.disk_cleanup_raw.get("risk_conf")
    elif source == "tempfile" and verdict.tempfile_raw:
        verdict.risk_conf = verdict.tempfile_raw.get("risk_conf")

    return verdict


def build_distribution(verdicts: list[FileVerdict]) -> dict:
    """统计 5 类风险分布(只统计 parse 成功的)。"""
    dist: dict[str, dict] = {}
    total = len(verdicts)
    for level in RISK_LEVELS:
        items = [v for v in verdicts if v.risk == level]
        count = len(items)
        size_bytes = sum(v.size_bytes for v in items)
        dist[level] = {
            "count": count,
            "size_gb": round(size_bytes / 1024**3, 3),
            "pct": round(100 * count / total, 1) if total else 0.0,
        }
    return dist


def build_suggestion(verdicts: list[FileVerdict]) -> dict:
    """根据分布生成删除建议。"""
    safe_low_files = [v for v in verdicts if v.risk in ("safe", "low")]
    medium_files = [v for v in verdicts if v.risk == "medium"]
    high_crit = [v for v in verdicts if v.risk in ("high", "critical")]

    return {
        "auto_delete_safe_low": {
            "count": len(safe_low_files),
            "size_gb": round(sum(v.size_bytes for v in safe_low_files) / 1024**3, 3),
        },
        "medium_user_confirm": {
            "count": len(medium_files),
            "size_gb": round(sum(v.size_bytes for v in medium_files) / 1024**3, 3),
        },
        "skip_high_critical": {
            "count": len(high_crit),
            "size_gb": round(sum(v.size_bytes for v in high_crit) / 1024**3, 3),
        },
    }


def run_scan_and_classify(
    root: str,
    disk_cleanup_adapter,
    tempfile_adapter,
    limit: Optional[int] = None,
    progress_cb=None,
) -> SuggestionReport:
    """端到端:扫 + 双 spec 分类 + 生成报告。"""
    t0 = time.time()
    root_path = Path(root)

    if not root_path.exists():
        raise FileNotFoundError(f"路径不存在: {root}")

    files = scan_directory(root_path, limit=limit, progress_cb=progress_cb)

    report = SuggestionReport(root=str(root_path))
    report.scanned_files = 0
    report.scanned_size_bytes = 0

    for idx, fp in enumerate(files, 1):
        v = classify_one_file(fp, disk_cleanup_adapter, tempfile_adapter)
        report.verdicts.append(v)
        report.scanned_files += 1
        report.scanned_size_bytes += v.size_bytes
        if v.parse_failed:
            report.parse_fail_count += 1

        if progress_cb and idx % 50 == 0:
            progress_cb(idx)

    report.distribution = build_distribution(report.verdicts)
    report.suggestion = build_suggestion(report.verdicts)
    report.elapsed_sec = time.time() - t0
    return report


def apply_deletion(
    report: SuggestionReport,
    *,
    auto_safe_low: bool = False,
    user_confirmed_medium: bool = False,
) -> tuple[list[dict], list[dict]]:
    """按报告执行。

    - auto_safe_low=True:删 safe/low
    - user_confirmed_medium=True:删 medium
    - 任何情况下都不删 high/critical
    Returns: (deleted, failed) 两份 list,每元素 = {path, reason?}
    """
    from send2trash import send2trash

    to_delete = []
    for v in report.verdicts:
        if v.risk in ("safe", "low") and auto_safe_low:
            to_delete.append(v)
        elif v.risk == "medium" and user_confirmed_medium:
            to_delete.append(v)
        # high/critical: 永不删

    deleted = []
    failed = []
    for v in to_delete:
        try:
            send2trash(v.path)
            deleted.append({"path": v.path, "risk": v.risk, "size_bytes": v.size_bytes})
        except Exception as e:  # noqa: BLE001
            failed.append({
                "path": v.path,
                "risk": v.risk,
                "reason": f"{type(e).__name__}: {e}",
            })

    report.deleted_files = deleted
    report.failed_deletes = failed
    report.applied = bool(deleted) or bool(failed)
    return deleted, failed


def print_text_report(report: SuggestionReport, show_top_n: int = 5) -> None:
    """人类可读的中文报告(stdout)。"""
    print()
    print(f"扫描: {report.root}  ({report.scanned_size_bytes / 1024**3:.2f} GB / "
          f"{report.scanned_files} 文件)")
    if report.parse_fail_count:
        print(f"      注: {report.parse_fail_count} 个文件 parse 失败(模型未给出 risk),"
              f"统一标记为 unknown,默认保留")
    print()
    print("[清理分析] tempfile + disk_cleanup_conf 双 spec 判定:")
    print()
    print("  5 类分布:")
    icons = {"safe": "  ", "low": "  ", "medium": "  ⚠️",
             "high": "  ⚠️", "critical": "  🔒 保留"}
    for level in RISK_LEVELS:
        d = report.distribution.get(level, {"count": 0, "size_gb": 0.0, "pct": 0.0})
        count = d["count"]
        if count == 0:
            continue
        size_gb = d["size_gb"]
        pct = d["pct"]
        print(f"    {level:<8} {count:>6} ({pct:>5.1f}%, {size_gb:.3f} GB){icons[level]}")
    print()
    print("  建议:")
    s = report.suggestion
    auto = s["auto_delete_safe_low"]
    med = s["medium_user_confirm"]
    skip = s["skip_high_critical"]
    print(f"    🟢 删除 safe + low = {auto['count']} 文件,"
          f"释放 {auto['size_gb']:.2f} GB")
    print(f"    🟡 medium 高价值,可清理 {med['size_gb']:.2f} GB"
          f"({med['count']} 文件,需 user 确认)")
    print(f"    🔴 high/critical 跳过 ({skip['count']} 文件,"
          f"{skip['size_gb']:.3f} GB)")
    print()

    if show_top_n > 0 and report.verdicts:
        print(f"  Top {show_top_n} 高风险文件预览:")
        high_risks = sorted(
            [v for v in report.verdicts if v.risk in ("high", "critical")],
            key=lambda v: (-RISK_ORDER.get(v.risk, 0), -v.size_bytes),
        )[:show_top_n]
        for v in high_risks:
            try:
                rel = Path(v.path).relative_to(report.root)
            except ValueError:
                rel = Path(v.path).name
            print(f"    [{v.risk:<8}] {rel}  ({v.size_bytes / 1024:.0f} KB, "
                  f"{v.age_days:.0f} 天, source={v.top_risk_source})")
        print()


def load_adapters():
    """懒加载双 adapter。返回 (disk_cleanup_adapter, tempfile_adapter)。

    用 disk_cleanup_conf 与 tempfile_conf(均带 calibrated prob)。
    """
    from adapter_registry import get_adapter

    print("[init] 加载 disk_cleanup_conf adapter ...", file=sys.stderr)
    disk_adapter = get_adapter("disk_cleanup_conf")
    print("[init] 加载 tempfile_conf adapter ...", file=sys.stderr)
    tmp_adapter = get_adapter("tempfile_conf")
    return disk_adapter, tmp_adapter