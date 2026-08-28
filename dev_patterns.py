# -*- coding: utf-8 -*-
# dev_patterns.py — 员工记忆·成功模式(落地八,2026-08-15)
#
# 定位:dev_lessons 是「错题本」(只记打回);本模块补「老员工手感」(成功怎么做成的)。
# 关键约束:成功自动全记会灌噪声库、稀释错题教训(HarnessBank:泛泛补丁可能负优化)。
# 所以第一步做【半自动】——双闸通过后提炼「可复用做法」为【候选】,挂到待挑队列,
# 由主会话(师傅)挑值得记的才 approve 进 dev_patterns。全自动等验证有效后再放开(第二步)。
#
# 三层员工记忆:
#   L1 项目手感(全员共享)  — namespace "dev_patterns",tag role:all(项目约定/惯用写法)
#   L2 角色技能(按岗位)    — namespace "dev_patterns",tag role:consumer / role:code-implementer
#   L3 复盘错题(现有)      — namespace "dev_lessons"(dev_lessons.py 那套,不动)
#
# 数据流:
#   双闸通过 → propose_success(role,title,task_title,result) 写候选 JSONL(不落库)
#   主会话   → pending() 看候选 → approve(i) 入库 dev_patterns / reject(i) 丢弃
#   上工时   → recall_patterns(query,role,n) 召回该角色成功模式,注入 system
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PATTERN_NAMESPACE = "dev_patterns"
_PENDING = Path.home() / ".local" / "share" / "aureon" / "log" / "dev_pattern_candidates.jsonl"

# 提炼「可复用做法」的关键词信号:产物里出现这些结构,才值得当候选(克制,不泛泛记)
_PATTERN_SIGNALS = (
    "def ", "class ", "import ", "from ", "mojom", "executeScript", "os_crypt",
    "改动文件", "verify", "校验", "回滚", "契约", "接口", "namespace", "分层",
    "先读", "最小 diff", "落盘", "双闸", "复用",
)


def propose_success(role: str, title: str, task_title: str, result: str) -> bool:
    """双闸通过后调用:若产物含可复用信号,写一条候选到待挑队列(不落库)。

    role: "consumer" | "code-implementer" | "all"。返回 True=挂了候选,False=无信号跳过。
    克制原则:无信号的成功不记(泛泛成功入库即噪声)。任何失败静默 False(增强,不阻塞)。
    """
    try:
        if not result or len(result) < 200:
            return False  # 太短没干货,不当候选
        sigs = [s for s in _PATTERN_SIGNALS if s in result]
        if len(sigs) < 2:
            return False  # 信号不足,泛泛成功,不记
        _PENDING.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "role": role,
            "title": title[:120],
            "task_title": task_title[:120],
            "snippet": result[:600],  # 只存片段供师傅判断,不存全文(脱敏)
            "signals": sigs,
        }
        with open(_PENDING, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def _read_pending() -> list[dict]:
    if not _PENDING.exists():
        return []
    out = []
    for line in _PENDING.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def _write_pending(recs: list[dict]) -> None:
    _PENDING.parent.mkdir(parents=True, exist_ok=True)
    with open(_PENDING, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def pending() -> list[dict]:
    """列出待师傅挑的成功模式候选。"""
    return _read_pending()


def approve(index: int, note: str = "") -> bool:
    """师傅挑中第 index 条候选 → 提炼入库 dev_patterns(tag role:xxx + pattern)。"""
    recs = _read_pending()
    if not (0 <= index < len(recs)):
        return False
    rec = recs.pop(index)
    _write_pending(recs)
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from memory.oi_memory import OIMemory  # noqa: PLC0415
        OIMemory().store(
            layer="L2",
            title=f"成功模式·{rec['role']}·{rec['title']}",
            content=(f"角色 {rec['role']} 在「{rec['task_title']}」中验证可行的做法。"
                     f"{('师傅批注: ' + note) if note else ''} 片段: {rec['snippet']}"),
            tags=["dev-pattern", "prisir", f"role:{rec['role']}", "success"],
            namespace=PATTERN_NAMESPACE,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def reject(index: int) -> bool:
    """师傅丢弃第 index 条候选(不值得记)。"""
    recs = _read_pending()
    if not (0 <= index < len(recs)):
        return False
    recs.pop(index)
    _write_pending(recs)
    return True


def recall_patterns(query: str, role: str, n: int = 3) -> str:
    """上工时召回该角色(及全员 role:all)的成功模式,格式化成注入文本。

    只召回本角色 + 全员共享的,不跨角色(避免把 consumer 的出稿手感套给代码工)。
    任何失败静默返回空串 —— 记忆是增强,不是必需。
    """
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from memory.oi_memory import OIMemory  # noqa: PLC0415
        mem = OIMemory()
        hits = mem.recall(query, n=max(n * 3, n), namespace=PATTERN_NAMESPACE)
        if not hits:
            return ""

        def _role(m) -> str:
            t = getattr(m, "tags", None)
            tags = [str(x) for x in t] if isinstance(t, (list, tuple)) else []
            return next((x.split(":", 1)[1] for x in tags if x.startswith("role:")), "")

        # 只留本角色或全员共享的
        hits = [h for h in hits if _role(h) in (role, "all")][:n]
        if not hits:
            return ""
        lines = []
        for h in hits:
            title = getattr(h, "title", "") or ""
            content = getattr(h, "content", "") or ""
            lines.append(f"- {title}: {content[:300]}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def _cli() -> None:
    import sys
    argv = sys.argv[1:]
    if not argv or argv[0] == "pending":
        recs = pending()
        if not recs:
            print("(无待挑成功模式候选)")
            return
        print(f"待挑成功模式候选 {len(recs)} 条:")
        for i, r in enumerate(recs):
            print(f"  [{i}] role={r['role']} | {r['title']}")
            print(f"       信号: {', '.join(r['signals'])} | {r['iso']}")
            print(f"       片段: {r['snippet'][:120]}...")
        print("  → approve: python dev_patterns.py approve <i> [批注] / reject: python dev_patterns.py reject <i>")
    elif argv[0] == "approve":
        note = " ".join(argv[2:]) if len(argv) > 2 else ""
        print("入库" if approve(int(argv[1]), note) else "approve 失败")
    elif argv[0] == "reject":
        print("已丢弃" if reject(int(argv[1])) else "reject 失败")
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
