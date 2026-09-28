"""
prisir_work/agent_handoff.py — Multi-agent handoff schema(Phase 9 MA-P1, 2026-09-28)。

承接 [[prisIr-openmontage-recon]] 后的多 agent 借鉴决策:
  「按最小 agent 团队设计,借鉴 OpenAI Agents SDK 的 handoffs + guardrails 设计模式」

## 定位
为 PrisirAI 的最小 agent 团队(Triage / Creative / Art / Audio / Edit / Ops)提供
**标准 handoff 协议**。借鉴 OpenAI Agents SDK 的两个原语:
  · handoffs — agent 之间交接任务
  · guardrails — 输入/输出校验,失败 tripwire 中止

**不接整 SDK**(虽然 MIT 可用),仅借鉴 schema 设计,避免外部依赖。

## 关键设计
1. **HandoffPayload 必须含完整 schema**(借鉴 OpenAI Agents SDK handoff):
   - task_id, original_goal, current_state
   - completed_steps, pending_steps, constraints
   - accumulated_context(精简后)
   - trace_id(跨 agent 唯一)
2. **ACCEPTED / REJECTED 必须显式返** — 接收方 agent 必须表态
3. **handoff caps** — 每次请求 handoff 数 ≤ 5(防「两 agent 互踢」死循环)
4. **超时** — 单步 handoff 默认 60s,超时降级
5. **escalate to human** — handoff 超 cap 或失败 → 终止分支,转人

## 关键 API
  - HandoffPayload dataclass — 标准 schema
  - accept_handoff(payload) → HandoffResult(accepted=True)
  - reject_handoff(payload, reason) → HandoffResult(accepted=False, reason=...)
  - HandoffCaps — 计数 + 超限抛 HandoffCapExceeded
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

__all__ = [
    "HandoffPayload",
    "HandoffResult",
    "HandoffAccept",
    "HandoffReject",
    "HandoffCaps",
    "HandoffCapExceeded",
    "accept_handoff",
    "reject_handoff",
    "DEFAULT_HANDOFF_CAP",
    "DEFAULT_HANDOFF_TIMEOUT_SEC",
]


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_HANDOFF_CAP = 5  # 每次请求最多 5 次 handoff
DEFAULT_HANDOFF_TIMEOUT_SEC = 60.0  # 单步 handoff 超时


# ---------------------------------------------------------------------------
# HandoffPayload — 标准 handoff schema
# ---------------------------------------------------------------------------

@dataclass
class HandoffPayload:
    """agent 间交接的标准 payload(借鉴 OpenAI Agents SDK HandoffInputData)。

    字段:
      task_id         本次任务的唯一 ID(workflow 级)
      trace_id        本次 handoff 链路的 trace ID(跨 agent 唯一)
      original_goal   用户原始目标(不可变)
      current_state   当前状态描述(谁手上 / 进度)
      completed_steps 已完成的子任务列表
      pending_steps   待完成的子任务列表
      constraints     约束条件(deadline / 预算 / 不可逆操作标记 等)
      accumulated_context  累计上下文(精简后,不给就空 dict)
      from_agent      发送方 agent name
      to_agent        接收方 agent name
      created_at      ISO 时间戳
      timeout_sec     单步超时(默认 60s)
    """
    task_id: str
    trace_id: str
    original_goal: str
    from_agent: str
    to_agent: str
    current_state: str = ""
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    accumulated_context: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    timeout_sec: float = DEFAULT_HANDOFF_TIMEOUT_SEC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HandoffPayload":
        return cls(
            task_id=d["task_id"],
            trace_id=d["trace_id"],
            original_goal=d["original_goal"],
            from_agent=d["from_agent"],
            to_agent=d["to_agent"],
            current_state=d.get("current_state", ""),
            completed_steps=list(d.get("completed_steps") or []),
            pending_steps=list(d.get("pending_steps") or []),
            constraints=dict(d.get("constraints") or {}),
            accumulated_context=dict(d.get("accumulated_context") or {}),
            created_at=d.get("created_at", time.time()),
            timeout_sec=float(d.get("timeout_sec", DEFAULT_HANDOFF_TIMEOUT_SEC)),
        )

    def is_expired(self) -> bool:
        """是否已超时。"""
        return (time.time() - self.created_at) > self.timeout_sec


# ---------------------------------------------------------------------------
# HandoffResult — ACCEPTED / REJECTED
# ---------------------------------------------------------------------------

@dataclass
class HandoffResult:
    """handoff 接收方返的结果。"""
    accepted: bool
    task_id: str
    trace_id: str
    to_agent: str
    reason: str = ""  # rejected 时必填
    new_payload: Optional[HandoffPayload] = None  # accepted 时可附新 payload 转交

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "to_agent": self.to_agent,
            "reason": self.reason,
            "new_payload": self.new_payload.to_dict() if self.new_payload else None,
        }


def accept_handoff(payload: HandoffPayload,
                   *, new_payload: Optional[HandoffPayload] = None) -> HandoffResult:
    """返 ACCEPTED。可选传 new_payload 转交给第三个 agent。"""
    return HandoffResult(
        accepted=True,
        task_id=payload.task_id,
        trace_id=payload.trace_id,
        to_agent=payload.to_agent,
        new_payload=new_payload,
    )


def reject_handoff(payload: HandoffPayload, reason: str) -> HandoffResult:
    """返 REJECTED。reason 必填(给发送方诊断用)。"""
    if not reason:
        raise ValueError("reject_handoff 必须填 reason")
    return HandoffResult(
        accepted=False,
        task_id=payload.task_id,
        trace_id=payload.trace_id,
        to_agent=payload.to_agent,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# HandoffCaps — 计数 + 超限抛
# ---------------------------------------------------------------------------

class HandoffCapExceeded(Exception):
    """handoff 次数超过 cap,需要 escalate to human。"""
    def __init__(self, task_id: str, trace_id: str, count: int, cap: int):
        self.task_id = task_id
        self.trace_id = trace_id
        self.count = count
        self.cap = cap
        super().__init__(
            f"handoff 超 cap: task_id={task_id} trace_id={trace_id} "
            f"count={count} cap={cap}, escalate to human")


class HandoffCaps:
    """单 trace 的 handoff 计数器。超 cap 抛 HandoffCapExceeded。"""
    def __init__(self, cap: int = DEFAULT_HANDOFF_CAP):
        self.cap = cap
        self._counts: dict[str, int] = {}  # trace_id → count

    def increment(self, trace_id: str) -> int:
        """+1,超 cap 抛 HandoffCapExceeded。返新计数。"""
        n = self._counts.get(trace_id, 0) + 1
        if n > self.cap:
            raise HandoffCapExceeded(task_id="?", trace_id=trace_id,
                                    count=n, cap=self.cap)
        self._counts[trace_id] = n
        return n

    def get(self, trace_id: str) -> int:
        return self._counts.get(trace_id, 0)

    def reset(self, trace_id: Optional[str] = None) -> None:
        """重置计数(单 trace 或全部)。"""
        if trace_id is None:
            self._counts.clear()
        else:
            self._counts.pop(trace_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_task_id() -> str:
    """生成新 task_id(workflow 级唯一)。"""
    return f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def new_trace_id() -> str:
    """生成新 trace_id(跨 agent 链路唯一)。"""
    return f"trace_{uuid.uuid4().hex[:12]}"