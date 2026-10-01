# -*- coding: utf-8 -*-
# dev_dispatch.py — 改代码任务「主会话认领 + consumer 兜底校验」(2026-08-15)
#
# 背景:606/612 连着两次「幻影交付」——报告里贴代码、实际文件没改。根因是 consumer
# 把「产出文本」当交付,没有「必须落盘」约束,也没人验证落没落盘。本模块落地拍板的
# 「1 起步 + 2 兜底」:
#
#   【1 分工】改代码类任务(声明了预期改动文件)派进 namespace="tasks-code",
#            dev-consumer 只消费 "tasks",物理上领不到;由主会话认领实现。
#            纯文本任务(方案/审查/调研)照常进 "tasks" 走 consumer。
#   【2 兜底】consumer 完成任一任务时,若其 content 声明了「改动文件:...」清单,
#            逐一查这些文件是否真的新建/修改了(mtime + 存在性);没真改 → 打回
#            (increment_retry),不标 done。把「幻影交付」变成可检测。
#
# 文件清单约定(派单 content 里写一行):
#   改动文件: mcp_.../llm.py, _backfill_dev_lesson_tags.py
#   (或)预期改动: path/to/a.cc; path/to/b.mojom     —— 逗号/分号/空格分隔
from __future__ import annotations

import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 改代码任务专属 namespace:consumer 不听这个,只有主会话认领。
CODE_NAMESPACE = "tasks-code"

# content 里声明改动文件的标记行(必须是行首声明,正文里描述用「含改动文件」等词不算)。
# 行首 `^` + `re.MULTILINE` 防止正文中「含**改动文件** + 行数」之类被误命中。
_DECL_RE = re.compile(
    r"^\s*(?:改动文件|预期改动|需改文件|修改文件)\s*[:：]\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_declared_files(content: str) -> list[str]:
    """从任务 content 解析「预期改动文件」清单。无声明 → 空列表(=纯文本任务)。

    只识别行首声明(行首以 `改动文件:` 等关键词开头的清单行)。
    正文里出现「含改动文件」等描述性词不会被当作声明。
    """
    for line in (content or "").splitlines():
        m = _DECL_RE.match(line)
        if m:
            raw = m.group(1)
            parts = re.split(r"[,;,、\s]+", raw)
            # 只剥 Markdown 装饰和句读,保留下划线/连字符/点(都是合法文件名字符)
            files = [p.strip().strip("`*。") for p in parts]
            # 只留像文件路径的(含 .扩展名),滤掉「见上文」「无」之类
            files = [f for f in files if re.search(r"\.\w{1,6}$", f) and "/" in f or f.endswith(".py")]
            return files
    return []


def is_code_task(content: str) -> bool:
    """是否改代码类任务(声明了预期改动文件)。"""
    return bool(parse_declared_files(content))


def verify_files_touched(declared: list[str], since_ts: float) -> tuple[bool, list[str]]:
    """兜底校验:声明的文件是否真被新建/修改(mtime >= since_ts,或全新出现)。

    since_ts:任务开始消费的时间。返回 (全部落实?, 未落实文件清单)。
    文件不存在、或 mtime 早于 since_ts(没被这次动过)都算未落实。
    **回退**:如果 ROOT/rel 不存在,尝试 ROOT/custom-hover-translate/rel(主代码仓库在子目录)。
    """
    missing: list[str] = []
    for rel in declared:
        p = ROOT / rel
        if not p.exists():
            # 回退:主代码仓库在 custom-hover-translate 子目录(历史布局)
            fallback = ROOT / "custom-hover-translate" / rel
            if fallback.exists():
                p = fallback
            else:
                missing.append(f"{rel}(不存在)")
                continue
        try:
            if p.stat().st_mtime < since_ts - 2:  # 2s 容差
                missing.append(f"{rel}(未修改)")
        except OSError:
            missing.append(f"{rel}(stat失败)")
    return (not missing), missing


# ─────────────────────────────────────────────────────────────
# 「1」主会话侧:改代码任务派进 tasks-code + 主会话认领
# ─────────────────────────────────────────────────────────────
def submit_code_task(title: str, content: str, files: list[str], priority: int = 5) -> int:
    """派改代码任务:自动在 content 顶部补「改动文件:...」行,派进 tasks-code。

    consumer 只听 "tasks",领不到 tasks-code —— 物理隔离,保证只有主会话认领。
    返回 task_id。

    跳过补行的判定:必须是「行首已有合规声明」(用 parse_declared_files 能解出
    非空清单)才算已声明;content 里出现「含改动文件」之类描述不算,需要补行。
    """
    from memory.task_queue import TaskQueue  # noqa: PLC0415
    decl = "改动文件: " + ", ".join(files)
    if parse_declared_files(content):
        full = content
    else:
        full = decl + "\n" + content
    r = TaskQueue().submit(title=title, content=full,
                           namespace=CODE_NAMESPACE, priority=priority)
    return r["task_id"] if isinstance(r, dict) else int(r)


def list_code_tasks() -> list:
    """列出待主会话认领的改代码任务(tasks-code 里的 ready)。"""
    from memory.task_queue import TaskQueue  # noqa: PLC0415
    try:
        return TaskQueue().list_ready(namespace=CODE_NAMESPACE, limit=50)
    except TypeError:
        return TaskQueue().list_ready()


def _cli() -> None:
    import sys
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    if argv[0] == "list":
        ts = list_code_tasks()
        if not ts:
            print("(tasks-code 无待认领)")
        for t in ts:
            print(f"#{t.id} [p{t.priority}] {t.title}")
            for f in parse_declared_files(getattr(t, "content", "")):
                print(f"    改动: {f}")
    elif argv[0] == "parse":  # 调试:解析一段 content 的声明文件
        print(parse_declared_files(" ".join(argv[1:])))
    else:
        print(__doc__)


# ─────────────────────────────────────────────────────────────
# P5-SwarmTLDR(2026-10-02):借鉴 jcode-swarm-core 派单协议
#
# jcode 的 swarm 派单在 `crates/jcode-swarm-core/src/lib.rs` 顶部定义了一组阈值常量,
# 强制「超长 body 必须带 tldr」「完成报告必含 marker 且长度上限」,用来压住 LLM 派单时
# 「堆长文不带摘要 / 完成报告里乱写」的常见病。本模块摘录这四个核心常量 + 五个
# helper,不做 1000 worker 上限那类与本仓库无关的复杂度。
#
# 借鉴源:`/tmp/jcode-recon/crates/jcode-swarm-core/src/lib.rs` 顶部常量段。
# ─────────────────────────────────────────────────────────────

# 阈值常量(从 jcode-swarm-core lib.rs 顶部常量段摘录)
SWARM_TLDR_REQUIRED_OVER_CHARS = 240
MAX_SWARM_TLDR_CHARS = 200
SWARM_COMPLETION_REPORT_MARKER = "SWARM COMPLETION REPORT REQUIRED"
MAX_SWARM_COMPLETION_REPORT_CHARS = 4000

# 行首锚定(`^` + MULTILINE),与 parse_declared_files 同款防误命中策略。
# 匹配 `tldr: ...`(中英冒号都收),case-insensitive 兼容 `Tldr:` / `TLDR:`。
# tldr 是单行:`.+?` 非贪婪 + `$` 行尾,normalize 时 `re.sub(r"\s+", " ", ...)` 把
# 行内的多空白(含制表符)压成单空格,但**不**跨行(行为与 jcode-swarm-core 一致,
# 那个项目里 tldr 也是单行)。
_TLDR_DECL_RE = re.compile(
    r"^\s*tldr\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_swarm_tldr(content: str) -> str | None:
    """从任务 content 解析 tldr 行(行首以 `tldr:` 开头的单行)。无声明 → None。

    只认行首声明;正文中描述性的「tldr 是...」之类不算。
    """
    if not content:
        return None
    m = _TLDR_DECL_RE.search(content)
    if not m:
        return None
    raw = m.group(1)
    # normalize:trim + 多空白压成单空格(包括换行被压平)
    return re.sub(r"\s+", " ", raw).strip() or None


def make_swarm_tldr(tldr: str) -> str:
    """归一 + 截断。强制仅在派单端调一次,consumer 不重复 normalize。

    步骤:trim → 空白压单空格 → 截到 MAX_SWARM_TLDR_CHARS。
    截断在归一之后做,避免「压空格后字符数比原始预判少」导致漏截。
    """
    if not tldr:
        return ""
    norm = re.sub(r"\s+", " ", tldr).strip()
    if len(norm) > MAX_SWARM_TLDR_CHARS:
        norm = norm[:MAX_SWARM_TLDR_CHARS].rstrip()
    return norm


def validate_swarm_tldr(content: str, tldr: str | None = None) -> tuple[bool, str]:
    """校验派单是否合规。

    规则:
      - body chars > SWARM_TLDR_REQUIRED_OVER_CHARS 时,必须给出 tldr(若未传则自动 parse)
      - tldr 必须 ≤ MAX_SWARM_TLDR_CHARS
      - tldr normalize 后不能为空(防止「tldr:    」这种纯空格骗过去)
      - body ≤ 240 时 tldr 可选(短任务不强求)

    返回 (pass?, reason)。失败 reason 含具体违规项,便于日志排查。
    """
    body = content or ""
    body_chars = len(body)
    if tldr is None:
        tldr = parse_swarm_tldr(body)
    if body_chars <= SWARM_TLDR_REQUIRED_OVER_CHARS:
        # 短 body:tldr 可选,即使给了也不卡上限外的边界(给短 body 也允许写 tldr)
        return True, f"body {body_chars} chars ≤ {SWARM_TLDR_REQUIRED_OVER_CHARS},tldr 可选"
    # 长 body:tldr 必须存在且合规
    if tldr is None:
        return False, (
            f"body {body_chars} chars > {SWARM_TLDR_REQUIRED_OVER_CHARS} 但缺 tldr"
            f"(需在 content 行首声明 `tldr: 简短摘要`)"
        )
    # 先 normalize(空白压单 + trim)但不截断,长度检查在 normalize 之后做,
    # 这样 caller 传超长 tldr 时会 FAIL(要求 caller 显式 make_swarm_tldr 后再校验),
    # 不会「输入 300 字被悄悄截到 200 还假装 pass」。
    normalized = re.sub(r"\s+", " ", tldr).strip()
    if not normalized:
        return False, "tldr 仅含空白,无效"
    if len(normalized) > MAX_SWARM_TLDR_CHARS:
        return False, (
            f"tldr {len(normalized)} chars 超 {MAX_SWARM_TLDR_CHARS} 上限"
            f"(normalize 后: {normalized[:40]}...)"
        )
    return True, f"ok, body={body_chars}chars tldr={len(normalized)}chars"


def validate_completion_report(report: str) -> tuple[bool, str]:
    """校验 consumer 交付的完成报告。

    规则:
      - 必须以 SWARM_COMPLETION_REPORT_MARKER 开头(允许前面有 BOM/空白)
      - 总长度 ≤ MAX_SWARM_COMPLETION_REPORT_CHARS
    """
    if not report:
        return False, "完成报告为空"
    stripped = report.lstrip("﻿").lstrip()  # BOM + 空白容差
    if not stripped.startswith(SWARM_COMPLETION_REPORT_MARKER):
        # 给出前 60 字符片段,便于定位是不是 typo
        head = stripped[:60].replace("\n", "\\n")
        return False, f"缺 marker「{SWARM_COMPLETION_REPORT_MARKER}」(前 60 字符: {head!r})"
    if len(report) > MAX_SWARM_COMPLETION_REPORT_CHARS:
        return False, (
            f"完成报告 {len(report)} chars 超 {MAX_SWARM_COMPLETION_REPORT_CHARS} 上限"
        )
    return True, f"ok, {len(report)} chars"


def build_completion_skeleton(tldr: str, summary: str, files_touched: list[str]) -> str:
    """便捷:构造以 SWARM_COMPLETION_REPORT_MARKER 开头的报告模板。

    三个段都是可选字符串,但 tldr/建议给(便于消费者一眼看到任务意图)。
    files_touched 可空(纯文本/调研任务)。
    """
    tldr_norm = make_swarm_tldr(tldr) if tldr else ""
    parts = [SWARM_COMPLETION_REPORT_MARKER]
    if tldr_norm:
        parts.append(f"tldr: {tldr_norm}")
    if summary:
        parts.append(f"summary: {summary.strip()}")
    if files_touched:
        files_str = ", ".join(files_touched)
        parts.append(f"files_touched: {files_str}")
    return "\n".join(parts)


if __name__ == "__main__":
    _cli()
