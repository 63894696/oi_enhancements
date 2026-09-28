#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# batch_classify.py — M3.69 通用 wrapper:扫目录/文件/日志行 → 任意 spec → 5 类风险报告(2026-09-24)
#
# 目的:
#   - 把 16 个 LoRA adapter 包成通用 CLI 工具
#   - 7 个 sibling 项目(log_triage/perf_alert/safe_exec/cleanup_suggest/email_triage/intent_router/commit_check)
#     都复用本 wrapper(Phase 2)
#   - Phase 1:独立可跑 CLI,Phase 2 才与 PrisirAI 整合
#
# 设计原则:
#   - 零侵入:不修改 companion/ 下任何代码,只 import
#   - 离线:无网络可用(LoRA 已下载到本地)
#   - 通用:同一段逻辑跑任意 spec,按 schema 选 prompt 模板
#   - 输出 5 类分布:CSV / JSON / Markdown 三选一
#
# 使用:
#   from batch_classify import scan_root, scan_file, scan_text, run_batch
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# 把 companion/ 加进 sys.path,这样可以直接 import adapter_registry
_COMPANION = Path(__file__).resolve().parent.parent.parent.parent / "oi_enhancements" / "companion"
# 上溯逻辑:batch_classify/src/batch_classify.py → projects/batch_classify/src/ → projects/ → oi_enhancements/projects/
# 所以 __file__ = .../projects/batch_classify/src/batch_classify.py
# parent.parent.parent.parent = .../oi_enhancements/
# 但实际我们要的是 .../oi_enhancements/companion/
# 修正:__file__ 是 .../projects/batch_classify/src/batch_classify.py,parent.parent.parent = .../projects/batch_classify
# parent.parent.parent.parent = .../projects/ → 再 .parent = .../oi_enhancements/ → /companion
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent  # .../batch_classify
_REPO_ROOT = _PROJECT_ROOT.parent.parent  # .../oi_enhancements
_COMPANION = _REPO_ROOT / "companion"
sys.path.insert(0, str(_COMPANION))


# ------------------------------------------------------------
# 通用解析器(覆盖所有 schema 输出)
# ------------------------------------------------------------
# 所有 schema 的输出形态:
#   Safety: <risk>(:<conf>)?
#   Jailbreak: <yes|no>(:<conf>)?
#   Action: <action>(:<conf>)?     # safety / safety_conf 没有 Action
_PARSE_PAT = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)

RISK_LABELS = ["safe", "low", "medium", "high", "critical"]


def _parse(raw: str) -> dict:
    """从 adapter raw 输出解析成结构化字段。复读时只取第一段。"""
    idx = raw.lower().find("safety:")
    if idx < 0:
        return {
            "risk": None, "risk_conf": None,
            "jailbreak": None, "jb_conf": None,
            "action": None, "action_conf": None,
            "parse_fail": True, "raw": raw,
        }
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = _PARSE_PAT.search(seg)
    if not m:
        return {
            "risk": None, "risk_conf": None,
            "jailbreak": None, "jb_conf": None,
            "action": None, "action_conf": None,
            "parse_fail": True, "raw": raw,
        }
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return {
        "risk": risk if risk in RISK_LABELS else None,
        "risk_conf": risk_conf,
        "jailbreak": "yes" if jb else "no",
        "jb_conf": jb_conf,
        "action": action,
        "action_conf": action_conf,
        "parse_fail": False,
        "raw": raw,
    }


# ------------------------------------------------------------
# spec → prompt 模板(按 schema 分支)
# ------------------------------------------------------------
def _build_text_for_path(spec_name: str, path: str) -> str:
    """对 disk_cleanup / tempfile 类:基于路径 + 元信息组装 prompt。

    对齐各 classify_<spec>.py 的 _build_text(同训练数据形态)。
    """
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

    if spec_name in ("disk_cleanup", "disk_cleanup_conf"):
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
    elif spec_name in ("tempfile", "tempfile_conf"):
        # tempfile 训练数据是 `路径: ...|大小:...|年龄:...|git:...` 单行
        # 但 classify_tempfile 现在也接受 path(从文件名提取)
        return (f"文件路径: {p.name}\n"
                f"扩展名: {p.suffix.lower() or '(无)'}\n"
                f"大小: {size_str}\n"
                f"年龄: {age_days:.0f} 天\n"
                f"问: 这个临时文件是否可以安全删除?")
    else:
        # 兜底:直接当文本
        return path


def _build_text_for_file(spec_name: str, file_path: Path, max_bytes: int = 8192) -> str:
    """对 log / email 类:读文件前 N 字节作 prompt。"""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        text = f"(无法读:{file_path})"

    if spec_name in ("log", "log_conf"):
        return (f"日志来源: {file_path.suffix or 'unknown'}\n"
                f"时间戳: 2026/09/24 14:00:00\n"
                f"级别: info\n"
                f"内容: {text[:500]}\n"
                f"问: 这条日志应该如何分类与处理?")
    elif spec_name in ("email", "email_conf"):
        # 文件当作 body
        return (f"发件人: unknown\n"
                f"主题: {file_path.name}\n"
                f"正文摘要: {text[:200]}\n"
                f"距今: 1 小时\n"
                f"附件: 否\n"
                f"问: 这封邮件应该如何分类与处理?")
    else:
        return text


def _build_text_for_text(spec_name: str, text: str) -> str:
    """直接对文本分类(safety 等)。"""
    if spec_name in ("safety", "safety_conf"):
        return text
    # 其它 spec 用文本当 body,加一行问题
    return f"{text}\n问: 这段内容应该被分类为什么风险?"


# ------------------------------------------------------------
# 5 类概率分布(从 confidence 推断;无 confidence 时退化为 one-hot)
# ------------------------------------------------------------
def _risk_to_distribution(risk: Optional[str], risk_conf: Optional[float]) -> dict:
    """把 (risk, risk_conf) 映射到 5 类概率分布。

    有 confidence(risk_conf ∈ [0,1]):把 mass 摊到 risk 类别,
    其余 4 类均分剩余 (1 - risk_conf) / 4。
    无 confidence:one-hot(risk) = 1.0,其余 0。
    """
    dist = {r: 0.0 for r in RISK_LABELS}
    if risk is None:
        # parse_fail:均匀
        return {r: 0.2 for r in RISK_LABELS}
    if risk_conf is not None and 0 < risk_conf <= 1:
        rest = (1.0 - risk_conf) / 4.0
        for r in RISK_LABELS:
            dist[r] = rest
        dist[risk] = risk_conf
    else:
        dist[risk] = 1.0
    return dist


# ------------------------------------------------------------
# 核心 API
# ------------------------------------------------------------
@dataclass
class ItemResult:
    """单条输入的分类结果。"""
    item_id: str                    # path 或 text 截断
    item_type: str                  # "path" / "text" / "file"
    spec: str                       # 跑哪个 spec
    risk: Optional[str] = None      # safe/low/medium/high/critical
    risk_conf: Optional[float] = None
    action: Optional[str] = None
    jailbreak: Optional[str] = None
    parse_fail: bool = False
    raw: str = ""
    tokens: int = 0
    latency_ms: int = 0
    distribution: dict = field(default_factory=dict)
    size_bytes: int = 0

    def to_row(self) -> dict:
        """给 CSV / JSON 用的扁平字典。"""
        d = self.distribution or {}
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "spec": self.spec,
            "safe": round(d.get("safe", 0.0), 4),
            "low": round(d.get("low", 0.0), 4),
            "medium": round(d.get("medium", 0.0), 4),
            "high": round(d.get("high", 0.0), 4),
            "critical": round(d.get("critical", 0.0), 4),
            "top_risk": self.risk or "parse_fail",
            "action": self.action or "n/a",
            "risk_conf": self.risk_conf,
            "jailbreak": self.jailbreak or "n/a",
            "parse_fail": self.parse_fail,
            "size_bytes": self.size_bytes,
            "latency_ms": self.latency_ms,
        }


def _classify_text(adapter, text: str, spec: str, item_id: str,
                   item_type: str = "text",
                   size_bytes: int = 0) -> ItemResult:
    """核心:对一段文本跑 adapter,返回 ItemResult。"""
    t0 = time.time()
    try:
        res = adapter.classify(text)
    except Exception as e:  # noqa: BLE001
        return ItemResult(
            item_id=item_id, item_type=item_type, spec=spec,
            parse_fail=True, raw=f"[ERROR] {type(e).__name__}: {e}",
            latency_ms=int((time.time() - t0) * 1000),
            size_bytes=size_bytes,
        )
    dt_ms = int((time.time() - t0) * 1000)
    parsed = _parse(res["raw"])
    dist = _risk_to_distribution(parsed["risk"], parsed["risk_conf"])
    risk = parsed["risk"] or "parse_fail"
    return ItemResult(
        item_id=item_id, item_type=item_type, spec=spec,
        risk=risk, risk_conf=parsed["risk_conf"],
        action=parsed["action"], jailbreak=parsed["jailbreak"],
        parse_fail=parsed["parse_fail"],
        raw=parsed["raw"], tokens=res.get("tokens", 0),
        latency_ms=dt_ms, distribution=dist,
        size_bytes=size_bytes,
    )


def scan_path(adapter, spec: str, path: str) -> ItemResult:
    """对单条路径分类(disk_cleanup / tempfile 类)。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    text = _build_text_for_path(spec, path)
    return _classify_text(adapter, text, spec, path, item_type="path",
                          size_bytes=size)


def scan_file(adapter, spec: str, file_path: str) -> ItemResult:
    """对单个文件分类(log / email 类:读文件内容)。"""
    p = Path(file_path)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    text = _build_text_for_file(spec, p)
    return _classify_text(adapter, text, spec, str(p), item_type="file",
                          size_bytes=size)


def scan_text(adapter, spec: str, text: str) -> ItemResult:
    """对单条文本分类(safety / 任意 spec)。"""
    item_id = text[:80] + ("..." if len(text) > 80 else "")
    full = _build_text_for_text(spec, text)
    return _classify_text(adapter, full, spec, item_id, item_type="text",
                          size_bytes=len(text))


def iter_root(root: str, since: Optional[str] = None,
              limit: Optional[int] = None,
              extensions: Optional[list] = None) -> Iterable[Path]:
    """递归遍历目录,产出 Path。

    Args:
        since: "24h" / "7d" / "30m" 之类的时间窗(只取 mtime 在窗口内的文件)
        limit: 最多取多少文件
        extensions: 只取这些扩展名(如 [".log", ".tmp"])
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"目录不存在: {root}")

    cutoff: Optional[float] = None
    if since:
        m = re.match(r"^(\d+)([smhd])$", since.strip().lower())
        if not m:
            raise ValueError(f"--since 格式错: {since!r},应为 like 24h / 7d / 30m")
        n, unit = int(m.group(1)), m.group(2)
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        cutoff = time.time() - n * mult

    count = 0
    for p in root_path.rglob("*"):
        if not p.is_file():
            continue
        if extensions and p.suffix.lower() not in [e.lower() for e in extensions]:
            continue
        if cutoff is not None:
            try:
                if p.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
        yield p
        count += 1
        if limit and count >= limit:
            return


# ------------------------------------------------------------
# 联合多 spec
# ------------------------------------------------------------
def merge_risk(results: list[ItemResult]) -> ItemResult:
    """多 spec 联合:取最高风险(top_risk = argmax over critical > high > ...)。

    入参 results 是同一 item 跑多个 spec 的结果(必须是同一 spec 集合的输出)。
    返回一个 merged ItemResult,distribution 取平均,top_risk 取风险最高的那条。
    """
    if not results:
        raise ValueError("merge_risk: empty results")
    if len(results) == 1:
        return results[0]

    # 风险排序:critical > high > medium > low > safe > parse_fail
    order = {"critical": 5, "high": 4, "medium": 3, "low": 2, "safe": 1, "parse_fail": 0}
    top = max(results, key=lambda r: order.get(r.risk or "parse_fail", 0))

    # distribution 平均
    keys = RISK_LABELS
    merged_dist = {k: 0.0 for k in keys}
    for r in results:
        d = r.distribution or {k: 0.0 for k in keys}
        for k in keys:
            merged_dist[k] += d.get(k, 0.0)
    for k in keys:
        merged_dist[k] /= len(results)

    # risk_conf / parse_fail / latency 取平均或合并
    confs = [r.risk_conf for r in results if r.risk_conf is not None]
    avg_conf = sum(confs) / len(confs) if confs else None

    return ItemResult(
        item_id=top.item_id,
        item_type=top.item_type,
        spec="+".join(sorted({r.spec for r in results})),
        risk=top.risk,
        risk_conf=avg_conf,
        action=top.action,
        jailbreak=top.jailbreak,
        parse_fail=any(r.parse_fail for r in results),
        raw=" || ".join(r.raw[:80] for r in results),
        tokens=sum(r.tokens for r in results),
        latency_ms=sum(r.latency_ms for r in results),
        distribution=merged_dist,
        size_bytes=top.size_bytes,
    )


# ------------------------------------------------------------
# 批跑入口(给 main.py 用)
# ------------------------------------------------------------
def run_batch(
    inputs: list[tuple[str, str]],  # [(kind, value), ...]  kind ∈ {path, file, text}
    specs: list[str],
    dry_run: bool = False,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list[ItemResult]:
    """跑批,返回 ItemResult 列表。

    Args:
        inputs: [(kind, value)] list;kind ∈ {"path", "file", "text"}
        specs: 要跑的 spec 名列表(从 ADAPTERS)
        dry_run: 只跑前 5 条
        on_progress: 进度回调(done, total)
    """
    from adapter_registry import get_adapter, list_scenarios

    if not specs:
        raise ValueError("specs 不能为空")
    available = set(list_scenarios())
    bad = [s for s in specs if s not in available]
    if bad:
        raise KeyError(
            f"未知 spec: {bad}\n"
            f"  可用: {sorted(available)}")

    # 预加载 adapter
    adapters = {s: get_adapter(s) for s in specs}

    if dry_run:
        inputs = inputs[:5]
    total = len(inputs)
    out: list[ItemResult] = []

    for i, (kind, value) in enumerate(inputs, 1):
        per_spec: list[ItemResult] = []
        for spec in specs:
            adapter = adapters[spec]
            try:
                if kind == "path":
                    r = scan_path(adapter, spec, value)
                elif kind == "file":
                    r = scan_file(adapter, spec, value)
                elif kind == "text":
                    r = scan_text(adapter, spec, value)
                else:
                    raise ValueError(f"未知 input kind: {kind}")
                per_spec.append(r)
            except Exception as e:  # noqa: BLE001
                per_spec.append(ItemResult(
                    item_id=value, item_type=kind, spec=spec,
                    parse_fail=True,
                    raw=f"[ERROR] {type(e).__name__}: {e}",
                ))
        # 多 spec → merge
        merged = merge_risk(per_spec) if len(per_spec) > 1 else per_spec[0]
        out.append(merged)
        if on_progress:
            on_progress(i, total)
    return out


# ------------------------------------------------------------
# 输出格式化
# ------------------------------------------------------------
def to_csv(results: list[ItemResult]) -> str:
    """生成 CSV 字符串。"""
    import csv
    import io
    if not results:
        return "path,safe,low,medium,high,critical,top_risk,action\n"
    buf = io.StringIO()
    fieldnames = list(results[0].to_row().keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        writer.writerow(r.to_row())
    return buf.getvalue()


def to_markdown(results: list[ItemResult], title: str = "Batch Classify Report",
                root_desc: str = "") -> str:
    """生成 Markdown 报告。"""
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    if root_desc:
        lines.append(f"**扫描**: {root_desc}  ({len(results)} 条)")
    if results:
        spec_set = sorted({r.spec for r in results})
        lines.append(f"**spec**: {', '.join(spec_set)}")
    lines.append("")

    # 5 类分布
    total = len(results)
    by_risk = {r: 0 for r in RISK_LABELS}
    by_risk["parse_fail"] = 0
    bytes_by_risk = {r: 0 for r in RISK_LABELS}
    bytes_by_risk["parse_fail"] = 0
    for r in results:
        key = r.risk if r.risk in RISK_LABELS else "parse_fail"
        by_risk[key] += 1
        bytes_by_risk[key] += r.size_bytes

    def _fmt_size(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        if b < 1024 * 1024 * 1024:
            return f"{b / 1024 / 1024:.1f} MB"
        return f"{b / 1024 / 1024 / 1024:.2f} GB"

    lines.append("## 5 类分布")
    lines.append("")
    lines.append("| 等级 | 文件数 | 占比 | 体积 |")
    lines.append("|------|--------|------|------|")
    for r in RISK_LABELS + ["parse_fail"]:
        n = by_risk[r]
        pct = n / total * 100 if total else 0
        sz = _fmt_size(bytes_by_risk[r])
        lines.append(f"| {r} | {n} | {pct:.1f}% | {sz} |")
    lines.append("")

    # critical / high 详情(top 20)
    hot = [r for r in results if r.risk in ("critical", "high")]
    hot.sort(key=lambda r: (r.distribution.get(r.risk, 0.0) if r.distribution else 0.0),
             reverse=True)
    if hot:
        lines.append(f"## critical / high 文件 (top 20 / 共 {len(hot)} 条)")
        lines.append("")
        lines.append("| item | risk | safe | low | medium | high | critical |")
        lines.append("|------|------|------|-----|--------|------|----------|")
        for r in hot[:20]:
            d = r.distribution or {}
            lines.append(
                f"| `{r.item_id}` | {r.risk} | "
                f"{d.get('safe', 0):.2f} | {d.get('low', 0):.2f} | "
                f"{d.get('medium', 0):.2f} | {d.get('high', 0):.2f} | "
                f"{d.get('critical', 0):.2f} |"
            )
        lines.append("")

    return "\n".join(lines)


def to_json(results: list[ItemResult], indent: int = 2) -> str:
    """生成 JSON 字符串。"""
    return json.dumps([r.to_row() for r in results],
                      ensure_ascii=False, indent=indent)


# 方便 main.py 调的统一输出函数
def format_output(results: list[ItemResult], fmt: str, **kwargs) -> str:
    if fmt == "csv":
        return to_csv(results)
    if fmt == "md" or fmt == "markdown":
        return to_markdown(results, **kwargs)
    if fmt == "json":
        return to_json(results)
    raise ValueError(f"未知 format: {fmt}")