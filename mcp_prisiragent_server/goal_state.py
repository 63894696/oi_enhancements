"""Goal state machine — unified gate for reviewer/verifier guards.

借鉴 openseek `agent/goal.mbt` 的 GoalCadence 状态机模式:
- 三种状态: MET / CONTINUING / BLOCKED
- 每个 turn 边界做决策评估
- 结构化错误码 + 持久化检查

v0.38 改进: 将 H6/H8 独立守卫统一为 Goal 状态机
"""
from __future__ import annotations

import glob
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any


class GoalStatus(Enum):
    """Goal 状态 — 对标 openseek GoalStatus"""
    MET = "met"                         # 守卫通过，目标达成
    CONTINUING = "continuing"           # 继续中（有剩余工作，需记录摘要）
    BLOCKED = "blocked"                # 受阻（需用户介入或外部状态变更）


class GateResult:
    """守卫检查结果 — 对标 openseek GoalCadence"""
    def __init__(
        self,
        status: GoalStatus,
        gate_name: str,
        task_id: int,
        remaining: str = "",
        reason: str = "",
        report_path: str = "",
        report_chars: int = 0,
    ):
        self.status = status
        self.gate_name = gate_name
        self.task_id = task_id
        self.remaining = remaining    # status=CONTINUING 时必填
        self.reason = reason          # status=BLOCKED 时必填
        self.report_path = report_path
        self.report_chars = report_chars

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "status": self.status.value,
            "gate": self.gate_name,
            "task_id": self.task_id,
        }
        if self.remaining:
            d["remaining"] = self.remaining
        if self.reason:
            d["reason"] = self.reason
        if self.report_path:
            d["report_path"] = self.report_path
        if self.report_chars:
            d["report_chars"] = self.report_chars
        return d


# ── 错误码定义 ──────────────────────────────────────────────────
# 对标 openseek goal decode 的错误码

REVIEWER_ERRORS = {
    "LAZY": "H6_REVIEWER_LAZY",
    "NO_REPORT": "H6_REVIEWER_NO_REPORT",
    "READ_FAIL": "H6_REVIEWER_READ_FAIL",
    "MISSING_FIELDS": "H6_REVIEWER_MISSING_FIELDS",
    "TOO_SHORT": "H6_REVIEWER_TOO_SHORT",
}

VERIFIER_ERRORS = {
    "LAZY": "H8_VERIFIER_LAZY",
    "NO_REPORT": "H8_VERIFIER_NO_REPORT",
    "READ_FAIL": "H8_VERIFIER_READ_FAIL",
    "MISSING_FIELDS": "H8_VERIFIER_MISSING_FIELDS",
    "TOO_SHORT": "H8_VERIFIER_TOO_SHORT",
}


# ── 通用守卫逻辑 ────────────────────────────────────────────────

def _find_recent_reports(
    pattern_prefix: str,
    task_id: int,
    window_seconds: int = 600,
) -> list[tuple[str, float]]:
    """在 C:/temp/ 查找最近创建的报告文件。

    返回 [(path, mtime), ...] 按 mtime 倒序排列。
    """
    cutoff = time.time() - window_seconds
    candidates = []
    # 精确匹配 + 通配符匹配
    for pattern in [
        f"C:/temp/{pattern_prefix}_{task_id}.md",
        f"C:/temp/{pattern_prefix}_*.md",
    ]:
        for p in glob.glob(pattern):
            try:
                mtime = os.path.getmtime(p)
                if mtime >= cutoff:
                    candidates.append((p, mtime))
            except OSError:
                pass
    candidates.sort(key=lambda x: -x[1])
    return candidates


def _read_report(path: str) -> str | None:
    """读取报告文件内容。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"READ_FAIL: {e}"


def _check_required_fields(report: str, required: list[str]) -> list[str]:
    """检查报告包含必需字段。"""
    return [f for f in required if f not in report]


# ── H6: Reviewer Gate ──────────────────────────────────────────

def check_reviewer_gate(
    task_id: int,
    file_ops: int,
    min_report_chars: int = 600,
) -> GateResult:
    """H6 Reviewer 守卫 — 检查 reviewer 是否真做了工作。

    对标 openseek goal 工具:
    - met → GateResult(status=MET)
    - blocked → GateResult(status=BLOCKED, reason=...)

    3 条件检查:
    1. file_ops >= 1 (H2 闸门已保)
    2. 写过 C:/temp/h1b_review_<task_id>.md (报告文件)
    3. 报告含 'spec_drift_rate:' + 'verdict' + 长度 >= 600 chars
    """
    if file_ops == 0:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H6_REVIEWER",
            task_id=task_id,
            reason=REVIEWER_ERRORS["LAZY"],
        )

    candidates = _find_recent_reports("h1b_review", task_id)
    if not candidates:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H6_REVIEWER",
            task_id=task_id,
            reason=REVIEWER_ERRORS["NO_REPORT"],
        )

    report_path, _ = candidates[0]
    report = _read_report(report_path)

    if report is None:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H6_REVIEWER",
            task_id=task_id,
            report_path=report_path,
            reason=REVIEWER_ERRORS["READ_FAIL"],
        )

    missing = _check_required_fields(report, ["spec_drift_rate", "verdict"])
    if missing:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H6_REVIEWER",
            task_id=task_id,
            report_path=report_path,
            report_chars=len(report),
            reason=f"{REVIEWER_ERRORS['MISSING_FIELDS']}: 缺字段 {missing}",
        )

    if len(report) < min_report_chars:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H6_REVIEWER",
            task_id=task_id,
            report_path=report_path,
            report_chars=len(report),
            reason=f"{REVIEWER_ERRORS['TOO_SHORT']}: 报告 {len(report)} chars (<{min_report_chars})",
        )

    return GateResult(
        status=GoalStatus.MET,
        gate_name="H6_REVIEWER",
        task_id=task_id,
        report_path=report_path,
        report_chars=len(report),
    )


# ── H8: Verifier Gate ──────────────────────────────────────────

def check_verifier_gate(
    task_id: int,
    file_ops: int,
    min_report_chars: int = 600,
) -> GateResult:
    """H8 Verifier 守卫 — 检查 verifier 是否真跑了 build。

    对标 openseek goal 工具:
    - met → GateResult(status=MET)
    - blocked → GateResult(status=BLOCKED, reason=...)

    3 条件检查:
    1. file_ops >= 1 (H2 闸门已保)
    2. 写过 C:/temp/orch_verify_<task_id>.md (报告文件)
    3. 报告含 'Build Status' + 'verdict' + 长度 >= 600 chars
    """
    if file_ops == 0:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H8_VERIFIER",
            task_id=task_id,
            reason=VERIFIER_ERRORS["LAZY"],
        )

    candidates = _find_recent_reports("orch_verify", task_id)
    if not candidates:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H8_VERIFIER",
            task_id=task_id,
            reason=VERIFIER_ERRORS["NO_REPORT"],
        )

    report_path, _ = candidates[0]
    report = _read_report(report_path)

    if report is None:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H8_VERIFIER",
            task_id=task_id,
            report_path=report_path,
            reason=VERIFIER_ERRORS["READ_FAIL"],
        )

    missing = _check_required_fields(report, ["Build Status", "verdict"])
    if missing:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H8_VERIFIER",
            task_id=task_id,
            report_path=report_path,
            report_chars=len(report),
            reason=f"{VERIFIER_ERRORS['MISSING_FIELDS']}: 缺字段 {missing}",
        )

    if len(report) < min_report_chars:
        return GateResult(
            status=GoalStatus.BLOCKED,
            gate_name="H8_VERIFIER",
            task_id=task_id,
            report_path=report_path,
            report_chars=len(report),
            reason=f"{VERIFIER_ERRORS['TOO_SHORT']}: 报告 {len(report)} chars (<{min_report_chars})",
        )

    return GateResult(
        status=GoalStatus.MET,
        gate_name="H8_VERIFIER",
        task_id=task_id,
        report_path=report_path,
        report_chars=len(report),
    )


# ── 统一 Goal 状态机 ───────────────────────────────────────────

class GoalMachine:
    """统一 Goal 状态机 — 对标 openseek GoalCadence。

    核心设计:
    - 每个 gate 是一个独立检查器 (check_reviewer_gate / check_verifier_gate)
    - 状态转换: met → 清除 / blocked → 暂停 / continuing → 继续
    - 决策点: task 结束边界调用 evaluate()
    """

    def __init__(self):
        self._results: dict[str, GateResult] = {}

    def add_gate(self, gate_name: str, result: GateResult):
        """添加一个 gate 的检查结果。"""
        self._results[gate_name] = result

    def evaluate(self) -> dict[str, Any]:
        """在所有 gate 完成后做整体评估。

        对标 openseek goal_reminder_text / goal_check_text:
        - 全部 met → 任务完成
        - 有 blocked → 需要用户介入
        - 有 continuing → 继续推进
        """
        if not self._results:
            return {"status": "no_gates", "details": []}

        blocked = [r for r in self._results.values() if r.status == GoalStatus.BLOCKED]
        met = [r for r in self._results.values() if r.status == GoalStatus.MET]

        if blocked:
            return {
                "status": "blocked",
                "blocking_gates": [r.gate_name for r in blocked],
                "reasons": [r.reason for r in blocked],
                "details": [r.to_dict() for r in self._results.values()],
            }

        if met and len(met) == len(self._results):
            return {
                "status": "met",
                "passed_gates": [r.gate_name for r in met],
                "details": [r.to_dict() for r in self._results.values()],
            }

        return {
            "status": "continuing",
            "met": [r.gate_name for r in met],
            "pending": [n for n in self._results if n not in [r.gate_name for r in met]],
            "details": [r.to_dict() for r in self._results.values()],
        }

    def get_gate_result(self, gate_name: str) -> GateResult | None:
        """获取特定 gate 的结果。"""
        return self._results.get(gate_name)

    def reset(self):
        """重置所有 gate 状态。"""
        self._results.clear()


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "goal_check_reviewer",
        "description": (
            "H6 Reviewer 守卫 — 检查 reviewer 是否真做了工作。"
            "3 条件: file_ops>=1 + 报告文件存在 + 含 spec_drift_rate/verdict + ≥600 chars。"
            "对标 openseek GoalCadence met/blocked/continuing 状态机。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"},
                "file_ops": {"type": "integer", "description": "文件操作次数"},
                "min_report_chars": {"type": "integer", "default": 600},
            },
            "required": ["task_id", "file_ops"],
        },
    },
    {
        "name": "goal_check_verifier",
        "description": (
            "H8 Verifier 守卫 — 检查 verifier 是否真跑了 build。"
            "3 条件: file_ops>=1 + 报告文件存在 + 含 Build Status/verdict + ≥600 chars。"
            "对标 openseek GoalCadence met/blocked/continuing 状态机。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"},
                "file_ops": {"type": "integer", "description": "文件操作次数"},
                "min_report_chars": {"type": "integer", "default": 600},
            },
            "required": ["task_id", "file_ops"],
        },
    },
    {
        "name": "goal_evaluate",
        "description": (
            "统一 Goal 状态机评估 — 在所有 gate 完成后做整体决策。"
            "对标 openseek goal_reminder_text / goal_check_text 决策点模式。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "results_json": {"type": "string", "description": "JSON 格式的 gate 结果列表"},
            },
            "required": [],
        },
    },
]


# ── MCP Handler Wrappers ────────────────────────────────────────

def _goal_check_reviewer_impl(
    task_id: int,
    file_ops: int,
    min_report_chars: int = 600,
) -> str:
    """MCP wrapper for check_reviewer_gate."""
    result = check_reviewer_gate(task_id, file_ops, min_report_chars)
    return result.to_json()


def _goal_check_verifier_impl(
    task_id: int,
    file_ops: int,
    min_report_chars: int = 600,
) -> str:
    """MCP wrapper for check_verifier_gate."""
    result = check_verifier_gate(task_id, file_ops, min_report_chars)
    return result.to_json()


def _goal_evaluate_impl(results_json: str = "") -> str:
    """MCP wrapper for GoalMachine.evaluate."""
    import json
    machine = GoalMachine()
    if results_json:
        data = json.loads(results_json)
        for item in data:
            status = GoalStatus(item.get("status", "met"))
            gr = GateResult(
                status=status,
                gate_name=item.get("gate", "unknown"),
                task_id=item.get("task_id", 0),
                remaining=item.get("remaining", ""),
                reason=item.get("reason", ""),
                report_path=item.get("report_path", ""),
                report_chars=item.get("report_chars", 0),
            )
            machine.add_gate(gr.gate_name, gr)
    return json.dumps(machine.evaluate(), ensure_ascii=False, indent=2)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "goal_check_reviewer",
        "description": (
            "H6 Reviewer 守卫 — 检查 reviewer 是否真做了工作。"
            "3 条件: file_ops>=1 + 报告文件存在 + 含 spec_drift_rate/verdict + ≥600 chars。"
            "对标 openseek GoalCadence met/blocked/continuing 状态机。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"},
                "file_ops": {"type": "integer", "description": "文件操作次数"},
                "min_report_chars": {"type": "integer", "default": 600},
            },
            "required": ["task_id", "file_ops"],
        },
    },
    {
        "name": "goal_check_verifier",
        "description": (
            "H8 Verifier 守卫 — 检查 verifier 是否真跑了 build。"
            "3 条件: file_ops>=1 + 报告文件存在 + 含 Build Status/verdict + ≥600 chars。"
            "对标 openseek GoalCadence met/blocked/continuing 状态机。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"},
                "file_ops": {"type": "integer", "description": "文件操作次数"},
                "min_report_chars": {"type": "integer", "default": 600},
            },
            "required": ["task_id", "file_ops"],
        },
    },
    {
        "name": "goal_evaluate",
        "description": (
            "统一 Goal 状态机评估 — 在所有 gate 完成后做整体决策。"
            "对标 openseek goal_reminder_text / goal_check_text 决策点模式。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "results_json": {"type": "string", "description": "JSON 格式的 gate 结果列表"},
            },
            "required": [],
        },
    },
]

HANDLERS = {
    "goal_check_reviewer": _goal_check_reviewer_impl,
    "goal_check_verifier": _goal_check_verifier_impl,
    "goal_evaluate": _goal_evaluate_impl,
}
