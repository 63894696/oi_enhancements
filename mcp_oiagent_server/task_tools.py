"""task_tools.py — v0.25 OIagent 任务接管 MCP tool

4 个 MCP tool:
- task_submit:Claude 写 task 到 OI Memory task queue
- task_status:查 task 详情(按 task_id)
- task_list:按 status/namespace 拉 task 列表
- task_cancel:取消 task

设计原则:
- 返回 dict 不抛异常(MCP handler 包 try/except)
- 错误格式:{"ok": False, "error": "...", "stage": "..."}
- 不重写:复用 memory.task_queue.TaskQueue
- backward compatible:无 namespace 参数时默认 'tasks'
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

logger = __import__("logging").getLogger("mcp_oiagent_server.task_tools")

# 路径常量
OI_ENHANCEMENTS_ROOT = Path("C:/Users/Administrator/oi_enhancements")


def _err(stage: str, exc: Exception) -> str:
    """统一错误格式"""
    return json.dumps({
        "ok": False,
        "error": str(exc),
        "stage": stage,
        "traceback": traceback.format_exc()[:500],
    }, ensure_ascii=False)


def _get_task_queue():
    """延迟 import,避免 MCP server 启动时强制加载 OIMemory"""
    sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
    from memory.task_queue import TaskQueue
    return TaskQueue()


# ============================================================
# 1. task_submit
# ============================================================
def task_submit_impl(
    title: str,
    content: str,
    depends_on: list[int] | None = None,
    priority: int = 0,
    namespace: str = "tasks",
    max_retries: int = 3,
) -> str:
    """Claude 提交 task 到 OI Memory task queue

    Args:
        title: 任务标题(简短)
        content: 任务描述(详细,OIagent 拿到后能直接跑)
        depends_on: 依赖的 task_id 列表
        priority: 数字越大越优先(默认 0)
        namespace: 默认 'tasks'
        max_retries: OIagent 失败重试次数(默认 3)

    Returns:
        {"ok": True, "task_id": int, "status": "pending|blocked"}
    """
    try:
        tq = _get_task_queue()
        result = tq.submit(
            title=title,
            content=content,
            depends_on=depends_on,
            priority=priority,
            namespace=namespace,
            max_retries=max_retries,
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return _err("task_submit", exc)


# ============================================================
# 2. task_status
# ============================================================
def task_status_impl(task_id: int | None = None) -> str:
    """查 task 详情

    Args:
        task_id: 要查的 task_id

    Returns:
        {"ok": True, "task": {...} | None}
    """
    try:
        tq = _get_task_queue()
        if task_id is None:
            return json.dumps({
                "ok": False,
                "error": "task_id 不能为空",
                "stage": "validate",
            }, ensure_ascii=False)
        task = tq.get(task_id)
        if not task:
            return json.dumps({
                "ok": False,
                "error": f"task_id={task_id} 不存在",
                "stage": "get",
            }, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "task": task.to_dict(),
        }, ensure_ascii=False, default=str)
    except Exception as exc:
        return _err("task_status", exc)


# ============================================================
# 3. task_list
# ============================================================
def task_list_impl(
    status: str | None = None,
    ready: bool = False,
    blocked: bool = False,
    namespace: str = "tasks",
    limit: int = 20,
) -> str:
    """拉 task 列表

    Args:
        status: pending/running/done/blocked/cancelled 之一
        ready: True 时返 depends_on 全 done 的 pending task(OIagent 调度入口)
        blocked: True 时返 status=blocked 的 task(Claude 拉取入口)
        namespace: 默认 'tasks'
        limit: 最大返回数(默认 20)

    Returns:
        {"ok": True, "tasks": [...], "count": N, "filter": {...}}
    """
    try:
        tq = _get_task_queue()
        filter_used: dict = {"namespace": namespace, "limit": limit}
        if ready:
            tasks = tq.list_ready(limit=limit, namespace=namespace)
            filter_used["mode"] = "ready"
        elif blocked:
            tasks = tq.list_blocked(limit=limit, namespace=namespace)
            filter_used["mode"] = "blocked"
        elif status:
            tasks = tq.list_by_status(status, limit=limit, namespace=namespace)
            filter_used["status"] = status
        else:
            # 无 filter:全部状态合并
            from memory.oi_memory import TASK_STATUSES
            tasks = []
            for s in TASK_STATUSES:
                tasks.extend(tq.list_by_status(s, limit=limit, namespace=namespace))
            filter_used["mode"] = "all"
        return json.dumps({
            "ok": True,
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks),
            "filter": filter_used,
        }, ensure_ascii=False, default=str)
    except Exception as exc:
        return _err("task_list", exc)


# ============================================================
# 4. task_cancel
# ============================================================
def task_cancel_impl(task_id: int, reason: str = "") -> str:
    """取消 task

    Args:
        task_id: 要取消的 task_id
        reason: 取消原因(append 到 content)

    Returns:
        {"ok": True, "task_id": int, "status": "cancelled"}
    """
    try:
        tq = _get_task_queue()
        result = tq.mark_cancelled(task_id, reason=reason)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return _err("task_cancel", exc)


# ============================================================
# 5. task_mark(辅助 tool:running/done/retry-fail)
# ============================================================
def task_mark_impl(
    task_id: int,
    action: str,
    result: str = "",
    error_msg: str = "",
) -> str:
    """统一 mark 接口:running / done / retry-fail

    Args:
        task_id: 要更新的 task_id
        action: "running" | "done" | "retry-fail"
        result: 当 action=done 时,append 到 content 的 result
        error_msg: 当 action=retry-fail 时,失败原因

    Returns:
        {"ok": True, "task_id": int, "status": ..., "unlocked_tasks": [...]}
    """
    try:
        tq = _get_task_queue()
        if action == "running":
            r = tq.mark_running(task_id)
        elif action == "done":
            r = tq.mark_done(task_id, result=result)
        elif action == "retry-fail":
            r = tq.increment_retry(task_id, error_msg=error_msg)
        else:
            return json.dumps({
                "ok": False,
                "error": f"未知 action: {action},期望 running/done/retry-fail",
                "stage": "validate",
            }, ensure_ascii=False)
        return json.dumps(r, ensure_ascii=False)
    except Exception as exc:
        return _err("task_mark", exc)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────
# 供 dynamic_registry.py 发现，实现 MCP 工具动态注册

TOOL_DEFS = [
    {
        "name": "task_submit",
        "description": (
            "向 OI Memory task queue 提交新 task。"
            "返回 task_id + status(pending/blocked)。"
            "depends_on 数组中的 task 未完成时自动 blocked。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题(简短)"},
                "content": {"type": "string", "description": "任务描述(OIagent 拿到后能直接跑)"},
                "depends_on": {"type": "array", "items": {"type": "integer"}, "description": "依赖的 task_id 列表"},
                "priority": {"type": "integer", "description": "数字越大越优先(默认 0)"},
                "namespace": {"type": "string", "description": "命名空间(默认 'tasks')"},
                "max_retries": {"type": "integer", "description": "失败重试次数(默认 3)"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "task_status",
        "description": "查询指定 task_id 的详情，返回完整 task 数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "要查询的 task_id"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_list",
        "description": "拉取 task 列表，支持按 status/ready/blocked/namespace 过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "running", "done", "blocked", "cancelled"], "description": "按状态过滤"},
                "ready": {"type": "boolean", "description": "True 时返回 depends_on 全完成的 pending task"},
                "blocked": {"type": "boolean", "description": "True 时返回 blocked 的 task"},
                "namespace": {"type": "string", "description": "命名空间(默认 'tasks')"},
                "limit": {"type": "integer", "description": "最大返回数(默认 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "task_cancel",
        "description": "取消指定 task，原因附加到 content。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "要取消的 task_id"},
                "reason": {"type": "string", "description": "取消原因"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_mark",
        "description": "统一 mark 接口: running / done / retry-fail。OIagent 调度用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "要更新的 task_id"},
                "action": {"type": "string", "enum": ["running", "done", "retry-fail"], "description": "动作"},
                "result": {"type": "string", "description": "action=done 时的结果"},
                "error_msg": {"type": "string", "description": "action=retry-fail 时的失败原因"},
            },
            "required": ["task_id", "action"],
        },
    },
]

HANDLERS = {
    "task_submit": task_submit_impl,
    "task_status": task_status_impl,
    "task_list": task_list_impl,
    "task_cancel": task_cancel_impl,
    "task_mark": task_mark_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="v0.25 task_tools standalone CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_sub = sub.add_parser("submit", help="提交 task")
    p_sub.add_argument("title")
    p_sub.add_argument("content")
    p_sub.add_argument("--depends-on", nargs="*", type=int, default=[])
    p_sub.add_argument("--priority", type=int, default=0)

    p_st = sub.add_parser("status", help="查 task")
    p_st.add_argument("task_id", type=int)

    p_ls = sub.add_parser("list", help="列 task")
    p_ls.add_argument("--status", default=None)
    p_ls.add_argument("--ready", action="store_true")
    p_ls.add_argument("--blocked", action="store_true")
    p_ls.add_argument("--limit", type=int, default=20)

    p_can = sub.add_parser("cancel", help="取消 task")
    p_can.add_argument("task_id", type=int)
    p_can.add_argument("--reason", default="")

    p_mk = sub.add_parser("mark", help="mark task")
    p_mk.add_argument("task_id", type=int)
    p_mk.add_argument("action", choices=["running", "done", "retry-fail"])
    p_mk.add_argument("--result", default="")
    p_mk.add_argument("--error-msg", default="")

    args = p.parse_args()
    if args.cmd == "submit":
        print(task_submit_impl(args.title, args.content, args.depends_on, args.priority))
    elif args.cmd == "status":
        print(task_status_impl(args.task_id))
    elif args.cmd == "list":
        print(task_list_impl(status=args.status, ready=args.ready, blocked=args.blocked, limit=args.limit))
    elif args.cmd == "cancel":
        print(task_cancel_impl(args.task_id, args.reason))
    elif args.cmd == "mark":
        print(task_mark_impl(args.task_id, args.action, result=args.result, error_msg=args.error_msg))


if __name__ == "__main__":
    _cli()