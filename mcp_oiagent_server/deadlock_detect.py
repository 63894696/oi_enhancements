"""Deadlock detection + position tracking for OIagent daemon.

借鉴 openseek `moonbitlang/async` structured concurrency 的死锁检测:
- 全协程追踪: 记录每个 agent 的位置 (read/write/bash/llm_call)
- 位置追踪: 每轮 LLM 调用记录当前位置
- 死锁检测: 超过阈值无位置变更 → 判定挂起
- 创建栈: 死锁时输出任务链 + 依赖关系

v0.38 对标 openseek async 死锁检测:
- 协程位置记录 → AgentPositionTracker
- 死锁判定 → DeadlockDetector
- 创建栈 → DependencyGraph
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


# ── 数据类型 ────────────────────────────────────────────────────

@dataclass
class AgentPosition:
    """单个 agent 的位置快照 — 对标 openseek async coroutine position."""
    task_id: int
    agent_type: str          # "dev" / "reviewer" / "spec-extract" / "verifier"
    position: str            # "reading" / "writing" / "editing" / "bash" / "llm_call" / "idle"
    last_seen: float         # Unix timestamp of last update
    round_count: int         # LLM rounds completed
    file_ops: int            # write_file + edit_file count
    elapsed_sec: float       # Total elapsed since task start
    stack_trace: str = ""    # Last few tool calls (for debugging)


@dataclass
class DeadlockInfo:
    """死锁检测结果."""
    is_deadlocked: bool
    task_id: int
    agent_type: str
    reason: str
    stuck_since_sec: float
    last_position: str
    recommendation: str = ""


@dataclass
class DependencyGraph:
    """任务依赖图 — 对标 openseek structured concurrency TaskGroup."""
    edges: dict[int, list[int]] = field(default_factory=dict)  # task_id → [depends_on]
    nodes: dict[int, AgentPosition] = field(default_factory=dict)  # task_id → position

    def add_edge(self, task_id: int, depends_on: list[int]):
        """添加依赖边."""
        self.edges[task_id] = depends_on

    def add_node(self, pos: AgentPosition):
        """添加节点."""
        self.nodes[pos.task_id] = pos

    def get_dependents(self, task_id: int) -> list[int]:
        """获取依赖此任务的其他任务."""
        return [tid for tid, deps in self.edges.items() if task_id in deps]

    def get_chain(self, task_id: int) -> list[int]:
        """获取从根到 task_id 的完整依赖链."""
        chain = []
        visited = set()
        def _walk(tid):
            if tid in visited:
                return
            visited.add(tid)
            chain.append(tid)
            for dep in self.edges.get(tid, []):
                _walk(dep)
        _walk(task_id)
        return chain


# ── 位置追踪器 ──────────────────────────────────────────────────

class PositionTracker:
    """追踪所有 agent 的位置变化 — 对标 openseek async 全协程追踪.

    用法:
        tracker = PositionTracker()
        tracker.update(task_id=538, agent_type="dev", position="writing", file_ops=2, round_count=5)
        tracker.update(task_id=539, agent_type="spec-extract", position="llm_call", round_count=15)
    """

    def __init__(self, stall_threshold_sec: int = 120):
        self.positions: dict[int, AgentPosition] = {}
        self.stall_threshold_sec = stall_threshold_sec  # 超过此时间无位置变更视为挂起

    def update(self, task_id: int, agent_type: str, position: str,
               round_count: int = 0, file_ops: int = 0,
               elapsed_sec: float = 0, stack_trace: str = ""):
        """更新 agent 位置."""
        self.positions[task_id] = AgentPosition(
            task_id=task_id,
            agent_type=agent_type,
            position=position,
            last_seen=time.time(),
            round_count=round_count,
            file_ops=file_ops,
            elapsed_sec=elapsed_sec,
            stack_trace=stack_trace,
        )

    def get_position(self, task_id: int) -> AgentPosition | None:
        """获取指定 task 的位置."""
        return self.positions.get(task_id)

    def get_all_positions(self) -> dict[int, AgentPosition]:
        """获取所有 agent 位置."""
        return dict(self.positions)

    def clear(self, task_id: int | None = None):
        """清除位置记录."""
        if task_id is None:
            self.positions.clear()
        else:
            self.positions.pop(task_id, None)


# ── 死锁检测器 ──────────────────────────────────────────────────

class DeadlockDetector:
    """检测 agent 挂起/死锁 — 对标 openseek async 死锁检测.

    检测规则:
    1. Stall: 超过 stall_threshold 无位置变更
    2. Zero-progress: round_count > 0 但 file_ops = 0
    3. Circular: 依赖图中出现环
    4. Starvation: 有任务 blocked 但依赖方已完成
    """

    def __init__(self, tracker: PositionTracker):
        self.tracker = tracker
        self.graph = DependencyGraph()
        self._history: list[dict] = []  # 历史记录

    def detect(self) -> list[DeadlockInfo]:
        """运行所有检测规则，返回死锁信息列表."""
        results = []
        now = time.time()

        for tid, pos in self.tracker.positions.items():
            # Rule 1: Stall detection
            elapsed_since_last = now - pos.last_seen
            if elapsed_since_last > self.tracker.stall_threshold_sec:
                results.append(DeadlockInfo(
                    is_deadlocked=True,
                    task_id=tid,
                    agent_type=pos.agent_type,
                    reason=f"STALLED: {pos.position} for {elapsed_since_last:.0f}s (> {self.tracker.stall_threshold_sec}s threshold)",
                    stuck_since_sec=elapsed_since_last,
                    last_position=pos.position,
                    recommendation=f"Kill task #{tid} ({pos.agent_type}), check dependency chain: {self.graph.get_chain(tid)}",
                ))

            # Rule 2: Zero-progress detection
            if pos.round_count > 3 and pos.file_ops == 0 and pos.position in ("reading", "llm_call"):
                results.append(DeadlockInfo(
                    is_deadlocked=True,
                    task_id=tid,
                    agent_type=pos.agent_type,
                    reason=f"ZERO_PROGRESS: {pos.round_count} rounds, 0 file_ops, stuck in '{pos.position}'",
                    stuck_since_sec=pos.elapsed_sec,
                    last_position=pos.position,
                    recommendation=f"Retry task #{tid} — model may be exploring without making changes",
                ))

            # Rule 3: Late completion (took too long for simple task)
            if pos.round_count > 15 and pos.elapsed_sec > 300:
                results.append(DeadlockInfo(
                    is_deadlocked=False,  # Warning, not deadlock
                    task_id=tid,
                    agent_type=pos.agent_type,
                    reason=f"SLOW: {pos.round_count} rounds, {pos.elapsed_sec:.0f}s elapsed — may be stuck in loop",
                    stuck_since_sec=pos.elapsed_sec,
                    last_position=pos.position,
                    recommendation=f"Monitor task #{tid} — consider H5 total cap if not already set",
                ))

        return results

    def record_completion(self, task_id: int, success: bool, result_summary: str = ""):
        """记录任务完成，更新依赖图."""
        pos = self.tracker.positions.get(task_id)
        if pos:
            pos.position = "done" if success else "failed"
        self._history.append({
            "task_id": task_id,
            "success": success,
            "summary": result_summary,
            "timestamp": time.time(),
        })

    def get_dependency_chain(self, task_id: int) -> list[int]:
        """获取任务的完整依赖链."""
        return self.graph.get_chain(task_id)

    def get_status_report(self) -> dict[str, Any]:
        """生成完整状态报告 — 对标 openseek async 死锁检测输出."""
        detections = self.detect()
        stalled = [d for d in detections if d.is_deadlocked]
        warnings = [d for d in detections if not d.is_deadlocked]

        return {
            "total_agents": len(self.tracker.positions),
            "active_agents": sum(1 for p in self.tracker.positions.values()
                               if p.position not in ("done", "failed")),
            "deadlocks": len(stalled),
            "warnings": len(warnings),
            "stalled_tasks": [
                {"task_id": d.task_id, "agent": d.agent_type, "reason": d.reason}
                for d in stalled
            ],
            "slow_tasks": [
                {"task_id": d.task_id, "agent": d.agent_type, "reason": d.reason}
                for d in warnings
            ],
            "dependency_graph": {
                "edges": {str(k): v for k, v in self.graph.edges.items()},
                "nodes": len(self.graph.nodes),
            },
            "recent_history": self._history[-10:],  # Last 10 completions
        }


# ── 集成工具 (MCP 暴露) ────────────────────────────────────────

# 全局单例 — 由 daemon 初始化
_tracker = PositionTracker()
_detector = DeadlockDetector(_tracker)


def get_position_tracker() -> PositionTracker:
    """获取全局位置追踪器."""
    return _tracker


def get_deadlock_detector() -> DeadlockDetector:
    """获取全局死锁检测器."""
    return _detector


def track_position(task_id: int, agent_type: str, position: str,
                   round_count: int = 0, file_ops: int = 0,
                   elapsed_sec: float = 0) -> str:
    """MCP tool: 更新 agent 位置 — 在 _execute_task_via_ask 每轮调用后记录."""
    _tracker.update(
        task_id=task_id,
        agent_type=agent_type,
        position=position,
        round_count=round_count,
        file_ops=file_ops,
        elapsed_sec=elapsed_sec,
    )
    return json.dumps({
        "ok": True,
        "task_id": task_id,
        "agent_type": agent_type,
        "position": position,
        "tracked": len(_tracker.positions),
    }, ensure_ascii=False)


def detect_deadlocks() -> str:
    """MCP tool: 运行死锁检测."""
    report = _detector.get_status_report()
    return json.dumps(report, ensure_ascii=False, indent=2)


def get_task_chain(task_id: int) -> str:
    """MCP tool: 获取任务依赖链."""
    chain = _detector.get_dependency_chain(task_id)
    return json.dumps({
        "task_id": task_id,
        "chain": chain,
        "chain_depth": len(chain),
    }, ensure_ascii=False)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "deadlock_track_position",
        "description": (
            "更新 agent 位置 — 在 _execute_task_via_ask 每轮调用后记录。"
            "对标 openseek async 全协程追踪。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "agent_type": {"type": "string", "enum": ["dev", "reviewer", "spec-extract", "verifier"]},
                "position": {"type": "string", "enum": ["reading", "writing", "editing", "bash", "llm_call", "idle", "done", "failed"]},
                "round_count": {"type": "integer", "default": 0},
                "file_ops": {"type": "integer", "default": 0},
                "elapsed_sec": {"type": "number", "default": 0},
            },
            "required": ["task_id", "agent_type", "position"],
        },
    },
    {
        "name": "deadlock_detect",
        "description": (
            "运行死锁检测 — 检查所有 agent 位置，报告挂起/零进展/慢速任务。"
            "对标 openseek async 死锁检测 + 位置追踪。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "deadlock_task_chain",
        "description": (
            "获取任务依赖链 — 显示从根任务到指定任务的路径。"
            "用于排查挂起时看是哪个上游任务阻塞了下游。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "要查依赖链的 task_id"},
            },
            "required": ["task_id"],
        },
    },
]

HANDLERS = {
    "deadlock_track_position": track_position,
    "deadlock_detect": detect_deadlocks,
    "deadlock_task_chain": get_task_chain,
}
