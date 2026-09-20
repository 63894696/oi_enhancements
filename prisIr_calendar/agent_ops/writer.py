# -*- coding: utf-8 -*-
"""
writer.py — Agent 层写入 API (task #5)

公共 API:
    add_user_event(store, *, summary, dtstart, dtend, venue_raw="") -> Event
    dismiss_event(store, *, event_id, reason, ledger_sink=None) -> None
    insert_buffer(store, *, for_event_id, before, eta_seconds, mode, vendor) -> Buffer

对齐契约:
    - travel-time-buddy.md 契约 #5 ETA + 小 buffer, rounded 到分钟
    - travel-time-buddy.md 契约 #6 标题 "Travel time", 元数据塞 description
    - travel-time-buddy.md 契约 #7 冲突不静默覆盖 — 询问或缩短
    - travel-time-buddy.md 契约 #8 dismiss = 政策, 永不重做 (ledger 留痕)

拍板决策 (来自任务说明):
    - SMALL_PAD = 60s
    - rounded 步长 = 5 分钟
    - buffer vs 已有 buffer 重叠 → 缩短 (不询问)
    - buffer vs 用户事件重叠 → 询问 (走 PROMPT_BUFFER_DESTROYS_DAY)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from prisIr_calendar.semantics.chain import Event
from prisIr_calendar.semantics.venue import Venue, resolve_venue
from prisIr_calendar.store import CalendarStore, EventRecord, BufferRecord

log = logging.getLogger("prisIr_calendar.agent_ops.writer")


# ============================================================
# 拍板常量 (来自任务说明)
# ============================================================
SMALL_PAD_SECONDS = 60
ROUND_STEP_MINUTES = 5
BUFFER_TITLE = "Travel time"  # 契约 #6 严格标题


# ============================================================
# Buffer 数据类 (writer 内部返回; 与 store.BufferRecord 区分)
# ============================================================
@dataclass
class Buffer:
    """插入的 buffer 描述.

    与 store.BufferRecord 字段对齐, 加 inserted (True=成功, False=失败) 和 conflict 标记.
    """
    buffer_id: str
    event_id: str
    type: str
    duration_min: int
    inserted_before: str
    inserted_after: str
    metadata: Optional[Dict[str, Any]] = None
    inserted: bool = True
    conflict: Optional[str] = None  # "yielded_to_user_event" / "shrunk" / None
    ambiguity: Optional[Any] = None  # prompts.Ambiguity (冲突让位时填)


# ============================================================
# helpers
# ============================================================
def _parse_iso(s: str) -> Optional[datetime]:
    """解析 ISO8601 → tz-aware datetime (UTC)."""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    """datetime → UTC ISO8601 字符串."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _round_up_to_step(total_seconds: int, step_minutes: int = ROUND_STEP_MINUTES) -> int:
    """rounded 到 step_minutes 分钟 (向上取整, 契约 #5).

    例: 60+60=120s (2min) → step=5min → 5min = 300s
        60+60+90=210s (3.5min) → step=5min → 5min = 300s
    """
    step_seconds = step_minutes * 60
    if total_seconds <= step_seconds:
        return step_seconds
    # ceil to step
    n = (total_seconds + step_seconds - 1) // step_seconds
    return n * step_seconds


def _conflicts_with_user_event(
    store: CalendarStore,
    buf_start: datetime,
    buf_end: datetime,
    exclude_event_id: str,
) -> Optional[EventRecord]:
    """检测 buffer 区间是否与已有用户事件重叠.

    Returns:
        首个冲突的 EventRecord (含 dismissed=0 + 非 agent), 否则 None
    """
    start_iso = _iso(buf_start)
    end_iso = _iso(buf_end)
    # 时间窗交集 (event.dtend > buf_start AND event.dtstart < buf_end)
    rows: List[EventRecord] = store.list_events(
        start_iso, end_iso, include_dismissed=False, sources=("user",),
    )
    for rec in rows:
        if rec.event_id == exclude_event_id:
            continue
        ev_start = _parse_iso(rec.dtstart_utc)
        ev_end = _parse_iso(rec.dtend_utc)
        if ev_start is None or ev_end is None:
            continue
        # 半开区间交集
        if ev_start < buf_end and ev_end > buf_start:
            return rec
    return None


def _conflicts_with_existing_buffer(
    store: CalendarStore,
    event_id: str,
    buf_start: datetime,
    buf_end: datetime,
) -> bool:
    """检测 buffer 区间是否与已有 buffer 重叠 (同一 event)."""
    buffers = store.list_buffers(event_id)
    for b in buffers:
        b_start = _parse_iso(b.inserted_before)
        b_end = _parse_iso(b.inserted_after)
        if b_start is None or b_end is None:
            continue
        if b_start < buf_end and b_end > buf_start:
            return True
    return False


def _shrink_to_fit_existing_buffer(
    store: CalendarStore,
    event_id: str,
    desired_start: datetime,
    desired_end: datetime,
) -> Tuple[datetime, datetime]:
    """缩短 buffer 区间, 避开已有 buffer (不询问 — 拍板决策).

    策略:
        1. 取已有 buffer 中 latest ending 的时刻 latest_end
        2. 取 latest_start (latest buffer 的起点) — 这是关键: 我们的新 buffer
           必须在 latest_start 之前结束 (或与 latest buffer 完全不重叠)
        3. 把 new_end 设为 latest_start - 1s (避免重叠)
        4. 必要时缩短 duration

    Returns:
        (cur_s, cur_e) 已避开冲突;duration 可能比 desired 短
        (上层 insert 时拿到 conflict='shrunk' 标记)
    """
    buffers = store.list_buffers(event_id)
    if not buffers:
        return desired_start, desired_end

    # 找已有 buffer 中 "位置最靠前结束" 的时刻,以及对应的"起点"
    # 我们要避开整个 latest_start..latest_end 区间
    parsed = []
    for b in buffers:
        s = _parse_iso(b.inserted_before)
        e = _parse_iso(b.inserted_after)
        if s and e:
            parsed.append((s, e))
    if not parsed:
        return desired_start, desired_end

    # 找 latest_end (max ending) — 它的起点就是 latest_start
    latest_buf = max(parsed, key=lambda x: x[1])
    latest_start = latest_buf[0]
    latest_end = latest_buf[1]

    desired_duration = (desired_end - desired_start).total_seconds()
    # 强制保留 5min 下限
    target_duration = max(desired_duration, ROUND_STEP_MINUTES * 60)

    # 第一阶段: new_end 在 latest_start 之前 (留 1s 安全 gap)
    new_end = latest_start - timedelta(seconds=1)
    # 起点允许在 desired_start 之后 (不会比 desired 更早)
    new_start = new_end - timedelta(seconds=target_duration)

    # 若 new_start < desired_start, 缩短 duration
    if new_start < desired_start:
        new_start = desired_start
        # new_end 取 latest_start - 1s (已经在 latest 之前)
        # duration = latest_start - 1s - desired_start
        max_dur = (new_end - new_start).total_seconds()
        if max_dur < ROUND_STEP_MINUTES * 60:
            # 实在塞不下 → 强制 5min, 起点 = new_end - 5min
            new_start = new_end - timedelta(seconds=ROUND_STEP_MINUTES * 60)
            # 还 < desired_start → 至少保 desired_start
            if new_start < desired_start:
                new_start = desired_start
                new_end = new_start + timedelta(seconds=ROUND_STEP_MINUTES * 60)
                # 仍 > latest_start → 截断到 latest_start - 1s
                if new_end > latest_start - timedelta(seconds=1):
                    new_end = latest_start - timedelta(seconds=1)
                    new_start = new_end - timedelta(seconds=ROUND_STEP_MINUTES * 60)
        # else: 用默认 new_start = desired_start, new_end = latest_start - 1s (duration < target)

    return new_start, new_end


# ============================================================
# 公开 API
# ============================================================
async def add_user_event(
    store: CalendarStore,
    *,
    summary: str,
    dtstart: str,
    dtend: str,
    venue_raw: str = "",
    description: Optional[str] = None,
) -> Event:
    """用户告知事件 → 走 venue resolve → 入库.

    Args:
        store: CalendarStore
        summary: 事件标题
        dtstart: UTC ISO8601 字符串 (e.g. "2026-09-19T15:00:00+00:00")
        dtend:   UTC ISO8601 字符串
        venue_raw: 用户口语化地址 (e.g. "国贸 SK") — 走 resolve_venue
        description: 描述 (用户原话等)

    Returns:
        Event (语义层), 已写库
    """
    venue_id: Optional[str] = None
    venue_obj: Optional[Venue] = None
    if venue_raw and venue_raw.strip():
        venue_obj = await resolve_venue(venue_raw, store=store)
        venue_id = venue_obj.venue_id

    rec = EventRecord(
        event_id="",
        summary=summary,
        description=description,
        dtstart_utc=dtstart,
        dtend_utc=dtend,
        timezone="Asia/Shanghai",
        venue_id=venue_id,
        source="user",
    )
    new_id = await store.add_event_async(rec)

    return Event(
        event_id=new_id,
        summary=summary,
        description=description,
        dtstart_utc=dtstart,
        dtend_utc=dtend,
        venue=venue_obj,
        dismissed=False,
        source="user",
    )


async def dismiss_event(
    store: CalendarStore,
    *,
    event_id: str,
    reason: str,
    ledger_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    """软删除事件 + 写 ledger + 回调 ledger_sink.append_dismissed_buffer (契约 #8).

    Args:
        store: CalendarStore
        event_id: 事件 ID
        reason: dismiss 原因 ∈ {"user_clicked_x", "user_edited", "user_moved"}
        ledger_sink: 可选回调, 接收 dict 记录 (供 user_profile.append_dismissed_buffer
                     直接调用 — 测试可注入 mock 验证)

    Returns:
        True if dismissed, False if not found / already dismissed
    """
    ok = await store.dismiss_event_async(event_id, reason=reason)
    if not ok:
        return False

    # ledger_sink 回调: 用 dismiss 时的 event 元数据(origin/dest/mode 走 best-effort)
    if ledger_sink is not None:
        try:
            rec = store.get_event(event_id)
            # best-effort: 这里拿不到 buffer 元数据,origin/dest 给空串占位
            # 真实生产链路里 buffer_id 已存,调用方应主动传更完整 dict
            ledger_sink({
                "event_id": event_id,
                "buffer_id": "",  # dismiss 的是事件本身,不是单条 buffer
                "origin": "",
                "destination": "",
                "mode": "",
                "reason": reason,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            })
        except Exception as e:  # noqa: BLE001 — 回调失败不影响主流程
            log.warning("dismiss_event: ledger_sink raised %s", e)
    return True


async def insert_buffer(
    store: CalendarStore,
    *,
    for_event_id: str,
    before: bool,
    eta_seconds: int,
    mode: str,
    vendor: str,
    origin: str = "",
    destination: str = "",
    buffer_type: str = "travel",
    ask_user_fn: Optional[Callable[[Any], Any]] = None,
) -> Buffer:
    """插入交通/休息缓冲 (契约 #5, #6, #7).

    Args:
        store: CalendarStore
        for_event_id: 依附事件 ID
        before: True=在事件之前;False=之后 (Phase 1 暂只支持 before)
        eta_seconds: Maps ETA 秒数 (来自 maps_vendor.eta)
        mode: "car" / "transit" / "walking" / "bicycling"
        vendor: "amap" / "google" / "manual"
        origin / destination: 给 metadata 留痕 (供 ledger / UI 回看)
        buffer_type: "travel" / "rest" / "preparation" / "cooldown" (默认 travel)
        ask_user_fn: 冲突让位时的询问回调 — 接收 prompts.Ambiguity, 返回用户答复字符串.
                     None = 不询问, 直接让位 (测试可注入 mock)

    Returns:
        Buffer 对象 (inserted=True / False 标记是否真插了)
    """
    if not before:
        # Phase 1 简化: 不支持 after-buffer
        log.warning("insert_buffer: after-buffer not supported yet (event_id=%s)", for_event_id)
        return Buffer(
            buffer_id="",
            event_id=for_event_id,
            type=buffer_type,
            duration_min=0,
            inserted_before="",
            inserted_after="",
            inserted=False,
            conflict="after_not_supported",
        )

    # 1) 拿到依附事件的 dtstart
    ev = store.get_event(for_event_id)
    if ev is None:
        log.warning("insert_buffer: event_id=%s not found", for_event_id)
        return Buffer(
            buffer_id="",
            event_id=for_event_id,
            type=buffer_type,
            duration_min=0,
            inserted_before="",
            inserted_after="",
            inserted=False,
            conflict="event_not_found",
        )

    ev_start = _parse_iso(ev.dtstart_utc)
    if ev_start is None:
        return Buffer(
            buffer_id="",
            event_id=for_event_id,
            type=buffer_type,
            duration_min=0,
            inserted_before="",
            inserted_after="",
            inserted=False,
            conflict="invalid_dtstart",
        )

    # 2) 计算 buffer 时长: ETA + SMALL_PAD, rounded 到 5 分钟
    raw_seconds = int(eta_seconds) + SMALL_PAD_SECONDS
    rounded_seconds = _round_up_to_step(raw_seconds)

    # buffer 在事件之前: inserted_before = 起点, inserted_after = 事件起点
    buf_start = ev_start - timedelta(seconds=rounded_seconds)
    buf_end = ev_start

    # 3) 冲突检测: 与已有 buffer 重叠 → 缩短 (不询问)
    conflict_marker: Optional[str] = None
    if _conflicts_with_existing_buffer(store, for_event_id, buf_start, buf_end):
        buf_start, buf_end = _shrink_to_fit_existing_buffer(
            store, for_event_id, buf_start, buf_end,
        )
        # 重新算 rounded (缩短后可能又不到 step, 至少保 5min)
        duration_seconds = max(int((buf_end - buf_start).total_seconds()), ROUND_STEP_MINUTES * 60)
        conflict_marker = "shrunk"
    else:
        duration_seconds = rounded_seconds

    duration_min = max(1, duration_seconds // 60)

    # 4) 冲突检测: 与用户事件重叠 → 询问 (走 PROMPT_BUFFER_DESTROYS_DAY)
    user_conflict = _conflicts_with_user_event(store, buf_start, buf_end, for_event_id)
    if user_conflict is not None:
        from .prompts import render_buffer_destroys_day, Ambiguity
        # 估算 short_min (取 duration_min 的一半, 至少 5min)
        short_min = max(5, duration_min // 2)
        ambiguity = render_buffer_destroys_day(
            summary=user_conflict.summary or "",
            eta_min=duration_min,
            short_min=short_min,
        )
        answer: Optional[str] = None
        if ask_user_fn is not None:
            try:
                answer = ask_user_fn(ambiguity)
                if asyncio.iscoroutine(answer):
                    answer = await answer
            except Exception as e:  # noqa: BLE001
                log.warning("insert_buffer: ask_user_fn raised %s", e)
                answer = None
        # 判定: answer 含 "短"/"short"/"shorter" → 缩到 short_min 插
        if isinstance(answer, str) and ("短" in answer or "short" in answer.lower() or "shorter" in answer.lower()):
            duration_min = short_min
            duration_seconds = duration_min * 60
            buf_start = ev_start - timedelta(seconds=duration_seconds)
            buf_end = ev_start
            # 走原插入路径
        else:
            # 没拿到答案 / 用户答 "next_day" / "skip" / 异常 → 让位
            _record_yield(store, for_event_id, reason="user_event_conflict")
            return Buffer(
                buffer_id="",
                event_id=for_event_id,
                type=buffer_type,
                duration_min=0,
                inserted_before=_iso(buf_start),
                inserted_after=_iso(buf_end),
                inserted=False,
                conflict="yielded_to_user_event",
                ambiguity=ambiguity,
            )

    # 5) 写库
    metadata = {
        "vendor": vendor,
        "eta_seconds": int(eta_seconds),
        "mode": mode,
        "origin": origin,
        "destination": destination,
        "buffer_title": BUFFER_TITLE,
    }

    buffer_id = await store.insert_buffer_async(
        event_id=for_event_id,
        type=buffer_type,
        duration_min=duration_min,
        inserted_before=_iso(buf_start),
        inserted_after=_iso(buf_end),
        metadata=metadata,
    )

    return Buffer(
        buffer_id=buffer_id,
        event_id=for_event_id,
        type=buffer_type,
        duration_min=duration_min,
        inserted_before=_iso(buf_start),
        inserted_after=_iso(buf_end),
        metadata=metadata,
        inserted=True,
        conflict=conflict_marker,
    )


def _record_yield(store: CalendarStore, event_id: str, *, reason: str) -> None:
    """buffer 让位 → 写一条 ledger (供回放)."""
    try:
        store.record_ledger(
            actor="agent",
            action="buffer_yield",
            target_event_id=event_id,
            rationale=reason,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("_record_yield: %s", e)


__all__ = [
    "Buffer",
    "SMALL_PAD_SECONDS",
    "ROUND_STEP_MINUTES",
    "BUFFER_TITLE",
    "add_user_event",
    "dismiss_event",
    "insert_buffer",
]
