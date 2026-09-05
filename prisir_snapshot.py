# -*- coding: utf-8 -*-
# prisir_snapshot.py — 文件快照 undo(2026-09-05 P5)
#
# 动机:write_file/edit_file 一旦落盘,模型改错就没法一键回退(对齐 Claude Code
# 的文件快照/undo)。本模块在「每次写/改文件前」把旧内容快照进内存(+可选落盘),
# 提供 undo 工具回滚最近一次或指定路径的改动。
#
# 设计红线:
#   - 纯本地、零 LLM 成本;快照是确定性字符串存取。
#   - 有上限(_MAX_SNAPSHOTS 条、单文件 _MAX_BYTES),防内存膨胀;超出丢最旧。
#   - 只快照「被工具写入前」的状态;新建文件快照为「不存在」标记,undo=删除。
from __future__ import annotations

import os
import threading
import time

_MAX_SNAPSHOTS = 100       # 最多留几条快照(超出丢最旧)
_MAX_BYTES = 2_000_000     # 单文件快照上限(>2MB 不快照,避免内存膨胀)

_LOCK = threading.Lock()
# 每条:{path, existed, content(bytes|None), ts, tool}
_STACK: list[dict] = []


def snapshot_before_write(path: str, tool: str = "write_file") -> None:
    """写/改文件前调用:记录旧状态。>2MB 或读失败时只记 existed 标记(undo 只能删新文件)。"""
    try:
        ap = os.path.abspath(path)
        existed = os.path.isfile(ap)
        content = None
        if existed:
            try:
                if os.path.getsize(ap) <= _MAX_BYTES:
                    with open(ap, "rb") as f:
                        content = f.read()
            except Exception:  # noqa: BLE001
                content = None  # 读不了就只记存在性
        with _LOCK:
            _STACK.append({"path": ap, "existed": existed,
                           "content": content, "ts": time.time(), "tool": tool})
            if len(_STACK) > _MAX_SNAPSHOTS:
                del _STACK[: len(_STACK) - _MAX_SNAPSHOTS]
    except Exception:  # noqa: BLE001 — 快照失败绝不阻塞写
        pass


def _restore(entry: dict) -> str:
    """按一条快照回滚。返回人读结果。"""
    path = entry["path"]
    if not entry["existed"]:
        # 新建文件 → undo = 删除
        if os.path.isfile(path):
            os.remove(path)
            return f"已删除新建文件 {path}"
        return f"{path} 本就不存在(无需回滚)"
    if entry["content"] is None:
        return f"{path} 快照时无法读取旧内容,不能回滚(仅知它存在过)"
    with open(path, "wb") as f:
        f.write(entry["content"])
    return f"已回滚 {path} 到改动前({len(entry['content'])} 字节)"


def undo_last() -> str:
    """回滚最近一次写/改。无快照返回提示。"""
    with _LOCK:
        if not _STACK:
            return "[undo] 没有可回滚的文件改动"
        entry = _STACK.pop()
    try:
        return _restore(entry)
    except Exception as e:  # noqa: BLE001
        return f"[undo error] {type(e).__name__}: {e}"


def undo_path(path: str) -> str:
    """回滚指定路径的最近一次改动。"""
    ap = os.path.abspath(path)
    with _LOCK:
        for i in range(len(_STACK) - 1, -1, -1):
            if _STACK[i]["path"] == ap:
                entry = _STACK.pop(i)
                break
        else:
            return f"[undo] {path} 没有快照记录"
    try:
        return _restore(entry)
    except Exception as e:  # noqa: BLE001
        return f"[undo error] {type(e).__name__}: {e}"


def list_snapshots() -> list[dict]:
    """当前快照栈(新→旧),供 undo 工具展示。"""
    with _LOCK:
        return [
            {"path": e["path"], "tool": e["tool"], "ts": e["ts"],
             "kind": ("新建" if not e["existed"]
                      else ("可回滚" if e["content"] is not None else "仅存在性"))}
            for e in reversed(_STACK)
        ]


def clear() -> None:
    with _LOCK:
        _STACK.clear()
