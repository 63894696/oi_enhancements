"""
prisir_work/video_budget.py — 视频 workflow 预算估算 + pre-compose 校验(Phase 11 OM-P3, 2026-09-28)。

承接 [[prisIr-phase-10-om-p2-scoring]] + 用户「免费优先,单集 ≤ $0.10」拍板。

## 定位
在 workflow 真正执行前,**预先估算成本**并拦截超预算方案:
- step 级 cost_per_call × 次数
- 同 tag 降级建议(付费 → 同 tag 免费)
- 弹卡路径:超预算 → fail-fast 返 need_confirm,前端让用户选「降级 / 加预算 / 取消」

## 默认预算常量
  DEFAULT_BUDGET_PER_EPISODE = $0.10 (用户拍板:免费优先)
  DEFAULT_COST_PER_STEP_HARD_CAP = $0.014 (7 步 ÷ 预算)

## 关键 API
  - estimate_step_cost(step, provider_map) → float
  - estimate_workflow_cost(steps, provider_map) → CostEstimate
  - suggest_replacements(step_provider, tag) → list[str] (同类免费 provider,quality 降序)
  - check_budget(steps, provider_map, budget) → BudgetCheck(ok / exceeded / replace_plan)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "CostEstimate",
    "BudgetCheck",
    "DEFAULT_BUDGET_PER_EPISODE",
    "DEFAULT_COST_PER_STEP_HARD_CAP",
    "estimate_step_cost",
    "estimate_workflow_cost",
    "suggest_replacements",
    "check_budget",
    "check_budget_for_steps",
]

log = logging.getLogger("prisir_work.video_budget")


# ---------------------------------------------------------------------------
# 默认预算常量(用户 2026-09-28 拍板:免费优先)
# ---------------------------------------------------------------------------

DEFAULT_BUDGET_PER_EPISODE: float = 0.10      # $0.10/集
DEFAULT_COST_PER_STEP_HARD_CAP: float = 0.014  # $0.014/步(7 步 × 7 = $0.10)
DEFAULT_STEPS_PER_EPISODE: int = 7             # 60s 短剧典型步数(image2video+tts+bgm+image+assemble)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class CostEstimate:
    """单 workflow 成本估算。"""
    total: float = 0.0
    per_step: dict[str, float] = field(default_factory=dict)
    steps_count: int = 0
    over_cap_count: int = 0  # 超过单步硬上限的 step 数

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 4),
            "per_step": {k: round(v, 4) for k, v in self.per_step.items()},
            "steps_count": self.steps_count,
            "over_cap_count": self.over_cap_count,
        }


@dataclass
class BudgetCheck:
    """预算校验结果。"""
    ok: bool
    estimate: CostEstimate
    budget: float
    exceeded: bool = False
    over_cap_steps: list[str] = field(default_factory=list)
    replace_plan: dict[str, list[str]] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exceeded": self.exceeded,
            "estimate": self.estimate.to_dict(),
            "budget": round(self.budget, 4),
            "over_cap_steps": list(self.over_cap_steps),
            "replace_plan": {k: list(v) for k, v in self.replace_plan.items()},
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# 估算函数
# ---------------------------------------------------------------------------

def estimate_step_cost(step: dict[str, Any],
                       provider_map: dict[str, str] | None = None) -> float:
    """估算单步成本。

    step:  {"id": "shot1", "capability": "video.image2video", "args": {...}}
    provider_map: {step_id: provider_name};缺则从 step['args']['provider'] 读

    返回 cost_per_call(美元)。未知 provider / 未知 capability → 0.0(保守)
    """
    try:
        from .video_provider_scoring import _PROVIDERS, _ensure_defaults
        _ensure_defaults()
    except ImportError:
        return 0.0

    step_id = step.get("id", "")
    provider_name = None
    if provider_map and step_id in provider_map:
        provider_name = provider_map[step_id]
    elif "provider" in step.get("args", {}):
        provider_name = step["args"]["provider"]
    if not provider_name or provider_name not in _PROVIDERS:
        return 0.0
    return float(_PROVIDERS[provider_name].get("cost_per_call", 0.0))


def estimate_workflow_cost(steps: list[dict[str, Any]],
                           provider_map: dict[str, str] | None = None) -> CostEstimate:
    """估算整个 workflow 成本。"""
    per_step: dict[str, float] = {}
    total = 0.0
    over_cap_count = 0
    for step in steps:
        sid = step.get("id", f"s{len(per_step)}")
        c = estimate_step_cost(step, provider_map)
        per_step[sid] = c
        total += c
        if c > DEFAULT_COST_PER_STEP_HARD_CAP:
            over_cap_count += 1
    return CostEstimate(
        total=total,
        per_step=per_step,
        steps_count=len(steps),
        over_cap_count=over_cap_count,
    )


def suggest_replacements(provider_name: str, tag: str | None = None) -> list[str]:
    """为某 provider 推荐同类免费替代(quality 降序)。

    用法:用户挑了 kling(image2video,付费),返回 [local_wan, ...]
          选 piper / edge_tts(免费)直接返 []。
    """
    try:
        from .video_provider_scoring import _PROVIDERS, _ensure_defaults
        _ensure_defaults()
    except ImportError:
        return []

    meta = _PROVIDERS.get(provider_name, {})
    if not meta:
        return []

    # 已是免费(cost=1.0)→ 无需替代
    if float(meta.get("cost", 1.0)) >= 1.0:
        return []

    actual_tag = tag or meta.get("tag", "")
    candidates: list[tuple[str, float]] = []
    for name, m in _PROVIDERS.items():
        if name == provider_name:
            continue
        if m.get("tag") != actual_tag:
            continue
        if float(m.get("cost", 1.0)) < 1.0:
            continue  # 只推免费替代
        # 选 quality 最高的免费 provider
        candidates.append((name, float(m.get("quality", 0.5))))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in candidates]


# ---------------------------------------------------------------------------
# 预算校验主函数
# ---------------------------------------------------------------------------

def check_budget(steps: list[dict[str, Any]],
                 provider_map: dict[str, str] | None = None,
                 budget: float = DEFAULT_BUDGET_PER_EPISODE) -> BudgetCheck:
    """校验 workflow 是否在预算内。

    返回 BudgetCheck:
      ok=True  → 预算内,可执行
      ok=False, exceeded=True → 超预算;replace_plan 给出每步可降级的免费 provider
      ok=False, exceeded=False → 校验失败(空 steps / 数据缺失)
    """
    if not steps:
        return BudgetCheck(
            ok=False, exceeded=False,
            estimate=CostEstimate(),
            budget=budget,
            message="empty workflow steps",
        )

    est = estimate_workflow_cost(steps, provider_map)

    # 找出超单步上限的 step
    over_cap_steps = [
        sid for sid, c in est.per_step.items()
        if c > DEFAULT_COST_PER_STEP_HARD_CAP
    ]

    # 为超限 step 算替换建议
    replace_plan: dict[str, list[str]] = {}
    for sid in over_cap_steps:
        step = next((s for s in steps if s.get("id") == sid), None)
        if not step:
            continue
        cur_provider = (provider_map or {}).get(sid) or step.get("args", {}).get("provider", "")
        if not cur_provider:
            continue
        replacements = suggest_replacements(cur_provider)
        if replacements:
            replace_plan[sid] = replacements

    if est.total > budget:
        msg = (
            f"workflow 预算超限: ${est.total:.4f} > ${budget:.4f} "
            f"(steps={est.steps_count}, over_cap={est.over_cap_count})"
        )
        if replace_plan:
            msg += f";可降级 {len(replace_plan)} 个 step 到免费 provider"
        return BudgetCheck(
            ok=False, exceeded=True,
            estimate=est, budget=budget,
            over_cap_steps=over_cap_steps,
            replace_plan=replace_plan,
            message=msg,
        )

    return BudgetCheck(
        ok=True, exceeded=False,
        estimate=est, budget=budget,
        over_cap_steps=over_cap_steps,
        replace_plan=replace_plan,
        message=f"workflow 在预算内: ${est.total:.4f} ≤ ${budget:.4f}",
    )


# Alias:便于 workflow 集成调用
def check_budget_for_steps(steps: list[dict[str, Any]],
                           provider_map: dict[str, str] | None = None,
                           budget: float = DEFAULT_BUDGET_PER_EPISODE) -> BudgetCheck:
    return check_budget(steps, provider_map, budget)