"""oiagent 团队协作能力接入(F3):把根目录 oiagent 的任务队列暴露为 PrisirWork 能力。

定位(见 prisirwork-foundation-integration-design §4 / F3):
- 浏览器/agent 经能力门面 submit/list 任务,底层路由到根目录 memory.task_queue。
- 复用不重复造:不新写队列,直接薄封装根目录 OIMemory + TaskQueue(SQLite 持久化)。
- 派单(submit)= L1 内嵌卡确认;查状态(list)= L0 只读。执行仍由主会话/consumer 认领,
  PrisirWork 只暴露「派单 + 查状态」入口,不替团队做执行决策。

红线:OI_HOME 默认 ~/.oi/memory.db;test 用 PRISIR_WORK_CONFIG 同目录隔离,不污染真库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 根目录(oi_enhancements)加入 sys.path,才能 import memory.task_queue。
# prisir_work/ 在根目录下,parent.parent = 根。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TQ = None  # 惰性单例;OI_HOME 在 import memory 前先设好(测试隔离用)


def _queue():
    """惰性构造 TaskQueue。OI_HOME 若被 PRISIR_WORK_OI_HOME 覆盖(测试),先设再 import。"""
    global _TQ
    if _TQ is None:
        oi_home = os.environ.get("PRISIR_WORK_OI_HOME")
        if oi_home:
            os.environ.setdefault("OI_HOME", oi_home)
        from memory.task_queue import TaskQueue
        _TQ = TaskQueue()
    return _TQ


def available() -> bool:
    """探 oiagent 团队栈是否可用(memory 包 + db 可建)。"""
    try:
        _queue()
        return True
    except Exception:
        return False


def submit(title: str, content: str = "", priority: int = 0,
           depends_on: list | None = None, namespace: str = "tasks") -> dict:
    """派单(L1):提交一个任务进 oiagent 队列。返回 {ok, task_id, status}。

    改代码类任务由调用方在 content 里写「改动文件: ...」并派 namespace='tasks-code'
    (走主会话认领);纯文本(方案/审查/调研)派 'tasks'(走 consumer)。本层不替调用方
    决定 namespace,只在缺省时给 'tasks'。
    """
    if not title:
        return {"ok": False, "error": "title_required"}
    try:
        r = _queue().submit(
            title=title, content=content or "",
            depends_on=depends_on or [], priority=int(priority or 0),
            namespace=namespace or "tasks",
        )
        return {"ok": r.get("ok", False), "team": "oiagent", **{k: v for k, v in r.items() if k != "ok"}}
    except Exception as e:
        return {"ok": False, "error": "team_unavailable", "detail": type(e).__name__}


def list_tasks(status: str = "ready", limit: int = 10, namespace: str = "tasks") -> dict:
    """查状态(L0 只读):按 ready/blocked/其他 status 拉任务概要。"""
    try:
        q = _queue()
        if status == "ready":
            items = q.list_ready(limit=int(limit), namespace=namespace)
        elif status == "blocked":
            items = q.list_blocked(limit=int(limit), namespace=namespace)
        else:
            items = q.list_by_status(status, limit=int(limit), namespace=namespace)
        out = [{"task_id": t.id, "title": t.title, "status": t.status,
                "priority": t.priority} for t in items]
        return {"ok": True, "team": "oiagent", "status": status, "count": len(out), "tasks": out}
    except Exception as e:
        return {"ok": False, "error": "team_unavailable", "detail": type(e).__name__}
