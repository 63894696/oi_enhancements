"""
prisIr_work/skills/replan.py — 两阶段 replan 闸门(Phase 3, 2026-09-28)。

定位:用户输入进来后,先问 LLM"你要调哪些 skill" → 返 plan JSON → 再执行。
     L1+ 风险默认走 replan,L0 直发(可配置)。

设计:
  · **replan LLM 二次调用** — 不在主对话流里,单次问答,温度 0
  · **plan_skill_calls(text)** 返 List[SkillCall] — 调 LLM 解析,失败返 []
  · **_should_replan(calls)** 决策 — L0 全过,L1+ > threshold 触发,否则自动过
  · **fail-open** — replan LLM 失败 / parse 失败 → 返 [],让 Phase 1 skill registry 兜底
  · **不修改 Phase 1/2** — planner 是新模块,走 registry.describe_skill / loader.execute_skill

调用形态:
  calls = plan_skill_calls(user_text, history=[...])
  if _should_replan(calls):
      推 ws: skill_plan_request(calls)  # 前端弹规划卡
      等用户确认 → 真发
  else:
      for c in calls: execute_skill(c.skill_id, c.args)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional

from .schema import SkillCall

log = logging.getLogger("prisir_work.skills.replan")


# ---------------------------------------------------------------------------
# 闸门决策
# ---------------------------------------------------------------------------

# 默认 L1+ 自动执行阈值(L1+ 个数 ≤ 这个就直接走,> 才弹卡)
DEFAULT_AUTO_EXECUTE_L1_THRESHOLD = 2


def _should_replan(
    calls: list[SkillCall],
    *,
    auto_execute_l1_threshold: int = DEFAULT_AUTO_EXECUTE_L1_THRESHOLD,
    replan_enabled: bool = True,
) -> bool:
    """replan LLM 给出的 calls 列表是否需要弹规划卡给用户。

    规则:
        - replan_enabled=False → 永不弹卡(全部 LLM 自家执行)
        - calls 空集 → 无需弹
        - 全 L0 → 不弹(只读)
        - L1+ 数量 > 阈值 → 弹卡
        - L1+ 数量 ≤ 阈值 → 不弹(自动执行)
    """
    if not replan_enabled:
        return False
    if not calls:
        return False
    l1_count = sum(1 for c in calls if c.risk in ("L1", "L2", "L3"))
    return l1_count > auto_execute_l1_threshold


# ---------------------------------------------------------------------------
# LLM 二次调用入口
# ---------------------------------------------------------------------------

# plan_llm_call signature:async (messages) → response_text
PlanLLMCall = Callable[[list[dict]], "asyncio.Future[str]"]


REPLAN_SYSTEM = """\
你是任务规划助手。看到用户的请求 + 工作台 skill 索引 JSON,
判断该调哪些 skill,以 JSON 返:
{"calls": [{"skill_id": "X", "args": {...}}, ...]}

规则:
  · 命中 skill → 写入 calls,skill_id 必须从索引 JSON 找
  · L0(只读)可以直接调;L1+ 风险卡走 confirm
  · 无 skill 可调 → 返 {"calls": []}
  · 参数从用户文本推断,缺参填空字符串 ""
  · 只输出 JSON,不要解释
"""

USER_TEMPLATE = """\
用户请求: {user_text}

技能索引 JSON:
{skills_index}
"""


async def plan_skill_calls(
    user_text: str,
    plan_llm_call: PlanLLMCall,
    *,
    skills_index: str | None = None,
    history: list[dict] | None = None,
    timeout_s: float = 8.0,
) -> list[SkillCall]:
    """replan LLM 二次调用 → List[SkillCall]。失败 / 超时 / parse 失败 → []。

    Args:
        user_text: 用户原始输入
        plan_llm_call: async (messages) → str(LLM 完整响应文本)
        skills_index: 自定义索引(默认用 describe_registry_compact())
        history: 可选历史 messages(LLM 看到上下文)
        timeout_s: 超时秒数,默认 8s
    """
    import asyncio
    if skills_index is None:
        from .registry import describe_registry_compact
        skills_index = describe_registry_compact()

    messages: list[dict] = [{"role": "system", "content": REPLAN_SYSTEM}]
    if history:
        messages.extend(history)
    messages.append({
        "role": "user",
        "content": USER_TEMPLATE.format(
            user_text=user_text, skills_index=skills_index),
    })

    try:
        raw = await asyncio.wait_for(plan_llm_call(messages), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("replan: plan_llm_call timeout (%.1fs)", timeout_s)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("replan: plan_llm_call raised: %s", exc)
        return []
    return parse_plan_response(raw or "")


# ---------------------------------------------------------------------------
# plan 解析
# ---------------------------------------------------------------------------

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_plan_response(raw: str) -> list[SkillCall]:
    """LLM 响应文本 → List[SkillCall]。容错:JSON 内嵌 / 文本噪声 / 缺字段。

    接受形态:
      {"calls": [{"skill_id": "X", "args": {...}}, ...]}
      {"calls": [{"skill_id": "X", "arguments": {...}}, ...]}  # arguments 别名
      直接 [{...}, ...]
      单个 {...} 也接受
    """
    if not raw or not isinstance(raw, str):
        return []
    # 提取 JSON 对象
    raw = raw.strip()
    # 去 ```json ... ``` 包装
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()
    # 找最外层 {...} 或 [...]
    json_text = raw
    if not raw.startswith("{") and not raw.startswith("["):
        m = _JSON_OBJ_RE.search(raw)
        if m:
            json_text = m.group(0)
    try:
        obj = json.loads(json_text)
    except Exception as exc:  # noqa: BLE001
        log.warning("replan: JSON parse failed: %s raw=%s",
                    exc, raw[:120])
        return []
    # 标准化
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("calls") or obj.get("plan") or []
    else:
        return []
    if not isinstance(items, list):
        return []

    out: list[SkillCall] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("skill_id") or it.get("id") or "").strip()
        if not sid:
            continue
        args = it.get("args") or it.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        # risk 补
        risk = "L0"
        try:
            from .registry import describe_skill as _ds
            desc = _ds(sid)
            if desc:
                risk = desc.index.risk
        except Exception:
            pass
        out.append(SkillCall(
            skill_id=sid,
            args=dict(args),
            risk=risk,
            raw=json.dumps(it, ensure_ascii=False),
        ))
    return out


# ---------------------------------------------------------------------------
# 便利函数:整套 replan 流程(给外部直接用)
# ---------------------------------------------------------------------------

async def maybe_replan_and_execute(
    user_text: str,
    plan_llm_call: PlanLLMCall,
    *,
    replan_enabled: bool = True,
    auto_execute_l1_threshold: int = DEFAULT_AUTO_EXECUTE_L1_THRESHOLD,
    on_plan_need_confirm: Optional[Callable[[list[SkillCall]], Any]] = None,
    skills_index: str | None = None,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """replan 完整流程。返回:
        {
          "calls": [SkillCall, ...],
          "need_replan": bool,   # True 时调用方应等用户确认
          "executed": [SkillResult, ...] | None,  # 自动执行时填
          "reason":  str,
        }
    流程:
      1) plan_skill_calls 调 LLM
      2) 空 plan → 直接返 False
      3) _should_replan 决策:
          True  → 不执行,等用户确认(若给了 on_plan_need_confirm 调一下)
          False → 顺序 execute_skill + 返 executed
    """
    from .loader import execute_skill
    calls = await plan_skill_calls(
        user_text, plan_llm_call,
        skills_index=skills_index, history=history,
    )
    if not calls:
        return {"calls": [], "need_replan": False,
                "executed": None, "reason": "empty_plan"}
    if _should_replan(calls,
                      auto_execute_l1_threshold=auto_execute_l1_threshold,
                      replan_enabled=replan_enabled):
        if on_plan_need_confirm:
            try:
                on_plan_need_confirm(calls)
            except Exception as exc:  # noqa: BLE001
                log.warning("replan: on_plan_need_confirm raised: %s", exc)
        return {"calls": calls, "need_replan": True,
                "executed": None, "reason": "l1_count_exceeds_threshold"}
    # 自动执行(强制 force=True 跳过 L1 confirm 闸门,因为 replan 已经做了总闸)
    results = []
    for c in calls:
        try:
            r = execute_skill(c.skill_id, dict(c.args or {}), force=True)
        except Exception as exc:  # noqa: BLE001
            from .schema import SkillResult
            r = SkillResult(skill_id=c.skill_id, ok=False,
                            error=f"replan_exec:{type(exc).__name__}")
        results.append(r)
    return {"calls": calls, "need_replan": False,
            "executed": results, "reason": "auto_executed"}


__all__ = [
    "_should_replan",
    "DEFAULT_AUTO_EXECUTE_L1_THRESHOLD",
    "plan_skill_calls",
    "parse_plan_response",
    "maybe_replan_and_execute",
    "REPLAN_SYSTEM",
]