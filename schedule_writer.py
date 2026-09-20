# -*- coding: utf-8 -*-
"""日程/任务 写入执行器 (Phase P2.5+8, 2026-09-20)

把 schedule_extractor.distill_schedule_sync 的输出真正落到:
  - 日历:CalendarStore.add_event_async(EventRecord)
  - todo 扩展:_ext_rpc_call("todo", "todo.add", ...)
  - pomodoro:只 log suggestion,不主动 start(用户必须自己开始)

失败静默:全 try/except + log.warning,不阻塞对话。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("schedule_writer")


async def write_to_calendar(events: list[dict]) -> list[str]:
    """events → CalendarStore.add_event_async。返写入成功的 event_id 列表。"""
    if not events:
        return []
    try:
        from prisIragent_web import get_calendar_store
        from prisIr_calendar.store import EventRecord
        from schedule_extractor import validate_iso
    except Exception as e:  # noqa: BLE001
        log.warning("write_to_calendar import err: %s", e)
        return []
    store = get_calendar_store()
    if store is None:
        log.warning("write_to_calendar: store unavailable")
        return []
    ids: list[str] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        dtstart = validate_iso(e.get("dtstart"))
        dtend = validate_iso(e.get("dtend"))
        summary = str(e.get("summary") or "").strip()
        if not summary or not dtstart or not dtend:
            log.warning("write_to_calendar skip invalid: %s", e)
            continue
        try:
            rec = EventRecord(
                event_id="",
                summary=summary[:200],
                description=e.get("description"),
                dtstart_utc=dtstart,
                dtend_utc=dtend,
                timezone=str(e.get("timezone") or "Asia/Shanghai"),
                source="ai_extracted",
            )
            eid = await store.add_event_async(rec)
            ids.append(eid)
        except Exception as ex:  # 单条失败不影响其他
            log.warning("write_to_calendar add_event fail: %s", ex)
    return ids


def write_to_todo(todos: list[dict]) -> int:
    """todos → _ext_rpc_call todo.add。返写入成功的条数。
       P2.5+B-0(2026-09-21):_ext_rpc_call 新契约返 {result: dict} | {error: str},
       旧版 if r.get("ok") or r.get("item") 全失效,改成 if not error.
    """
    if not todos:
        return 0
    try:
        from prisIragent_web import _ext_rpc_call
    except Exception as e:  # noqa: BLE001
        log.warning("write_to_todo import err: %s", e)
        return 0
    n = 0
    for t in todos:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        priority = t.get("priority") if t.get("priority") in ("high", "mid", "low") else "mid"
        tags = t.get("tags") if isinstance(t.get("tags"), list) else []
        try:
            r = _ext_rpc_call("todo", "todo.add", {
                "title": title[:200],
                "due": t.get("due"),
                "priority": priority,
                "tags": [str(x)[:32] for x in tags][:8],
            }, timeout=5)
            if not isinstance(r, dict):
                log.warning("write_to_todo: bad rpc return %r", r)
                continue
            if r.get("error"):
                err = r["error"]
                log.warning("write_to_todo rpc err: %s", err)
                if "ext_not_running" in str(err):
                    break  # 整个 ext 不在,不再试
                continue
            # r["result"] 形如 {ok: True, item: {...}} / {ok: True, id: ...} 由 todo ext 决定
            n += 1
        except Exception as ex:
            log.warning("write_to_todo fail: %s", ex)
    return n


def log_pomo_suggestion(pomo: dict) -> int:
    """pomodoro 只 log suggestion,不 start。返 suggest 条数。"""
    if not pomo or not isinstance(pomo, dict):
        return 0
    suggests = pomo.get("suggest") or []
    if not isinstance(suggests, list) or not suggests:
        return 0
    reason = str(pomo.get("reason") or "")
    log.info("[schedule] pomo suggestion: %s | reason=%s", suggests, reason)
    return len(suggests)


def record_history(entries: list[dict]) -> None:
    """写入 _SCHEDULE_TRIGGERS_HISTORY(模块级 list),供 /api/schedule/history 查。"""
    if not entries:
        return
    try:
        from prisIragent_web import _SCHEDULE_TRIGGERS_HISTORY
        import time as _t
        for e in entries:
            e2 = dict(e)
            e2.setdefault("ts", _t.time())
            _SCHEDULE_TRIGGERS_HISTORY.append(e2)
        # 限长 200,环形裁剪
        if len(_SCHEDULE_TRIGGERS_HISTORY) > 200:
            del _SCHEDULE_TRIGGERS_HISTORY[:-200]
    except Exception as ex:
        log.warning("record_history err: %s", ex)


async def write_all(extracted: dict) -> dict:
    """一次性写入 events/todos + log pomo。返统计 + record history。"""
    events = extracted.get("events") or []
    todos = extracted.get("todos") or []
    pomo = extracted.get("pomo") or {}
    eids = await write_to_calendar(events)
    ntodos = write_to_todo(todos)
    npomo = log_pomo_suggestion(pomo)
    history = []
    if eids:
        history.append({"kind": "events", "ids": eids, "count": len(eids)})
    if ntodos:
        history.append({"kind": "todos", "count": ntodos})
    if npomo:
        history.append({"kind": "pomo_suggest", "count": npomo,
                        "reason": pomo.get("reason", "")})
    record_history(history)
    return {"events": len(eids), "todos": ntodos, "pomo_suggest": npomo}