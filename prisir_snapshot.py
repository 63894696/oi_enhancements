# -*- coding: utf-8 -*-
# prisir_snapshot.py — 文件快照 undo(2026-09-05 P5) + 持久化版本库(2026-09-16)
#
# 动机:write_file/edit_file 一旦落盘,模型改错就没法一键回退(对齐 Claude Code
# 的文件快照/undo)。本模块在「每次写/改文件前」把旧内容快照进内存栈(fast undo)
# + 落盘版版本库(持久化,重启可查,可回滚到任意历史版本)。
#
# 设计红线:
#   - 纯本地、零 LLM 成本;快照是确定性字符串存取。
#   - 内存栈有上限(_MAX_SNAPSHOTS 条、单文件 _MAX_BYTES),防内存膨胀;超出丢最旧。
#   - 落盘库有版本数上限(_VERSIONS_PER_FILE),按时间/编号淘汰最旧;>2MB 不入库(只记存在性)。
#   - 只快照「被工具写入前」的状态;新建文件快照为「不存在」标记,undo/rollback=删除。
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

_MAX_SNAPSHOTS = 100       # 内存栈最多留几条(超出丢最旧)
_MAX_BYTES = 2_000_000     # 单文件快照上限(>2MB 不快照,避免内存膨胀)
_VERSIONS_PER_FILE = 50    # 落盘库单文件最多存几个版本(超出丢最旧)

_LOCK = threading.Lock()
_LOCK_PERSIST = threading.Lock()
# 每条:{path, existed, content(bytes|None), ts, tool}
_STACK: list[dict] = []


def _data_dir() -> Path:
    root = os.environ.get("PRISIR_DATA_DIR") or str(Path.home() / ".local" / "share" / "prisir")
    return Path(root)


def _versions_root() -> Path:
    """落盘版本库根目录: <data>/file_versions/<sha1(path)>[0:2]/<sha1(path)>/<ts>.bin"""
    return _data_dir() / "file_versions"


def _path_hash(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()


def _version_dir(path: str) -> Path:
    h = _path_hash(path)
    return _versions_root() / h[0:2] / h


def snapshot_before_write(path: str, tool: str = "write_file") -> None:
    """写/改文件前调用: ①入内存栈(fast undo) ②落盘版版本库(持久可回滚)。"""
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
        entry = {"path": ap, "existed": existed,
                 "content": content, "ts": time.time(), "tool": tool}
        with _LOCK:
            _STACK.append(entry)
            if len(_STACK) > _MAX_SNAPSHOTS:
                del _STACK[: len(_STACK) - _MAX_SNAPSHOTS]
    except Exception:  # noqa: BLE001
        pass  # 内存栈失败绝不阻塞写
    # 落盘库失败也绝不阻塞写(独立 try)
    try:
        _persist_version(ap, entry["existed"], entry["content"], entry["ts"], entry["tool"])
    except Exception:  # noqa: BLE001
        pass


def _persist_version(path: str, existed: bool, content: bytes | None,
                     ts: float, tool: str) -> None:
    """落盘一条版本: <vdir>/<ts>.json(元信息) + <ts>.bin(可选内容)。"""
    d = _version_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{ts:.6f}"
    meta = {
        "path": os.path.abspath(path),
        "existed": existed,
        "ts": ts,
        "tool": tool,
        "size": (len(content) if content is not None else 0),
        "has_content": content is not None,
    }
    (d / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if content is not None:
        (d / f"{stem}.bin").write_bytes(content)
    # 超过上限:按 ts 删最旧(保留元信息无内容的也删,减少噪音)
    _trim_versions(d, keep=_VERSIONS_PER_FILE)


def _trim_versions(d: Path, keep: int) -> None:
    try:
        metas = sorted(d.glob("*.json"), key=lambda p: p.name)
        if len(metas) <= keep:
            return
        for old in metas[: len(metas) - keep]:
            stem = old.stem
            bin_p = d / f"{stem}.bin"
            old.unlink(missing_ok=True)
            bin_p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
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
    """当前内存快照栈(新→旧),供 undo 工具展示。"""
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


# ============================================================
# 持久化版本库 API(2026-09-16 #16):重启可查、可回滚到任意历史版本。
# ============================================================

def list_versions(path: str) -> list[dict]:
    """列出某文件的所有历史版本(新→旧)。失败返回 []。"""
    try:
        d = _version_dir(path)
        if not d.is_dir():
            return []
        out = []
        for meta_p in sorted(d.glob("*.json"), key=lambda p: p.name, reverse=True):
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                out.append({
                    "ts": m.get("ts", 0),
                    "tool": m.get("tool", ""),
                    "existed": m.get("existed", False),
                    "size": m.get("size", 0),
                    "has_content": m.get("has_content", False),
                    "version_id": meta_p.stem,  # 时间戳字符串, 用作回滚标识
                })
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


def read_version(path: str, version_id: str) -> str | None:
    """读某版本的完整内容(用于预览)。不存在/失败返 None。"""
    try:
        d = _version_dir(path)
        meta_p = d / f"{version_id}.json"
        if not meta_p.is_file():
            return None
        m = json.loads(meta_p.read_text(encoding="utf-8"))
        if not m.get("has_content"):
            return None
        bin_p = d / f"{version_id}.bin"
        if not bin_p.is_file():
            return None
        # Windows 上文本文件以 CRLF 落盘,预览给前端时统一归一为 LF(更可读),
        # 真正的回滚由 rollback_to 写二进制字节,保持原文件 LF/CRLF 一致。
        text = bin_p.read_bytes().decode("utf-8", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:  # noqa: BLE001
        return None


def rollback_to(path: str, version_id: str) -> str:
    """把 path 回到某历史版本。先做当前快照(防回滚错了再回滚),再覆盖。

    返回人读结果。失败返回错误字符串。"""
    try:
        ap = os.path.abspath(path)
        d = _version_dir(ap)
        meta_p = d / f"{version_id}.json"
        if not meta_p.is_file():
            return f"[rollback] {path} 没有版本 {version_id!r}"
        m = json.loads(meta_p.read_text(encoding="utf-8"))
        if not m.get("has_content"):
            return f"[rollback] 该版本无可读内容(快照时 >2MB 跳过)"
        bin_p = d / f"{version_id}.bin"
        if not bin_p.is_file():
            return f"[rollback] 该版本内容丢失"
        # 1) 落盘前先做一次快照(覆盖前的当前状态也进历史, 便于再回滚)
        snapshot_before_write(ap, "rollback")
        # 2) 写回历史版本内容
        os.makedirs(os.path.dirname(ap) or ".", exist_ok=True)
        with open(ap, "wb") as f:
            f.write(bin_p.read_bytes())
        return f"已回滚 {path} 到 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m['ts']))} 的版本({m.get('size',0)} 字节)"
    except Exception as e:  # noqa: BLE001
        return f"[rollback error] {type(e).__name__}: {e}"


def clear_versions(path: str) -> int:
    """清空某文件的所有历史版本。返回删除条数(失败 0)。"""
    try:
        d = _version_dir(os.path.abspath(path))
        if not d.is_dir():
            return 0
        n = 0
        for p in d.iterdir():
            try:
                p.unlink()
                n += 1
            except Exception:  # noqa: BLE001
                pass
        try:
            d.rmdir()
        except Exception:  # noqa: BLE001
            pass
        return n
    except Exception:  # noqa: BLE001
        return 0
