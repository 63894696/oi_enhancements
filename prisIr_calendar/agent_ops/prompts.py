# -*- coding: utf-8 -*-
"""
prompts.py — 一次一问话术模板 (task #5)

核心契约 (travel-time-buddy.md):
    - "一次只问一个清晰问题"
    - 多 ambiguity 时按优先级合并成一条 (one_at_a_time)

模板:
    PROMPT_AMBIGUOUS_PLACE       — venue 解析失败 + workplace 都没
    PROMPT_UNKNOWN_WFH           — 没有 workplace_addresses.office
    PROMPT_UNCLEAR_MODE          — default_travel_mode 没设
    PROMPT_BUFFER_DESTROYS_DAY   — buffer 会毁掉这天(整天都在路上)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional


class PromptKind(str, Enum):
    """每条 prompt 的语义类型 — 给 UI 层做样式/优先级区分."""
    AMBIGUOUS_PLACE = "ambiguous_place"
    UNKNOWN_WFH = "unknown_wfh"
    UNCLEAR_MODE = "unclear_mode"
    BUFFER_DESTROYS_DAY = "buffer_destroys_day"


# ============================================================
# 模板 (中英混合, 默认中文 — 对齐产品边界国内用户优先)
# ============================================================

PROMPT_AMBIGUOUS_PLACE = "我不太确定「{raw}」在哪里,能告诉我准确地址吗?或者你打算在 WFH?"

PROMPT_UNKNOWN_WFH = "今天你在办公室还是在 WFH?"

PROMPT_UNCLEAR_MODE = "从{origin}到{dest}你通常怎么去?开车/地铁/自行车?"

PROMPT_BUFFER_DESTROYS_DAY = (
    "为「{summary}」插{eta_min}分钟交通会占满当天,"
    "要插短一点({short_min}分钟)还是跳到第二天?"
)


# ============================================================
# Ambiguity 数据类 (供 travel_buffer.py 用)
# ============================================================
@dataclass(frozen=True)
class Ambiguity:
    """一次 ambiguity 的描述.

    kind:    PromptKind
    message: 已填充好的中文模板(参数已填)
    kwargs:  模板原始参数, 用于重新渲染或回看
    priority: 数字越小越靠前 (0 = 最优先问)
    """
    kind: PromptKind
    message: str
    kwargs: dict
    priority: int = 100


# ============================================================
# 模板渲染
# ============================================================
def render_ambiguous_place(raw: str) -> Ambiguity:
    return Ambiguity(
        kind=PromptKind.AMBIGUOUS_PLACE,
        message=PROMPT_AMBIGUOUS_PLACE.format(raw=raw or "这个地方"),
        kwargs={"raw": raw or ""},
        priority=0,
    )


def render_unknown_wfh() -> Ambiguity:
    return Ambiguity(
        kind=PromptKind.UNKNOWN_WFH,
        message=PROMPT_UNKNOWN_WFH,
        kwargs={},
        priority=10,
    )


def render_unclear_mode(origin: str, dest: str) -> Ambiguity:
    return Ambiguity(
        kind=PromptKind.UNCLEAR_MODE,
        message=PROMPT_UNCLEAR_MODE.format(
            origin=origin or "起点",
            dest=dest or "终点",
        ),
        kwargs={"origin": origin or "", "dest": dest or ""},
        priority=20,
    )


def render_buffer_destroys_day(summary: str, eta_min: int, short_min: int) -> Ambiguity:
    return Ambiguity(
        kind=PromptKind.BUFFER_DESTROYS_DAY,
        message=PROMPT_BUFFER_DESTROYS_DAY.format(
            summary=summary or "这场会议",
            eta_min=int(eta_min),
            short_min=int(short_min),
        ),
        kwargs={"summary": summary or "", "eta_min": int(eta_min), "short_min": int(short_min)},
        priority=5,
    )


# ============================================================
# 一次一问合并
# ============================================================
def one_at_a_time(questions: Iterable[Ambiguity]) -> Optional[Ambiguity]:
    """多 ambiguity 时, 按 priority 取最优先的一条.

    设计:
        - 数字越小越靠前 (priority=0 是最高优先级)
        - 完全没东西返回 None (调用方据此走默认逻辑)
        - 单条直接返回原值, 不改 message
    """
    items: List[Ambiguity] = [q for q in questions if q is not None]
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    items.sort(key=lambda q: (q.priority, q.kind.value))
    return items[0]


__all__ = [
    "PromptKind",
    "Ambiguity",
    "PROMPT_AMBIGUOUS_PLACE",
    "PROMPT_UNKNOWN_WFH",
    "PROMPT_UNCLEAR_MODE",
    "PROMPT_BUFFER_DESTROYS_DAY",
    "render_ambiguous_place",
    "render_unknown_wfh",
    "render_unclear_mode",
    "render_buffer_destroys_day",
    "one_at_a_time",
]
