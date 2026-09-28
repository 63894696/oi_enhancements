"""prisir_work/agent_video_workflow.py — 跨能力编排 workflow(P3j T15-D + Phase 9 OM-P1)。

定位:让「做视频 + 上传 B站 + 上传 YouTube」一次到位。
按节点 DAG 跑 — 每个节点是 {capability, args, depends_on}。
输出用 $ref 引用前驱节点的产出。

设计:
  · 简化版 DAG — 拓扑排序 + 顺序/并行执行
  · 复用 endpoints._REGISTRY 的 handler 跟 agent_natural_video.execute
    同一执行路径 — 不绕过红线
  · 节点失败 → 中断整图,返 failed_step + 之前成功的结果
  · 不引入 graph_engine.py 的 VariablePool — 视频 workflow 用 dict 就够

Phase 9 OM-P1 增量(2026-09-28):
  · checkpoint 协议集成 — 借鉴 [[prisIr-openmontage-recon]]
  · run_workflow(workflow_id=...) 显式传 ID 时,每个 step 落 checkpoint
  · run_workflow(resume=True) 从最近 checkpoint 跳过已完成 step
  · 不传 workflow_id → 旧行为(无 checkpoint,向后兼容)

用例(JSON DSL):
  {
    "steps": [
      {"id": "create", "capability": "video.create",
       "args": {"topic": "PrisirAI", "script": "..."}},
      {"id": "yt_upload", "capability": "youtube.upload",
       "depends_on": ["create"],
       "args": {"video": "$create.artifact.path", "title": "PrisirAI Demo"}},
    ],
    "stop_on_error": true,
    "workflow_id": "wf_20260928_abc123",  # 可选,显式传则落 checkpoint
    "resume": false                       # 可选,True 跳过已完成 step
  }

  from prisir_work.agent_video_workflow import run_workflow
  result = run_workflow(dsl)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["WorkflowStep", "WorkflowResult", "run_workflow", "parse_dependencies"]


# ---------------------------------------------------------------------------
# Phase 9 OM-P1:checkpoint 集成(import 内部,避免外部未装时挂)
# ---------------------------------------------------------------------------

def _get_checkpoint_manager():
    """lazy import CheckpointManager — 测试可 monkey patch。"""
    try:
        from .video_checkpoint import CheckpointManager
        return CheckpointManager()
    except Exception as exc:  # noqa: BLE001
        log.warning("CheckpointManager 不可用: %s", exc)
        return None


log = logging.getLogger("prisir_work.workflow")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    """DAG 中的一步。"""
    id: str
    capability: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    """整个 workflow 的执行结果。"""
    ok: bool
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)  # step_id → {ok, result, args_used}
    failed_step: str = ""
    error: str = ""
    artifact: dict[str, Any] = field(default_factory=dict)  # 额外元数据(budget_check / metrics 等)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "steps": dict(self.steps),
            "failed_step": self.failed_step,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# $ref 解析 — 把 "video": "$create.artifact.path" 换成前驱的输出
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")


def _resolve_refs(value: Any, ctx: dict[str, dict[str, Any]]) -> Any:
    """递归把字符串里的 $ref 替换成 ctx 里对应的值。

    ctx: {"step_id": {"ok": True, "result": {...}, ...}, ...}
    """
    if isinstance(value, str):
        # 整个字符串就是一个 $ref → 直接替换(返回 dict/str/int 等)
        m = _REF_RE.fullmatch(value)
        if m:
            return _resolve_ref_path(m.group(1), ctx)
        # 字符串里混着 $ref → 仅替换,保留其它字符
        def _sub(m):
            return str(_resolve_ref_path(m.group(1), ctx))
        return _REF_RE.sub(_sub, value)
    if isinstance(value, list):
        return [_resolve_refs(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_refs(v, ctx) for k, v in value.items()}
    return value


def _resolve_ref_path(path: str, ctx: dict[str, dict[str, Any]]) -> Any:
    """$create.artifact.path → ctx["create"]["result"]["artifact"]["path"]"""
    parts = path.split(".")
    if not parts:
        return None
    node_id = parts[0]
    if node_id not in ctx:
        log.warning("$ref 未找到 step: %s", node_id)
        return None
    cur: Any = ctx[node_id].get("result", {})
    for p in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif hasattr(cur, p):
            cur = getattr(cur, p)
        else:
            return None
    return cur


def parse_dependencies(steps: list[dict[str, Any]]) -> list[WorkflowStep]:
    """从 DSL dict list 解析成 WorkflowStep list。"""
    out = []
    for s in steps:
        if not s.get("id"):
            raise ValueError(f"step 缺 id: {s}")
        if not s.get("capability"):
            raise ValueError(f"step 缺 capability: {s}")
        out.append(WorkflowStep(
            id=s["id"],
            capability=s["capability"],
            args=s.get("args") or {},
            depends_on=s.get("depends_on") or [],
        ))
    return out


# ---------------------------------------------------------------------------
# 拓扑排序
# ---------------------------------------------------------------------------

def _topo_sort(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """Kahn's algorithm。无环保证由 DSL 构造时定;这里只排序。"""
    step_map = {s.id: s for s in steps}
    in_degree: dict[str, int] = {s.id: 0 for s in steps}
    children: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep not in step_map:
                raise ValueError(f"step {s.id} 引用未知 dep: {dep}")
            in_degree[s.id] += 1
            children[dep].append(s.id)
    queue = [sid for sid, d in in_degree.items() if d == 0]
    out: list[WorkflowStep] = []
    while queue:
        sid = queue.pop(0)
        out.append(step_map[sid])
        for c in children[sid]:
            in_degree[c] -= 1
            if in_degree[c] == 0:
                queue.append(c)
    if len(out) != len(steps):
        raise ValueError("workflow 含环或缺失 dep")
    return out


# ---------------------------------------------------------------------------
# 单步执行 — 走 endpoints._REGISTRY(跟 agent_natural_video.execute 同一路径)
# ---------------------------------------------------------------------------

def _execute_step(step: WorkflowStep, ctx: dict[str, dict[str, Any]],
                 *, timeout: float = 900.0) -> tuple[bool, dict[str, Any], str]:
    """跑一步:解析 $ref → 调 endpoint handler。返 (ok, payload, error)。"""
    from . import capability as _cap
    from . import endpoints as _ep

    cap_entry = _cap.get(step.capability)
    if not cap_entry:
        return False, {}, f"capability_not_found:{step.capability}"
    ep_entry = _ep._REGISTRY.get(cap_entry["endpoint"])
    if not ep_entry:
        return False, {}, f"endpoint_not_found:{cap_entry['endpoint']}"

    # 替换 $ref
    try:
        resolved_args = _resolve_refs(step.args, ctx)
    except Exception as e:  # noqa: BLE001
        return False, {}, f"ref_resolve_failed:{type(e).__name__}: {e}"

    try:
        payload, _status = ep_entry["handler"](resolved_args)
        return payload.get("ok", False), payload, (
            "" if payload.get("ok") else payload.get("error", "execute_failed"))
    except Exception as e:  # noqa: BLE001
        return False, {}, f"exception:{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 入口:run_workflow(dsl)
# ---------------------------------------------------------------------------

def run_workflow(dsl: dict[str, Any], *, stop_on_error: bool = True,
                workflow_id: Optional[str] = None,
                resume: bool = False,
                budget: Optional[float] = None,
                pre_compose_check: bool = True) -> WorkflowResult:
    """执行工作流。

    dsl 形态:
      {"steps": [{"id": "...", "capability": "...", "args": {...}, "depends_on": [...]}],
       "stop_on_error": True,
       "workflow_id": "...",  # 可选
       "resume": False,       # 可选
       "budget": 0.10}        # 可选,OM-P3 pre-compose 校验

    Phase 9 OM-P1 增量:
      · workflow_id: 显式传则每个 step 落 checkpoint(JSON 到 ~/.prisIrai/checkpoints/wf_<id>/)
      · resume=True: 跳过 status=='ok' 的 step,从失败的 step 续跑
      · 不传 workflow_id → 旧行为(无 checkpoint),向后兼容

    Phase 11 OM-P3 增量:
      · budget: 单集预算(默认 None = 用 video_budget.DEFAULT_BUDGET_PER_EPISODE)
      · pre_compose_check: 是否预算预检(默认 True);False 跳过预算检查,旧行为
      · 超预算 → 返 ok=False, error='budget_exceeded', artifact 里含 check_budget 结果
        + replace_plan(给前端弹卡用)
    """
    steps_raw = dsl.get("steps") or []
    if not steps_raw:
        return WorkflowResult(ok=False, error="no_steps")
    if stop_on_error is False:
        stop_on_error = dsl.get("stop_on_error", True)

    # Phase 9:checkpoint manager 可选
    wf_id = workflow_id or dsl.get("workflow_id")
    resume = resume or bool(dsl.get("resume"))
    cm = _get_checkpoint_manager() if wf_id else None

    # Phase 11 OM-P3:pre-compose 预算校验(可选)
    budget_val = budget if budget is not None else dsl.get("budget")
    pre_compose = pre_compose_check and dsl.get("pre_compose_check", True)
    if pre_compose:
        try:
            from .video_budget import check_budget_for_steps, DEFAULT_BUDGET_PER_EPISODE
            budget_to_use = budget_val if budget_val is not None else DEFAULT_BUDGET_PER_EPISODE
            check = check_budget_for_steps(steps_raw, budget=float(budget_to_use))
            if not check.ok:
                return WorkflowResult(
                    ok=False,
                    error=f"budget_exceeded:{check.message}",
                    artifact={"budget_check": check.to_dict()},
                )
        except ImportError:
            pass  # video_budget 未装,降级不拦截

    try:
        steps = parse_dependencies(steps_raw)
        ordered = _topo_sort(steps)
    except ValueError as e:
        return WorkflowResult(ok=False, error=f"parse_failed:{e}")

    # Phase 9:resume 模式 → 跳过已完成 step
    skip_set: set[str] = set()
    if cm and resume and wf_id:
        skip_set = set(cm.resume_from(wf_id, [s.id for s in ordered]))
        if skip_set:
            log.info("resume: 跳过已完成 step %s", sorted(skip_set))

    ctx: dict[str, dict[str, Any]] = {}

    # Phase 9:resume 模式 → 从 checkpoint 加载已完成的 ctx
    if cm and skip_set:
        for sid in skip_set:
            payload = cm.load_step_payload(wf_id, sid)
            if payload:
                # 反推 ctx 形态
                ctx[sid] = {
                    "ok": True,
                    "result": payload.get("result", payload),
                    "error": "",
                    "args": payload.get("args", {}),
                    "capability": payload.get("capability", ""),
                }

    for step in ordered:
        # Phase 9:resume → 跳过已完成 step
        if step.id in skip_set:
            continue
        ok, payload, error = _execute_step(step, ctx)
        ctx[step.id] = {"ok": ok, "result": payload, "error": error,
                        "args": step.args, "capability": step.capability}
        # Phase 9:每个 step 后落 checkpoint
        if cm and wf_id:
            ck_status = "ok" if ok else "failed"
            ck_error = error if not ok else ""
            try:
                cm.save(wf_id, step.id,
                        {"ok": ok, "result": payload, "args": step.args,
                         "capability": step.capability},
                        status=ck_status, error=ck_error)
            except Exception as ck_exc:  # noqa: BLE001
                # checkpoint 落盘失败不阻断 workflow,只记 warn
                log.warning("checkpoint save 失败 wf=%s step=%s: %s",
                            wf_id, step.id, ck_exc)
        if not ok:
            return WorkflowResult(
                ok=False, steps=ctx, failed_step=step.id,
                error=error or "step_failed")

    return WorkflowResult(ok=True, steps=ctx, error="")