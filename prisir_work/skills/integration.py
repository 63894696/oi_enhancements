"""
prisIr_work/skills/integration.py — Skills 工作台主面板集成层(Phase 6, 2026-09-28)。

定位:把 Skills 工作台(registry/replan/exec_compat)接入 PrisirAI 主面板(prisIragent_web.py)。
     主面板走 stdlib HTTP + polling,不是 companion 的 ws,所以需要独立的入口函数。

复用:
  · describe_registry_compact() — JSON 索引(~8300c),注入 system prompt
  · plan_skill_calls() / _should_replan() — 两阶段 replan 闸门
  · route_exec() — EXEC ↔ tool_use 灰度切换

新增:
  · skills_index_block(text) — 包装成 system prompt 段,失败静默
  · SkillPlanQueue — 主面板事件队列(polling 协议)
  · push_skill_plan_request / auto_executed / confirm_ack — 队列端事件

主面板策略(skills_replan_enabled=False 默认):
  索引可见 + L0 直发,不弹卡。等用户手动开 replan 才走两阶段。

不破坏:
  · prisIr_work/capability.py(0 修改)
  · companion 现有 ws 事件链路(Phase 3.5/4/5 ship,独立工作)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from .loader import describe_registry_compact
from .replan import (
    DEFAULT_AUTO_EXECUTE_L1_THRESHOLD,
    _should_replan,
    plan_skill_calls,
)

log = logging.getLogger("prisir_work.skills.integration")


# ---------------------------------------------------------------------------
# 配置项(主面板 + companion 通用,与 Phase 1.5/3.5 同名)
# ---------------------------------------------------------------------------

# 索引注入开关(默认开;开 = LLM 看得到 80 skill 名;关 = 走老能力清单)
DEFAULT_SKILLS_INDEX_ENABLED = True
# replan 闸门(默认关;开 = LLM 二次调用 → 弹规划卡)
DEFAULT_SKILLS_REPLAN_ENABLED = False
# replan LLM 超时(秒)
DEFAULT_SKILLS_REPLAN_TIMEOUT = 8.0


# ---------------------------------------------------------------------------
# skills_index 注入段(给 _shell_system_prompt 追加)
# ---------------------------------------------------------------------------

def skills_index_block(text: str = "") -> str:
    """生成【Skills 工作台索引】段,失败返空串。

    与 companion Phase 1.5 同款输出(describe_registry_compact);
    长度 ~8300c,失败静默(import 失败/registry 空都返 "")。
    """
    try:
        idx = describe_registry_compact()
        if not idx:
            return ""
        return (
            "【Skills 工作台】当前可调用的 skill 索引(JSON):\n"
            + idx
            + "\n需要调工具时,先用 tool_calls 调 skill 拿到完整 schema,再 execute。"
        )
    except Exception as e:  # noqa: BLE001
        log.debug("skills_index_block 失败: %s", e)
        return ""


# ---------------------------------------------------------------------------
# 主面板事件队列(polling 协议,沿用 external_inject 范式)
# ---------------------------------------------------------------------------


class _SkillPlanEvent:
    """单条 skill_plan 事件(type + payload + session_id + ts + id)。

    id 是 UUID-like,前端 ack 后从队列移除(避免重连重复处理)。
    """

    __slots__ = ("id", "ts", "type", "session_id", "payload")

    def __init__(self, event_type: str, session_id: str, payload: dict):
        self.id = f"sp-{int(time.time() * 1000)}-{id(self)}"
        self.ts = time.time()
        self.type = event_type
        self.session_id = session_id
        self.payload = payload

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "type": self.type,
            "session_id": self.session_id,
            **self.payload,
        }


class SkillPlanQueue:
    """主面板 skill_plan 事件队列。

    设计(沿用 _INJECT_QUEUE 模式):
      · FIFO + 上限 32
      · 满了丢最老(写入路径优先)
      · peek 返未 ack 列表,前端 ack 后从队列移除
      · thread-safe(Lock)
    """

    MAX_SIZE = 32

    def __init__(self):
        self._items: list[_SkillPlanEvent] = []
        self._lock = threading.Lock()

    def push(self, event_type: str, session_id: str, payload: dict) -> str:
        """压一条事件,返回 evt.id(前端 ack 用)。满了丢最老。"""
        with self._lock:
            if len(self._items) >= self.MAX_SIZE:
                self._items.pop(0)
            ev = _SkillPlanEvent(event_type, session_id, payload)
            self._items.append(ev)
            return ev.id

    def peek(self, session_id: Optional[str] = None) -> list[dict]:
        """列出当前未 ack 事件(可按 session_id 过滤)。"""
        with self._lock:
            items = list(self._items)
        if session_id:
            items = [it for it in items if it.session_id == session_id]
        return [it.to_dict() for it in items]

    def ack(self, event_id: str) -> bool:
        """移除指定 id 的事件(前端确认后调用)。返是否真删了。"""
        with self._lock:
            for i, it in enumerate(self._items):
                if it.id == event_id:
                    self._items.pop(i)
                    return True
            return False

    def clear_session(self, session_id: str) -> int:
        """清掉某会话所有未 ack 事件(会话切换/重置用)。"""
        with self._lock:
            before = len(self._items)
            self._items = [it for it in self._items if it.session_id != session_id]
            return before - len(self._items)


# ---------------------------------------------------------------------------
# 主面板 singleton 队列
# ---------------------------------------------------------------------------

_QUEUE: Optional[SkillPlanQueue] = None
_QUEUE_LOCK = threading.Lock()


def get_queue() -> SkillPlanQueue:
    """singleton,延迟建;主面板入口文件调用一次后复用。"""
    global _QUEUE
    if _QUEUE is None:
        with _QUEUE_LOCK:
            if _QUEUE is None:
                _QUEUE = SkillPlanQueue()
    return _QUEUE


def push_skill_plan_request(
    session_id: str, calls: list[dict], *, max_risk: str = "L1", summary: str = ""
) -> str:
    """弹规划卡:前端 polling 拉到 → 弹卡 → 用户点执行/取消。"""
    return get_queue().push(
        "skill_plan_request",
        session_id,
        {
            "calls": calls,
            "max_risk": max_risk,
            "summary": summary,
        },
    )


def push_skill_plan_auto_executed(
    session_id: str, calls: list[dict], results: list[dict]
) -> str:
    """自动执行完成:L0 / L1+ ≤ threshold 时推这条 sys 卡。"""
    return get_queue().push(
        "skill_plan_auto_executed",
        session_id,
        {
            "calls": calls,
            "results": results,
            "ok_count": sum(1 for r in results if r.get("ok")),
            "total": len(results),
        },
    )


def push_skill_plan_confirm_ack(
    session_id: str, *, approved: bool, executed: int = 0, total: int = 0,
    reason: str = "",
) -> str:
    """用户确认 ack(approved=true 返 executed/total;false 返 reason)。"""
    return get_queue().push(
        "skill_plan_confirm_ack",
        session_id,
        {
            "approved": approved,
            "executed": executed,
            "total": total,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# 主面板 replan 入口(被 _run_chat_thread 在 chat_done 之前调)
# ---------------------------------------------------------------------------


async def maybe_skill_plan_replan(
    *,
    user_text: str,
    answer: str,
    session_id: str,
    plan_llm_call,
    skills_replan_enabled: bool = DEFAULT_SKILLS_REPLAN_ENABLED,
    auto_execute_l1_threshold: int = DEFAULT_AUTO_EXECUTE_L1_THRESHOLD,
    timeout_s: float = DEFAULT_SKILLS_REPLAN_TIMEOUT,
    execute_skill_fn=None,
) -> dict:
    """主面板 replan 闸门入口。

    流程:
        1. replan_enabled=False → 立刻返 {skipped: True}
        2. user_text 太短 / answer 含 EXEC: → 跳过
        3. plan_skill_calls(LLM 二次) → calls
        4. _should_replan(calls):
             · True  → push skill_plan_request(待前端 ack + confirm)
             · False → execute_skill + push auto_executed sys 卡
    任何异常 → fail-open,返 {skipped: True, error: str}。
    """
    if not skills_replan_enabled:
        return {"skipped": True, "reason": "replan_disabled"}
    if not user_text or len(user_text.strip()) < 2:
        return {"skipped": True, "reason": "text_too_short"}
    if answer and "EXEC:" in answer:
        # 已有 EXEC 标记,不强插规划
        return {"skipped": True, "reason": "exec_marker_present"}

    try:
        calls = await plan_skill_calls(
            user_text, plan_llm_call, timeout_s=timeout_s
        )
    except Exception as e:  # noqa: BLE001
        log.warning("replan LLM 失败,跳过: %s", e)
        return {"skipped": True, "reason": f"plan_failed:{e}"}

    if not calls:
        return {"skipped": True, "reason": "empty_plan"}

    # 转 dict 给前端
    call_dicts = [
        {"skill_id": c.skill_id, "args": dict(c.args), "risk": c.risk}
        for c in calls
    ]

    # 是否需要弹规划卡
    if _should_replan(
        calls,
        auto_execute_l1_threshold=auto_execute_l1_threshold,
        replan_enabled=skills_replan_enabled,
    ):
        max_risk = max(
            (c.risk for c in calls if c.risk in ("L0", "L1", "L2", "L3")),
            default="L1",
            key=lambda r: ["L0", "L1", "L2", "L3"].index(r),
        )
        push_skill_plan_request(
            session_id,
            call_dicts,
            max_risk=max_risk,
            summary=f"{len(calls)} 项 skill 规划",
        )
        return {"skipped": False, "needs_confirm": True, "calls": call_dicts}

    # ≤ threshold 自动执行
    if execute_skill_fn is None:
        from .loader import execute_skill as _execute_skill
        execute_skill_fn = _execute_skill

    results = []
    for c in calls:
        try:
            r = execute_skill_fn(c.skill_id, dict(c.args), force=True)
            results.append({
                "skill_id": c.skill_id,
                "ok": r.ok,
                "error": r.error or "",
                "result": r.payload or {},
            })
        except Exception as e:  # noqa: BLE001
            results.append({
                "skill_id": c.skill_id,
                "ok": False,
                "error": str(e),
                "result": {},
            })
    push_skill_plan_auto_executed(session_id, call_dicts, results)
    return {
        "skipped": False,
        "auto_executed": True,
        "calls": call_dicts,
        "results": results,
    }


__all__ = [
    # 配置
    "DEFAULT_SKILLS_INDEX_ENABLED",
    "DEFAULT_SKILLS_REPLAN_ENABLED",
    "DEFAULT_SKILLS_REPLAN_TIMEOUT",
    "DEFAULT_AUTO_EXECUTE_L1_THRESHOLD",
    # 索引注入
    "skills_index_block",
    # 队列
    "SkillPlanQueue",
    "get_queue",
    "push_skill_plan_request",
    "push_skill_plan_auto_executed",
    "push_skill_plan_confirm_ack",
    # replan 入口
    "maybe_skill_plan_replan",
]