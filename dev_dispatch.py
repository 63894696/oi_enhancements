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


if __name__ == "__main__":
    _cli()
