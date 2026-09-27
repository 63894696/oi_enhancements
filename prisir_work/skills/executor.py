"""
prisIr_work/skills/executor.py — Skills 执行器(Phase 1, 2026-09-27)。

定位:把 SkillCall(协议无关 IR) 路由到后端实际执行,返 SkillResult。

三种后端:
  builtin    → endpoints._REGISTRY handler(body) → (payload, status)
  extension  → _ext_rpc_call(ext_id, method, body, timeout) — 走 ext_bridge
  tool_use   → Phase 2 适配器层(暂 stub 返 need_confirm)

设计:
  · **不重复风险门** — 老 endpoints handler 内部已经处理错误,L1+ confirm 在
    describe_skill 的 confirm 字段。executor 只需路由,不二次校验。
  · **fill_defaults** — 复用 agent_natural_video.fill_defaults 给 args 兜底
  · **fail-soft** — 任何坏调用返 SkillResult(ok=False, error=...),绝不抛栈
"""
from __future__ import annotations

import logging
from typing import Any

from .schema import SkillCall, SkillResult

log = logging.getLogger("prisir_work.skills.executor")


# ---------------------------------------------------------------------------
# args 默认值兜底 — 复用现有 agent_natural_video.fill_defaults
# ---------------------------------------------------------------------------

def _fill_defaults(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """尝试用 video module 的 fill_defaults 兜底。失败/缺失就原样返。"""
    if not args:
        return dict(args or {})
    try:
        from .. import agent_natural_video as _anv
        if hasattr(_anv, "fill_defaults"):
            return _anv.fill_defaults(skill_id, dict(args))
    except Exception:  # noqa: BLE001
        pass
    return dict(args or {})


# ---------------------------------------------------------------------------
# 后端分发
# ---------------------------------------------------------------------------

def _exec_builtin(call: SkillCall) -> SkillResult:
    """调 endpoints._REGISTRY[endpoint].handler(body) → (payload, status)。"""
    try:
        from .. import capability as _cap
        from .. import endpoints as _ep
        cap_entry = _cap.get(call.skill_id)
        if not cap_entry:
            return SkillResult(skill_id=call.skill_id, ok=False,
                               error=f"capability_not_found:{call.skill_id}")
        ep_path = cap_entry.get("endpoint")
        if not ep_path:
            return SkillResult(skill_id=call.skill_id, ok=False,
                               error=f"endpoint_missing:{call.skill_id}")
        ep_entry = _ep._REGISTRY.get(ep_path)
        if not ep_entry:
            return SkillResult(skill_id=call.skill_id, ok=False,
                               error=f"endpoint_not_registered:{ep_path}")
        body = _fill_defaults(call.skill_id, dict(call.args or {}))
        try:
            payload, _status = ep_entry["handler"](body)
        except Exception as exc:  # noqa: BLE001
            log.warning("skill %s handler raised: %s: %s",
                        call.skill_id, type(exc).__name__, exc)
            return SkillResult(skill_id=call.skill_id, ok=False,
                               error=f"{type(exc).__name__}:{exc}")
        ok = bool(payload.get("ok", False))
        return SkillResult(
            skill_id=call.skill_id,
            ok=ok,
            payload=dict(payload or {}),
            error="" if ok else str(payload.get("error", "execute_failed")),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("skill %s builtin dispatch failed: %s",
                    call.skill_id, exc)
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error=f"builtin_dispatch_failed:{type(exc).__name__}")


def _exec_extension(call: SkillCall) -> SkillResult:
    """调 _ext_rpc_call(ext_id, method, body, timeout)。Phase 1 stub。"""
    try:
        from prisiragent_web import _ext_rpc_call  # 延迟 import
    except Exception as exc:  # noqa: BLE001
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error=f"ext_bridge_unavailable:{type(exc).__name__}")
    ext_id = call.args.get("__ext_id__", "") if isinstance(call.args, dict) else ""
    method = call.args.get("__method__", call.skill_id) if isinstance(call.args, dict) else call.skill_id
    body = {k: v for k, v in (call.args or {}).items()
            if not k.startswith("__")}
    if not ext_id:
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error="extension_missing_ext_id")
    try:
        rv = _ext_rpc_call(ext_id, method, body, timeout=4.0)
    except Exception as exc:  # noqa: BLE001
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error=f"ext_rpc_failed:{type(exc).__name__}:{exc}")
    if not isinstance(rv, dict):
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error="ext_rpc_non_dict_return")
    if rv.get("error"):
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error=str(rv["error"]))
    payload = rv.get("result")
    if not isinstance(payload, dict):
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error="ext_rpc_bad_result")
    ok = bool(payload.get("ok", True))
    return SkillResult(
        skill_id=call.skill_id,
        ok=ok,
        payload=payload,
        error="" if ok else str(payload.get("error", "ext_failed")),
    )


# ---------------------------------------------------------------------------
# 顶层 API
# ---------------------------------------------------------------------------

def execute(call: SkillCall, *, backend: str | None = None) -> SkillResult:
    """执行一次 skill 调用。backend 为 None 时自动从 describe 推。"""
    if not isinstance(call, SkillCall):
        return SkillResult(skill_id=str(call), ok=False,
                           error="invalid_call_type")
    if not call.skill_id:
        return SkillResult(skill_id="", ok=False, error="empty_skill_id")

    # 决定后端
    if backend is None:
        try:
            from .registry import describe_skill
            desc = describe_skill(call.skill_id)
            if desc is None:
                return SkillResult(skill_id=call.skill_id, ok=False,
                                   error=f"skill_not_found:{call.skill_id}")
            backend = desc.index.backend
        except Exception:  # noqa: BLE001
            backend = "builtin"

    if backend == "extension":
        return _exec_extension(call)
    if backend == "tool_use":
        # Phase 2 实装 — 暂返 not_implemented
        return SkillResult(skill_id=call.skill_id, ok=False,
                           error="tool_use_backend_not_implemented_phase_2")
    # default = builtin
    return _exec_builtin(call)


def execute_many(calls: list[SkillCall]) -> list[SkillResult]:
    """批量执行(顺序,每个返 SkillResult)。空 list → []。"""
    out: list[SkillResult] = []
    for c in calls or []:
        out.append(execute(c))
    return out


__all__ = ["execute", "execute_many"]