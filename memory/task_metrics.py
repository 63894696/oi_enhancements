"""task_metrics.py — v0.27 task queue 真用数据采集工具

目的:统计 task queue 实际使用数据,评估 v0.25 承接能力。

指标:
- 提交频次(按天/小时)
- depends_on 使用率(任务带 depends_on 的占比)
- blocked 比例(blocked task 占总 task 的比例)
- retry 触发率(有 RETRY N/ 标记的 task 比例)
- 平均生命周期(submit → done/cancelled 的时间)
- 状态分布(by_status)
- namespace 分布(按 cwd 推断)

用法:
    # 1. 跑一次快照
    python task_metrics.py snapshot

    # 2. 持续监控(每 60s 记一次)
    python task_metrics.py watch --interval 60

    # 3. 报告(累计数据)
    python task_metrics.py report
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.oi_memory import OIMemory  # noqa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v027.task_metrics")

DB_PATH = Path.home() / ".oi" / "memory.db"
METRICS_LOG = Path("C:/Users/Administrator/.claude/projects/C--Users-Administrator/memory/task_metrics.jsonl")


def _query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _all_tasks(namespace_prefix: str = "tasks") -> list[dict]:
    """拉所有 task(从 OI Memory L2 层 + namespace=xxx)"""
    return _query(
        "SELECT id, title, content, namespace, status, depends_on_json, priority, created_at "
        "FROM memories WHERE layer='L2' AND namespace LIKE ? ORDER BY id",
        (f"{namespace_prefix}%",),
    )


def _is_task(m: dict) -> bool:
    return m["title"].startswith("task:") or m.get("namespace", "").startswith("tasks") or m.get("status") in ("pending", "running", "done", "blocked", "cancelled")


def snapshot() -> dict:
    """一次性快照"""
    mem = OIMemory()
    # 走 list_by_layer 拿所有 task(避免 SQL 漏)
    tasks = mem.list_by_layer("L2", limit=1000, namespace="tasks")
    tasks_data = [t.to_dict() if hasattr(t, "to_dict") else {
        "id": t.id, "title": t.title, "content": t.content, "namespace": t.namespace,
        "status": t.status, "depends_on": t.depends_on, "priority": t.priority,
        "created_at": t.created_at, "tags": t.tags,
    } for t in tasks]

    # 统计
    by_status = Counter(t["status"] for t in tasks_data)
    has_depends = [t for t in tasks_data if t.get("depends_on")]
    with_retry = [t for t in tasks_data if "=== RETRY " in (t.get("content") or "")]
    completed = [t for t in tasks_data if t["status"] == "done"]
    blocked = [t for t in tasks_data if t["status"] == "blocked"]

    # 生命周期:done 的 task 从 created_at 到 "=== RESULT ===" 时间
    lifecycles = []
    for t in completed:
        if "=== RESULT ===" in (t.get("content") or ""):
            # 粗略:created_at 到当前时间(因为我们没存 done_at 字段)
            life = time.time() - t["created_at"]
            lifecycles.append(life)
    avg_lifecycle = sum(lifecycles) / len(lifecycles) if lifecycles else 0

    return {
        "snapshot_at": time.time(),
        "snapshot_at_iso": datetime.now().isoformat(),
        "total_tasks": len(tasks_data),
        "by_status": dict(by_status),
        "with_depends_on": len(has_depends),
        "depends_on_pct": round(len(has_depends) / max(len(tasks_data), 1) * 100, 2),
        "with_retry": len(with_retry),
        "retry_pct": round(len(with_retry) / max(len(tasks_data), 1) * 100, 2),
        "blocked_count": len(blocked),
        "blocked_pct": round(len(blocked) / max(len(tasks_data), 1) * 100, 2),
        "completed_count": len(completed),
        "avg_lifecycle_seconds": round(avg_lifecycle, 2),
        "lifecycle_count": len(lifecycles),
    }


def log_snapshot():
    """记一次快照到 task_metrics.jsonl"""
    snap = snapshot()
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    log.info(f"snapshot logged: {snap['total_tasks']} tasks, {snap['by_status']}")
    return snap


def watch(interval: int = 60):
    """持续监控"""
    log.info(f"watching every {interval}s, log to {METRICS_LOG}")
    try:
        while True:
            log_snapshot()
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("stopped")


def report():
    """聚合报告"""
    if not METRICS_LOG.exists():
        print("无 metrics 数据,先跑 snapshot 或 watch")
        return
    snapshots = []
    for line in METRICS_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not snapshots:
        print("metrics log 为空")
        return

    print(f"\n=== v0.27 task queue 报告 ({len(snapshots)} 快照) ===\n")
    first = snapshots[0]
    last = snapshots[-1]
    print(f"时间范围: {first['snapshot_at_iso']} → {last['snapshot_at_iso']}\n")

    print(f"任务总数: {first['total_tasks']} → {last['total_tasks']} (+{last['total_tasks'] - first['total_tasks']})")
    print(f"\n=== 当前 by_status ===")
    for status, count in last['by_status'].items():
        print(f"  {status}: {count}")
    print(f"\n=== 当前指标 ===")
    print(f"  depends_on 使用率: {last['depends_on_pct']}% ({last['with_depends_on']} 条)")
    print(f"  retry 触发率: {last['retry_pct']}% ({last['with_retry']} 条)")
    print(f"  blocked 比例: {last['blocked_pct']}% ({last['blocked_count']} 条)")
    print(f"  completed 累计: {last['completed_count']}")
    if last['avg_lifecycle_seconds']:
        print(f"  平均生命周期: {last['avg_lifecycle_seconds']:.1f}秒")


def main():
    p = argparse.ArgumentParser(description="v0.27 task queue 数据采集")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("snapshot", help="跑一次快照,打到 stdout")

    p_watch = sub.add_parser("watch", help="持续监控")
    p_watch.add_argument("--interval", type=int, default=60)

    sub.add_parser("report", help="聚合报告")

    args = p.parse_args()
    if args.cmd == "snapshot":
        snap = snapshot()
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    elif args.cmd == "watch":
        watch(args.interval)
    elif args.cmd == "report":
        report()


if __name__ == "__main__":
    main()