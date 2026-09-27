"""
prisIr_work/skills/loader.py — Skills 工作台 loader 门面(Phase 1, 2026-09-27)。

定位:把 registry + executor 合并成顶层 API,处理 L1+ confirm 闸门。

API:
  describe_registry()    — 返索引 JSON(系统 prompt 用)
  describe_registry_compact() — 索引紧凑 JSON 串
  describe_skill(id)     — 返 SkillDescribe
  execute_skill(id, args) — 直接调,返 SkillResult(L0 走透传,L1+ 返 need_confirm=True)
  search_skills(q) / list_skills() / count_skills()

确认门:
  · L0 → 直接 execute_skill,返 SkillResult
  · L1/L2/L3 → 不真发,返 SkillResult(need_confirm=True, confirm_msg=...)
    调用方(companion)拿到这个事件后弹前端确认卡,用户点确认才二次调
"""
from __future__ import annotations

import logging
from typing import Any

from .schema import SkillCall, SkillDescribe, SkillResult

log = logging.getLogger("prisir_work.skills.loader")


# ---------------------------------------------------------------------------
# 顶层 API 透传(避免直接 import 内部模块)
# ---------------------------------------------------------------------------

def describe_registry() -> dict[str, Any]:
    from .registry import describe_registry as _dr
    return _dr()


def describe_registry_compact() -> str:
    from .registry import describe_registry_compact as _dc
    return _dc()


def describe_skill(skill_id: str) -> SkillDescribe | None:
    from .registry import describe_skill as _ds
    return _ds(skill_id)


def list_skills():
    from .registry import list_skills as _ls
    return _ls()


def search_skills(query: str):
    from .registry import search_skills as _ss
    return _ss(query)


def count_skills() -> int:
    from .registry import count_skills as _cs
    return _cs()


# ---------------------------------------------------------------------------
# 确认闸门 — L1+ 返 need_confirm,L0 真发
# ---------------------------------------------------------------------------

_NEEDS_CONFIRM = {"L1", "L2", "L3"}


def execute_skill(skill_id: str, args: dict[str, Any] | None = None,
                   *, force: bool = False) -> SkillResult:
    """顶层执行入口(L1+ 默认走 confirm 闸门)。

    Args:
        skill_id: skill id
        args: 入参 dict
        force: True 跳过 L1+ 闸门直接发(给已确认用户回放用)

    Returns:
        SkillResult:
          - ok=True, payload={...}                L0 成功
          - need_confirm=True, confirm_msg=...    L1+ 等用户确认
          - ok=False, error=...                   失败 / 缺失 skill
    """
    from .registry import describe_skill as _ds
    from .executor import execute as _exe

    desc = _ds(skill_id)
    if desc is None:
        return SkillResult(skill_id=skill_id, ok=False,
                           error=f"skill_not_found:{skill_id}")

    risk = desc.index.risk
    if not force and risk in _NEEDS_CONFIRM:
        return SkillResult(
            skill_id=skill_id,
            ok=False,
            need_confirm=True,
            confirm_msg=desc.confirm or f"风险等级 {risk},需用户确认",
        )

    call = SkillCall(
        skill_id=skill_id,
        args=dict(args or {}),
        risk=risk,
        raw="",
    )
    return _exe(call, backend=desc.index.backend)


__all__ = [
    "describe_registry",
    "describe_registry_compact",
    "describe_skill",
    "execute_skill",
    "list_skills",
    "search_skills",
    "count_skills",
]