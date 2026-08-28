"""task_queue.py — v0.25 OIagent 任务队列

设计原则:
- 不重写 OIMemory,只是把 store/recall/list_by_layer 包成 task 友好接口
- Task 存储:OIMemory L2 层 + namespace='tasks' + status/depends_on/priority 字段
- 状态机:pending → running → done / pending → blocked → (Claude 决策) → cancelled 或重试
- depends_on:依赖的 task_id 列表,全 done 才进入 ready(pending)

API:
    from memory.task_queue import TaskQueue, Task
    tq = TaskQueue(OIMemory())
    tid = tq.submit("test", "跑 cognee 验证")  # 返回 task_id
    ready = tq.list_ready(limit=10)  # OIagent 调度入口
    blocked = tq.list_blocked(limit=20)  # Claude 拉取入口
    tq.mark_running(tid)
    tq.mark_done(tid, result="cognee 真跑成功")
    tq.mark_blocked_with_error(tid, "max_retries exceeded")
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Optional

from .oi_memory import OIMemory, TASK_STATUSES, TASK_DEFAULT_STATUS, TASK_NAMESPACE_PREFIX, TASK_TITLE_PREFIX

logger = logging.getLogger("oi_memory.task_queue")


def _err(stage: str, exc: Exception) -> dict:
    """统一错误格式(参 .claude/rules/python.md 错误处理)"""
    return {
        "ok": False,
        "error": str(exc),
        "stage": stage,
        "traceback": traceback.format_exc()[:500],
    }


@dataclass
class Task:
    """Task 数据结构,OIMemory Memory 的 task 友好封装"""
    id: int
    title: str
    content: str
    status: str
    depends_on: list[int]
    priority: int
    namespace: str
    created_at: float
    tags: list[str] = field(default_factory=list)
    # 扩展字段(从 content 解析或单独存)
    retry_count: int = 0
    max_retries: int = 3
    error_msg: str = ""
    result: str = ""  # mark_done 时 append 的结果

    def to_dict(self) -> dict:
        return asdict(self)


class TaskQueue:
    """OI Memory task 队列封装

    设计要点:
    - submit():Claude 写 task,自动判 depends_on 决定初始 status
    - list_ready():OIagent 调度入口,返 depends_on 全 done 的 pending task
    - list_blocked():Claude 拉取入口,返 status=blocked 的 task
    - list_by_status():通用按 status 过滤
    - mark_running/done/blocked:状态机推进
    - recheck_dependencies():task A 变 done 后,扫描所有 blocked 看能否解锁成 pending
    """

    def __init__(self, oi_memory: Optional[OIMemory] = None):
        self.mem = oi_memory or OIMemory()

    # ============================================================
    # 写入侧(Claude 调用)
    # ============================================================
    def submit(
        self,
        title: str,
        content: str,
        depends_on: list[int] | None = None,
        priority: int = 0,
        namespace: str = TASK_NAMESPACE_PREFIX,
        max_retries: int = 3,
        tags: list[str] | None = None,
    ) -> dict:
        """Claude 提交 task

        Returns:
            {"ok": True, "task_id": int, "status": "pending|blocked", "namespace": str}
        """
        try:
            if not title:
                return {"ok": False, "error": "title 不能为空", "stage": "validate"}
            depends_on = depends_on or []

            # depends_on 中有未 done 的 → 初始 status=blocked
            status = TASK_DEFAULT_STATUS
            if depends_on and not self._all_dependencies_done(depends_on):
                status = "blocked"

            # 把 max_retries 存到 tags 后续解析(简化:不另起列)
            tags = tags or ["task"]
            if max_retries != 3:
                tags.append(f"max_retries:{max_retries}")

            full_title = f"{TASK_TITLE_PREFIX}{title}" if not title.startswith(TASK_TITLE_PREFIX) else title
            tid = self.mem.store(
                layer="L2",
                title=full_title,
                content=content,
                tags=tags,
                namespace=namespace,
                status=status,
                depends_on=depends_on,
                priority=priority,
            )
            return {
                "ok": True,
                "task_id": tid,
                "status": status,
                "namespace": namespace,
                "depends_on": depends_on,
                "priority": priority,
            }
        except Exception as exc:
            return _err("submit", exc)

    # ============================================================
    # 读取侧
    # ============================================================
    def list_ready(self, limit: int = 10, namespace: str = TASK_NAMESPACE_PREFIX) -> list[Task]:
        """OIagent 调度入口:返 depends_on 全 done 的 pending task,按 priority DESC"""
        pending = self.mem.list_by_layer("L2", limit=200, namespace=namespace, status="pending")
        ready = []
        for m in pending:
            if self._all_dependencies_done(m.depends_on):
                ready.append(self._to_task(m))
        ready.sort(key=lambda t: (-t.priority, t.created_at))
        return ready[:limit]

    def list_blocked(self, limit: int = 20, namespace: str = TASK_NAMESPACE_PREFIX) -> list[Task]:
        """Claude 拉取入口:返 status=blocked 的 task(等 Claude 决策)"""
        blocked = self.mem.list_by_layer("L2", limit=limit, namespace=namespace, status="blocked")
        return [self._to_task(m) for m in blocked]

    def list_by_status(self, status: str, limit: int = 50, namespace: str = TASK_NAMESPACE_PREFIX) -> list[Task]:
        """按 status 过滤拉取"""
        items = self.mem.list_by_layer("L2", limit=limit, namespace=namespace, status=status)
        return [self._to_task(m) for m in items]

    def get(self, task_id: int) -> Task | None:
        m = self.mem.get_by_id(task_id)
        if not m:
            return None
        return self._to_task(m)

    # ============================================================
    # 状态机推进(OIagent 调用)
    # ============================================================
    def mark_running(self, task_id: int) -> dict:
        try:
            self.mem.update_status(task_id, "running")
            return {"ok": True, "task_id": task_id, "status": "running"}
        except Exception as exc:
            return _err("mark_running", exc)

    def mark_done(self, task_id: int, result: str = "") -> dict:
        """标 done + append result 到 content + 自动 recheck 依赖此 task 的 blocked"""
        try:
            if result:
                self.mem.append_to_content(task_id, f"=== RESULT ===\n{result}")
            self.mem.update_status(task_id, "done")
            # 自动 recheck:找所有 depends_on 包含此 task 的 blocked
            unlocked = self.recheck_dependencies(depended_task_id=task_id)
            return {
                "ok": True,
                "task_id": task_id,
                "status": "done",
                "unlocked_tasks": unlocked,  # 状态从 blocked 变 pending 的 task 列表
            }
        except Exception as exc:
            return _err("mark_done", exc)

    def mark_cancelled(self, task_id: int, reason: str = "") -> dict:
        try:
            if reason:
                self.mem.append_to_content(task_id, f"=== CANCELLED ===\n{reason}")
            self.mem.update_status(task_id, "cancelled")
            return {"ok": True, "task_id": task_id, "status": "cancelled"}
        except Exception as exc:
            return _err("mark_cancelled", exc)

    def increment_retry(self, task_id: int, error_msg: str = "") -> dict:
        """OIagent 跑失败时调:retry_count+1,超 max_retries 自动转 blocked

        Returns:
            {"ok": True, "task_id": int, "retry_count": int, "max_retries": int,
             "status": "running|blocked", "should_retry": bool}
        """
        try:
            task = self.get(task_id)
            if not task:
                return {"ok": False, "error": f"task_id={task_id} 不存在", "stage": "get"}
            new_retry = task.retry_count + 1
            # 把 retry_count + error_msg 写到 content(简化:不另起列)
            suffix = f"=== RETRY {new_retry}/{task.max_retries} ===\n{error_msg}"
            self.mem.append_to_content(task_id, suffix)
            if new_retry >= task.max_retries:
                self.mem.update_status(task_id, "blocked")
                return {
                    "ok": True,
                    "task_id": task_id,
                    "retry_count": new_retry,
                    "max_retries": task.max_retries,
                    "status": "blocked",
                    "should_retry": False,
                    "error_msg": error_msg,
                }
            else:
                # 重试时把 status 重置回 pending(OIagent 重新拉)
                self.mem.update_status(task_id, "pending")
                return {
                    "ok": True,
                    "task_id": task_id,
                    "retry_count": new_retry,
                    "max_retries": task.max_retries,
                    "status": "pending",
                    "should_retry": True,
                }
        except Exception as exc:
            return _err("increment_retry", exc)

    # ============================================================
    # 依赖管理
    # ============================================================
    def recheck_dependencies(self, depended_task_id: int) -> list[int]:
        """当 depended_task_id 变 done 时,扫描所有 blocked 的 task,
        如果它们的 depends_on 现在全 done,自动解锁成 pending。

        Returns:
            解锁的 task_id 列表
        """
        blocked = self.mem.list_by_layer("L2", limit=200, namespace=TASK_NAMESPACE_PREFIX, status="blocked")
        unlocked = []
        for m in blocked:
            if depended_task_id in m.depends_on and self._all_dependencies_done(m.depends_on):
                self.mem.update_status(m.id, "pending")
                unlocked.append(m.id)
        return unlocked

    def _all_dependencies_done(self, depends_on: list[int]) -> bool:
        """检查所有依赖 task 是否 status=done"""
        if not depends_on:
            return True
        for dep_id in depends_on:
            dep = self.mem.get_by_id(dep_id)
            if not dep:
                # 依赖不存在 → 视为未 done(永久 blocked,等 Claude 决策)
                return False
            if dep.status != "done":
                return False
        return True

    # ============================================================
    # 内部辅助
    # ============================================================
    def _to_task(self, m) -> Task:
        """OIMemory Memory → Task dataclass

        从 tags 解析 retry_count / max_retries / result / error_msg(简化方案)
        """
        retry_count = 0
        max_retries = 3
        for tag in m.tags:
            if tag.startswith("max_retries:"):
                try:
                    max_retries = int(tag.split(":", 1)[1])
                except ValueError:
                    pass
        # 从 content 解析 === RETRY n/m === 出现次数
        retry_count = m.content.count("=== RETRY ")
        # 从 content 解析 === RESULT === 和 === CANCELLED ===
        result = ""
        error_msg = ""
        if "=== RESULT ===" in m.content:
            result = m.content.split("=== RESULT ===", 1)[1].strip()
        if "=== CANCELLED ===" in m.content:
            error_msg = m.content.split("=== CANCELLED ===", 1)[1].strip()
        # 拿最后一次 RETRY 的 error_msg
        if "=== RETRY " in m.content:
            parts = m.content.split("=== RETRY ")
            last_retry = parts[-1]
            if "\n" in last_retry:
                error_msg = last_retry.split("\n", 1)[1].strip()
        return Task(
            id=m.id,
            title=m.title,
            content=m.content,
            status=m.status,
            depends_on=m.depends_on,
            priority=m.priority,
            namespace=m.namespace,
            created_at=m.created_at,
            tags=m.tags,
            retry_count=retry_count,
            max_retries=max_retries,
            error_msg=error_msg,
            result=result,
        )


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="v0.25 task_queue standalone CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    # submit
    p_sub = sub.add_parser("submit", help="提交新 task")
    p_sub.add_argument("title")
    p_sub.add_argument("content")
    p_sub.add_argument("--depends-on", nargs="*", type=int, default=[])
    p_sub.add_argument("--priority", type=int, default=0)
    p_sub.add_argument("--namespace", default=TASK_NAMESPACE_PREFIX)
    p_sub.add_argument("--max-retries", type=int, default=3)

    # list
    p_ls = sub.add_parser("list", help="列 task")
    p_ls.add_argument("--status", default=None, help="pending/running/done/blocked/cancelled")
    p_ls.add_argument("--namespace", default=TASK_NAMESPACE_PREFIX)
    p_ls.add_argument("--limit", type=int, default=20)

    # get
    p_get = sub.add_parser("get", help="查 task")
    p_get.add_argument("task_id", type=int)

    # mark
    for action in ("running", "done", "cancelled"):
        p_act = sub.add_parser(f"mark-{action}", help=f"mark task as {action}")
        p_act.add_argument("task_id", type=int)
        if action in ("done", "cancelled"):
            p_act.add_argument("--result", default="", help="result content")

    # retry
    p_retry = sub.add_parser("retry-fail", help="增加 retry_count,失败次数+1")
    p_retry.add_argument("task_id", type=int)
    p_retry.add_argument("--error-msg", default="")

    args = p.parse_args()
    tq = TaskQueue()

    if args.cmd == "submit":
        r = tq.submit(
            title=args.title, content=args.content,
            depends_on=args.depends_on, priority=args.priority,
            namespace=args.namespace, max_retries=args.max_retries,
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "list":
        if args.status == "ready":
            tasks = tq.list_ready(limit=args.limit, namespace=args.namespace)
        elif args.status == "blocked":
            tasks = tq.list_blocked(limit=args.limit, namespace=args.namespace)
        elif args.status:
            tasks = tq.list_by_status(args.status, limit=args.limit, namespace=args.namespace)
        else:
            tasks = [tq.get(t.id) for t in []]
            # 全部 status 都拉
            for s in TASK_STATUSES:
                tasks.extend(tq.list_by_status(s, limit=args.limit, namespace=args.namespace))
        print(json.dumps([t.to_dict() for t in tasks], ensure_ascii=False, indent=2))
    elif args.cmd == "get":
        t = tq.get(args.task_id)
        print(json.dumps(t.to_dict() if t else {"error": "not_found"}, ensure_ascii=False, indent=2))
    elif args.cmd.startswith("mark-"):
        action = args.cmd[5:]
        if action == "running":
            r = tq.mark_running(args.task_id)
        elif action == "done":
            r = tq.mark_done(args.task_id, result=args.result)
        elif action == "cancelled":
            r = tq.mark_cancelled(args.task_id, reason=args.result)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "retry-fail":
        r = tq.increment_retry(args.task_id, error_msg=args.error_msg)
        print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()