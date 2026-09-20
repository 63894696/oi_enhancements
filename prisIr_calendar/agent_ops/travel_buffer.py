# -*- coding: utf-8 -*-
"""
travel_buffer.py — 主入口 (task #5) ★ 核心

公共 API:
    TravelBufferAgent: 类, 主入口
        __init__(store, *, user_profile, ledger_sink=None, ask_user_fn=None, ...)
        scan_and_protect(*, days=14) -> ScanReport
        ask_user(ambiguity) -> str

完整流程 (scan_and_protect, 对应 travel-time-buddy.md 9 条契约):
    1. 读 user_profile.workplace_addresses + default_travel_mode
    2. list_window(days=14) → 拿事件
    3. 对每个 event:
        a. classify.should_skip_event → 跳过
        b. venue.resolve_venue + is_workplace_diff → True 才需要 buffer
        c. chain.choose_origin_for_event → 起点
        d. maps_vendor.eta(origin, dest, mode) → ETA 秒数
        e. writer.insert_buffer → 写 DB
    4. 检查 dismissed_buffer_ledger: 曾经 dismiss 过的 origin+dest 组合不再插
    5. 返回 ScanReport: 插入 / 跳过 / 询问 三类计数

注入点:
    user_profile: dict (或 None) — 来自 user_profile.load_travel_profile()
    ledger_sink:  dismiss 时回调 (供 user_profile.append_dismissed_buffer)
    ask_user_fn:   冲突/ambiguity 时调用, 默认 mock (返回 "skip")
    eta_fn:        ETA 计算, 默认 maps_vendor.eta
    geocode_fn:    地址解析, 默认 maps_vendor.geocode
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from prisIr_calendar.semantics.chain import Event as SemEvent
from prisIr_calendar.semantics.venue import Venue, resolve_venue
from prisIr_calendar.semantics.classify import should_skip_event
from prisIr_calendar.semantics.chain import choose_origin_for_event
from prisIr_calendar.tools import maps_vendor
from prisIr_calendar.store import CalendarStore

from .reader import list_window
from .writer import (
    add_user_event,
    dismiss_event,
    insert_buffer,
    Buffer,
    BUFFER_TITLE,
    SMALL_PAD_SECONDS,
    ROUND_STEP_MINUTES,
)
from .prompts import (
    Ambiguity,
    one_at_a_time,
    render_ambiguous_place,
    render_unknown_wfh,
    render_unclear_mode,
)

log = logging.getLogger("prisIr_calendar.agent_ops.travel_buffer")


# ============================================================
# 类型别名 (注入契约)
# ============================================================
EtaFn = Callable[[str, str, str], Awaitable[Dict[str, Any]]]
GeocodeFn = Callable[[str], Awaitable[Dict[str, Any]]]
AskUserFn = Callable[[Ambiguity], Awaitable[str]]  # Phase 1 一律 async
LedgerSinkFn = Callable[[Dict[str, Any]], None]


# ============================================================
# ScanReport — 一次 scan 的统计
# ============================================================
@dataclass
class ScanReport:
    """scan_and_protect 的结果汇总.

    字段:
        inserted: 成功插入的 Buffer 列表
        skipped:   跳过的 event_id → skip_reason 映射
        asked:     询问过的 Ambiguity 列表
        yielded:   让位的 event_id 列表 (冲突 → 让位, 不插)
        errors:    错误明细 list[str]
    """
    inserted: List[Buffer] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    asked: List[Ambiguity] = field(default_factory=list)
    yielded: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def inserted_count(self) -> int:
        return len(self.inserted)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def asked_count(self) -> int:
        return len(self.asked)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inserted_count": self.inserted_count,
            "skipped_count": self.skipped_count,
            "asked_count": self.asked_count,
            "yielded_count": len(self.yielded),
            "error_count": len(self.errors),
            "inserted": [
                {"event_id": b.event_id, "duration_min": b.duration_min,
                 "buffer_id": b.buffer_id, "conflict": b.conflict}
                for b in self.inserted
            ],
            "skipped": dict(self.skipped),
            "asked": [{"kind": a.kind.value, "message": a.message} for a in self.asked],
            "yielded": list(self.yielded),
            "errors": list(self.errors),
        }


# ============================================================
# TravelBufferAgent — 主入口
# ============================================================
class TravelBufferAgent:
    """旅行缓冲 agent — 完整 scan_and_protect 流程编排."""

    def __init__(
        self,
        store: CalendarStore,
        *,
        user_profile: Optional[Dict[str, Any]] = None,
        ledger_sink: Optional[LedgerSinkFn] = None,
        ask_user_fn: Optional[AskUserFn] = None,
        eta_fn: Optional[EtaFn] = None,
        geocode_fn: Optional[GeocodeFn] = None,
    ):
        """构造.

        Args:
            store: CalendarStore
            user_profile: 出行画像 dict (来自 user_profile.load_travel_profile())
                          None → 视为空画像 (走 PROMPT_UNKNOWN_WFH)
            ledger_sink: dismiss 时回调, 接收 dict 记录
            ask_user_fn: 冲突/ambiguity 时调用, 返回用户答复字符串
            eta_fn: ETA 计算, 默认 maps_vendor.eta
            geocode_fn: 地址解析, 默认 maps_vendor.geocode
        """
        self.store = store
        self.user_profile = user_profile or {}
        self.ledger_sink = ledger_sink
        self.ask_user_fn = ask_user_fn or self._default_ask_user
        self.eta_fn = eta_fn or maps_vendor.eta
        self.geocode_fn = geocode_fn or maps_vendor.geocode

    # --------------------------------------------------------
    # 默认 ask_user (无注入时静默 skip)
    # --------------------------------------------------------
    async def _default_ask_user(self, ambiguity: Ambiguity) -> str:
        """无 ask_user_fn 注入时的默认行为: 静默 skip (返回 'skip').

        生产环境由 UI 层注入真实 ask_user_fn.
        """
        log.info("travel_buffer: ask_user skipped (no sink wired): %s", ambiguity.message)
        return "skip"

    # --------------------------------------------------------
    # 询问入口 (UI 层可独立调用)
    # --------------------------------------------------------
    async def ask_user(self, question: Ambiguity) -> str:
        """一次只问一个清晰问题 (契约 #9)."""
        return await self.ask_user_fn(question)

    # --------------------------------------------------------
    # 核心: scan_and_protect
    # --------------------------------------------------------
    async def scan_and_protect(self, *, days: int = 14) -> ScanReport:
        """完整流程 (travel-time-buddy 9 条契约).

        流程:
            1. 读 workplace_addresses + default_travel_mode
            2. list_window(days)
            3. 逐 event 处理 (skip / venue / chain / ETA / insert_buffer)
            4. 检查 dismissed_buffer_ledger
            5. 返回 ScanReport
        """
        report = ScanReport()

        # 1) 出行画像
        workplace = (self.user_profile or {}).get("workplace_addresses") or {}
        office = workplace.get("office")
        home = workplace.get("home")
        default_mode = (self.user_profile or {}).get("default_travel_mode")

        # 1a) 没有 workplace.office → 走 PROMPT_UNKNOWN_WFH
        if not office and not home:
            q = render_unknown_wfh()
            report.asked.append(q)
            # 询问一次;无 sink 时默认 skip(直接结束 — 没有起点信息不能继续)
            answer = await self.ask_user(q)
            if answer == "skip" or not answer:
                # 没拿到 workplace → 全部 events skip
                # 但仍要 list 一遍给个总数 (契约 #1 仍然返回)
                try:
                    events = await list_window(self.store, days=days)
                except Exception as e:  # noqa: BLE001
                    report.errors.append(f"list_window failed: {e}")
                    return report
                for ev in events:
                    report.skipped[ev.event_id] = "no_workplace"
                return report

        # 2) 14 天时间窗
        try:
            events = await list_window(self.store, days=days)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"list_window failed: {e}")
            return report

        if not events:
            return report

        # 3) 逐 event 处理
        # 先 scan_chain 拿到相邻 in-person 链接 — 用于 choose_origin_for_event
        from prisIr_calendar.semantics.chain import scan_chain
        chain_links = await scan_chain(events, workplace=workplace, default_mode=default_mode or "car")

        # 建索引: next_event_id → origin_event_id (供快速查"上一场是哪个")
        origin_by_next: Dict[str, str] = {
            link.next_event_id: link.origin_event_id for link in chain_links
        }

        # 之前处理过的 event_id (用于 choose_origin_for_event 时找 prior_event)
        last_processed: Dict[str, SemEvent] = {}

        for ev in events:
            await self._process_one_event(
                ev,
                report=report,
                workplace=workplace,
                default_mode=default_mode,
                origin_by_next=origin_by_next,
                last_processed=last_processed,
            )
            # 维护 last_processed (按 dtstart 升序, 自然顺序就是按时间)
            last_processed[ev.event_id] = ev

        return report

    # --------------------------------------------------------
    # 单 event 处理(便于被外部单点调用)
    # --------------------------------------------------------
    async def _process_one_event(
        self,
        ev: SemEvent,
        *,
        report: ScanReport,
        workplace: Dict[str, Any],
        default_mode: Optional[str],
        origin_by_next: Dict[str, str],
        last_processed: Dict[str, SemEvent],
    ) -> None:
        """处理单个 event (供 scan_and_protect 串行调用)."""
        # a) classify.should_skip_event
        skip, reason = should_skip_event(ev, workplace=workplace)
        if skip:
            report.skipped[ev.event_id] = reason
            return

        # b) venue 已 resolve (由 reader 完成); 二次确认 workplace_diff
        if ev.venue is None or not ev.venue.normalized:
            report.skipped[ev.event_id] = "no_venue"
            return

        from prisIr_calendar.semantics.venue import is_workplace_diff
        if not is_workplace_diff(ev.venue, workplace):
            report.skipped[ev.event_id] = "same_workplace"
            return

        # c) chain 起点
        prior_id = origin_by_next.get(ev.event_id)
        prior_event = last_processed.get(prior_id) if prior_id else None
        origin = choose_origin_for_event(ev, prior_event, workplace)
        if not origin:
            report.skipped[ev.event_id] = "no_origin"
            return

        # d) ETA
        mode = default_mode or "car"
        try:
            eta_res = await self.eta_fn(origin, ev.venue.normalized, mode)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"eta failed for {ev.event_id}: {e}")
            report.skipped[ev.event_id] = "eta_error"
            return

        if not eta_res.get("ok"):
            # ETA 失败 → ambiguous (走 PROMPT_AMBIGUOUS_PLACE 或 PROMPT_UNCLEAR_MODE)
            ambiguity = one_at_a_time([
                render_ambiguous_place(ev.venue.raw or ev.venue.normalized),
                render_unclear_mode(origin, ev.venue.normalized),
            ])
            if ambiguity is not None:
                report.asked.append(ambiguity)
                report.skipped[ev.event_id] = f"asked:{ambiguity.kind.value}"
            return

        eta_seconds = int(eta_res.get("duration_seconds") or 0)
        if eta_seconds <= 0:
            report.skipped[ev.event_id] = "zero_eta"
            return

        # 4) dismissed_buffer_ledger 检查 (契约 #8: dismiss = 政策, 永不重做)
        if self._was_dismissed(origin, ev.venue.normalized, mode):
            report.skipped[ev.event_id] = "previously_dismissed"
            return

        # e) insert_buffer
        buf = await insert_buffer(
            self.store,
            for_event_id=ev.event_id,
            before=True,
            eta_seconds=eta_seconds,
            mode=mode,
            vendor=eta_res.get("source") or "manual",
            origin=origin,
            destination=ev.venue.normalized,
            buffer_type="travel",
            ask_user_fn=self.ask_user_fn,
        )

        if buf.inserted:
            report.inserted.append(buf)
        elif buf.conflict == "yielded_to_user_event":
            report.yielded.append(ev.event_id)
            if buf.ambiguity is not None:
                report.asked.append(buf.ambiguity)
        elif buf.conflict == "shrunk":
            # 缩短后插入成功 — 也算 inserted
            report.inserted.append(buf)
        else:
            report.skipped[ev.event_id] = buf.conflict or "unknown_conflict"

    # --------------------------------------------------------
    # dismissed ledger 查询 (契约 #8)
    # --------------------------------------------------------
    def _was_dismissed(self, origin: str, destination: str, mode: str) -> bool:
        """origin+dest+mode 三元组是否在 dismissed_buffer_ledger 里."""
        ledger = (self.user_profile or {}).get("dismissed_buffer_ledger") or []
        if not ledger:
            return False
        for rec in ledger:
            if not isinstance(rec, dict):
                continue
            if (rec.get("origin") == origin
                    and rec.get("destination") == destination
                    and (rec.get("mode") or "") == (mode or "")):
                return True
        return False


# ============================================================
# 高层便捷函数 (供 task #6 UI / http_api 直接调用)
# ============================================================
async def protect_now(
    store: CalendarStore,
    *,
    user_profile: Optional[Dict[str, Any]] = None,
    days: int = 14,
    ledger_sink: Optional[LedgerSinkFn] = None,
    ask_user_fn: Optional[AskUserFn] = None,
) -> ScanReport:
    """单次 scan_and_protect 快捷入口.

    对应场景: 用户加完事件后, agent 立刻跑一次保护(走默认注入).
    """
    agent = TravelBufferAgent(
        store,
        user_profile=user_profile,
        ledger_sink=ledger_sink,
        ask_user_fn=ask_user_fn,
    )
    return await agent.scan_and_protect(days=days)


__all__ = [
    "TravelBufferAgent",
    "ScanReport",
    "Ambiguity",
    "protect_now",
]
