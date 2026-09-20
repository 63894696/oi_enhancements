# -*- coding: utf-8 -*-
"""
store.py — prisIr_calendar 存储层 (Phase 1)

封装:
    - sqlite3 + WAL
    - asyncio.Lock (单进程内写串行化)
    - ICS 导出 (RFC 5545 + X-PRISIR-* 扩展)

设计:
    - 不用 SQLAlchemy (设计文档拍板)
    - 用 stdlib sqlite3 + zoneinfo (Python 3.9+)
    - 用 icalendar 库做 ICS 序列化, recurring-ical-events 做循环展开
    - 写路径: 全部走 asyncio.Lock, busy_timeout=5000ms

依赖 (声明在 setup, Phase 1 不强装):
    - icalendar>=7.0
    - recurring-ical-events>=3.0
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import icalendar
from icalendar.prop import vText, vBoolean

# recurring-ical-events 可选依赖, Phase 1 仅在 list_events 展开循环时用到
try:
    import recurring_ical_events  # type: ignore
    _HAS_RECURR = True
except ImportError:
    _HAS_RECURR = False

log = logging.getLogger("prisIr_calendar.store")


# ============================================================
# 数据类 (序列化字典的轻量包装, dataclass 优先)
# ============================================================
@dataclass
class EventRecord:
    """events 表行 — 见 schema.sql 字段说明"""
    event_id: str
    summary: str
    description: Optional[str]
    dtstart_utc: str
    dtend_utc: str
    timezone: str = "Asia/Shanghai"
    venue_id: Optional[str] = None
    source: str = "user"
    chain_origin: Optional[str] = None
    workplace_diff: Optional[str] = None
    dismissed: bool = False
    dismissed_at: Optional[str] = None
    dismiss_reason: Optional[str] = None
    rrule: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EventRecord":
        d = dict(row)
        # JSON 字段解包 (workplace_diff)
        wd = d.get("workplace_diff")
        if wd and isinstance(wd, str):
            try:
                d["workplace_diff"] = json.loads(wd)
            except (json.JSONDecodeError, TypeError):
                pass
        # dismissed 0/1 → bool
        d["dismissed"] = bool(d.get("dismissed", 0))
        return cls(**d)


@dataclass
class BufferRecord:
    """buffers 表行"""
    buffer_id: str
    event_id: str
    type: str
    duration_min: int
    inserted_after: str
    inserted_before: str
    metadata: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "BufferRecord":
        return cls(**dict(row))


@dataclass
class LedgerRecord:
    """ledger 表行"""
    ledger_id: int
    occurred_at: str
    actor: str
    action: str
    target_event_id: Optional[str] = None
    target_buffer_id: Optional[str] = None
    policy_snapshot: Optional[str] = None
    rationale: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LedgerRecord":
        return cls(**dict(row))


# ============================================================
# CalendarStore — 主入口
# ============================================================
class CalendarStore:
    """封装 sqlite3 + WAL + asyncio.Lock.

    线程模型:
        - asyncio.Lock 在单进程内串行化写
        - 同一进程多个 event loop 共享同一 lock (实例方法)
        - 跨进程并发: WAL 多读单写, busy_timeout 兜底

    用法:
        store = CalendarStore(Path("data/calendar.db"))
        store.init_schema()
        async with store._lock:        # 写路径
            store.add_event(...)
        await store.add_event_async(...)
    """

    SCHEMA_PATH = Path(__file__).parent / "schema.sql"

    def __init__(self, db_path: Path | str, *, schema_path: Optional[Path] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.schema_path = schema_path or self.SCHEMA_PATH
        # asyncio.Lock 必须绑定到 event loop, 这里只声明, init_schema() 时懒创建
        self._lock: Optional[asyncio.Lock] = None
        # 同步入口的 threading.Lock (init_schema 调用方可能是同步)
        self._init_lock = asyncio.Lock() if asyncio.get_event_loop_policy() else None
        self._initialized = False

    # --------------------------------------------------------
    # 初始化
    # --------------------------------------------------------
    def init_schema(self) -> None:
        """读 schema.sql, 跑 DDL. 幂等 (IF NOT EXISTS)."""
        if self._initialized:
            return
        sql = self.schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            # PRAGMA 要逐条 execute, 不支持多语句一次性 exec
            for stmt in _split_statements(sql):
                conn.execute(stmt)
            # P2.5+8(2026-09-20):迁移 source CHECK 约束,允许 'ai_extracted'
            # 老 DB 上 CHECK 仍为 IN ('user','agent'),需要重建 events 表。
            # 用 PRAGMA table_info 看 sqlite_master 中 CHECK 子句太脆,直接重写 events 表最稳。
            self._migrate_add_ai_extracted_source(conn)
            conn.commit()
        self._initialized = True
        log.info("calendar schema initialized at %s", self.db_path)

    def _migrate_add_ai_extracted_source(self, conn) -> None:
        """P2.5+8:events.source 扩到 IN ('user','agent','ai_extracted'),
        顺便补 rrule 列(老表若有漏)。
        老 DB 表重建法:把 events 全量拷到 events_new(放宽 CHECK + 含 rrule 列),
        索引同 schema.sql 顺序重建,最后替换原表。"""
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if not row or not row[0]:
            return  # 表还没建,让 schema.sql 走标准 CREATE 流程
        sql_text = row[0]
        # 已迁移?如果 CHECK 含 ai_extracted 且含 rrule 列 → 跳过
        if "ai_extracted" in sql_text and "rrule" in sql_text:
            return
        log.info("calendar migrate: events.source CHECK 加 'ai_extracted' + 补 rrule 列")
        # 关外键防 cascade 阻塞
        conn.execute("PRAGMA foreign_keys = OFF;")
        # 拷到 events_new — 完整 schema(含 rrule 列,schema.sql 标准列顺序)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events_new (
                event_id       TEXT PRIMARY KEY,
                summary        TEXT NOT NULL,
                description    TEXT,
                dtstart_utc    TEXT NOT NULL,
                dtend_utc      TEXT NOT NULL,
                timezone       TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                venue_id       TEXT,
                source         TEXT NOT NULL DEFAULT 'user'
                    CHECK (source IN ('user', 'agent', 'ai_extracted')),
                chain_origin   TEXT,
                workplace_diff TEXT,
                dismissed      INTEGER NOT NULL DEFAULT 0
                    CHECK (dismissed IN (0, 1)),
                dismissed_at   TEXT,
                dismiss_reason TEXT,
                rrule          TEXT,
                created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)
        # 列拷一份(用 COALESCE 兜空列;老表无 rrule → 直接给 NULL)
        try:
            conn.execute("""
                INSERT INTO events_new
                  (event_id, summary, description, dtstart_utc, dtend_utc,
                   timezone, venue_id, source, chain_origin, workplace_diff,
                   dismissed, dismissed_at, dismiss_reason, rrule,
                   created_at, updated_at)
                SELECT event_id, summary, description, dtstart_utc, dtend_utc,
                       COALESCE(timezone,'Asia/Shanghai'), venue_id,
                       COALESCE(source,'user'), chain_origin, workplace_diff,
                       COALESCE(dismissed,0), dismissed_at, dismiss_reason, NULL,
                       COALESCE(created_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                       COALESCE(updated_at, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                FROM events;
            """)
        except Exception as ex:  # noqa: BLE001
            log.warning("calendar migrate copy fail: %s", ex)
            return
        conn.execute("DROP TABLE events;")
        conn.execute("ALTER TABLE events_new RENAME TO events;")
        # 重建索引(取 schema.sql 里 events 相关的索引名)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_events_window ON events (dtstart_utc, dtend_utc);",
            "CREATE INDEX IF NOT EXISTS idx_events_chain_origin ON events (chain_origin);",
            "CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);",
            "CREATE INDEX IF NOT EXISTS idx_events_dismissed ON events (dismissed);",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        conn.execute("PRAGMA foreign_keys = ON;")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        # WAL 在 init_schema 时已设, 运行时不要再 set, 否则每连接开关一次没意义
        return conn

    def _get_lock(self) -> asyncio.Lock:
        """懒创建 asyncio.Lock, 必须在 event loop 内调用."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # --------------------------------------------------------
    # 写路径 — 同步原语, 异步包装在外层
    # --------------------------------------------------------
    def add_event(self, event: EventRecord) -> str:
        """新增事件. event_id 已填则用之, 否则生成 UUID.

        Returns:
            event_id

        Raises:
            sqlite3.IntegrityError: 同 event_id 二次 add
        """
        if not event.event_id:
            event.event_id = str(uuid.uuid4())
        now = _utc_now_iso()
        if not event.created_at:
            event.created_at = now
        event.updated_at = now

        workplace_diff = event.workplace_diff
        if workplace_diff is not None and not isinstance(workplace_diff, str):
            workplace_diff = json.dumps(workplace_diff, ensure_ascii=False)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    event_id, summary, description,
                    dtstart_utc, dtend_utc, timezone,
                    venue_id,
                    source, chain_origin, workplace_diff,
                    dismissed, dismissed_at, dismiss_reason,
                    rrule,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id, event.summary, event.description,
                    event.dtstart_utc, event.dtend_utc, event.timezone,
                    event.venue_id,
                    event.source, event.chain_origin, workplace_diff,
                    1 if event.dismissed else 0, event.dismissed_at, event.dismiss_reason,
                    event.rrule,
                    event.created_at, event.updated_at,
                ),
            )
            self._record_ledger(conn, actor="agent" if event.source == "agent"
                                else ("agent" if event.source == "ai_extracted" else "user"),
                                action="add_event",
                                target_event_id=event.event_id,
                                rationale=f"add_event summary={event.summary!r}")
            conn.commit()
        return event.event_id

    def dismiss_event(self, event_id: str, reason: str = "") -> bool:
        """软删除事件 (dismissed=1). 留 ledger 审计.

        Returns:
            True if row updated, False if event not found.

        业务规则:
            - 已 dismissed 的事件再次 dismiss 是 no-op (返回 False, 不写 ledger)
        """
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE events SET dismissed = 1, dismissed_at = ?, "
                "dismiss_reason = ?, updated_at = ? "
                "WHERE event_id = ? AND dismissed = 0",
                (now, reason, now, event_id),
            )
            if cur.rowcount == 0:
                conn.commit()
                return False
            self._record_ledger(conn, actor="user", action="dismiss",
                                target_event_id=event_id,
                                rationale=reason or "(no reason given)")
            conn.commit()
            return True

    def insert_buffer(
        self,
        event_id: str,
        type: str,
        duration_min: int,
        *,
        inserted_before: str,
        inserted_after: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """插入 agent 缓冲 (交通 / 休息 / ...).

        Args:
            event_id: 缓冲依附的事件 (FK)
            type: 'travel' | 'rest' | 'preparation' | 'cooldown'
            duration_min: 缓冲时长(分钟)
            inserted_before: 事件开始前, 缓冲开始时刻 (UTC ISO8601)
            inserted_after: 缓冲结束时刻 = 事件开始时刻 (UTC ISO8601)
            metadata: agent 上下文 (Maps ETA / 通勤距离 / 天气), JSON

        Returns:
            buffer_id (新生成 UUID)

        Raises:
            sqlite3.IntegrityError: event_id 不存在 (FK 约束)
        """
        buffer_id = str(uuid.uuid4())
        now = _utc_now_iso()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO buffers (
                    buffer_id, event_id, type, duration_min,
                    inserted_after, inserted_before, metadata,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (buffer_id, event_id, type, duration_min,
                 inserted_after, inserted_before, meta_json,
                 now, now),
            )
            self._record_ledger(conn, actor="agent", action="insert_buffer",
                                target_event_id=event_id,
                                rationale=f"{type} {duration_min}min before {inserted_before}")
            conn.commit()
        return buffer_id

    def record_ledger(
        self,
        actor: str,
        action: str,
        target_event_id: Optional[str] = None,
        target_buffer_id: Optional[str] = None,
        policy_snapshot: Optional[Dict[str, Any]] = None,
        rationale: str = "",
    ) -> int:
        """手动写一条 ledger (供 buffer 让位 / chain 识别等场景).

        Returns:
            ledger_id (新生成)
        """
        with self._connect() as conn:
            self._record_ledger(conn, actor, action, target_event_id,
                                target_buffer_id, policy_snapshot, rationale)
            conn.commit()
            cur = conn.execute("SELECT last_insert_rowid() AS id")
            return int(cur.fetchone()["id"])

    def _record_ledger(
        self,
        conn: sqlite3.Connection,
        actor: str,
        action: str,
        target_event_id: Optional[str] = None,
        target_buffer_id: Optional[str] = None,
        policy_snapshot: Optional[Dict[str, Any]] = None,
        rationale: str = "",
    ) -> None:
        policy_json = json.dumps(policy_snapshot, ensure_ascii=False) if policy_snapshot else None
        conn.execute(
            """
            INSERT INTO ledger (
                occurred_at, actor, action,
                target_event_id, target_buffer_id,
                policy_snapshot, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_utc_now_iso(), actor, action,
             target_event_id, target_buffer_id,
             policy_json, rationale),
        )

    # --------------------------------------------------------
    # 读路径
    # --------------------------------------------------------
    def list_events(
        self,
        window_start: str,
        window_end: str,
        *,
        include_dismissed: bool = False,
        sources: Optional[Sequence[str]] = None,
    ) -> List[EventRecord]:
        """列时间窗内事件.

        Args:
            window_start: UTC ISO8601
            window_end:   UTC ISO8601
            include_dismissed: 默认 False (Phase 1 隐藏 dismissed)
            sources: 过滤 source (None = 所有)

        Returns:
            按 dtstart_utc 升序
        """
        sql = ["SELECT * FROM events WHERE 1=1"]
        params: List[Any] = []
        if not include_dismissed:
            sql.append("AND dismissed = 0")
        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql.append(f"AND source IN ({placeholders})")
            params.extend(sources)
        # 时间窗: 事件与窗有交集 (event.dtend >= window_start AND event.dtstart <= window_end)
        sql.append("AND dtend_utc >= ? AND dtstart_utc <= ?")
        params.extend([window_start, window_end])
        sql.append("ORDER BY dtstart_utc ASC")
        sql_str = "\n".join(sql)
        with self._connect() as conn:
            rows = conn.execute(sql_str, params).fetchall()
        return [EventRecord.from_row(r) for r in rows]

    def get_event(self, event_id: str) -> Optional[EventRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return EventRecord.from_row(row) if row else None

    def list_buffers(self, event_id: str) -> List[BufferRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM buffers WHERE event_id = ? ORDER BY inserted_before ASC",
                (event_id,),
            ).fetchall()
        return [BufferRecord.from_row(r) for r in rows]

    def list_ledger(
        self,
        *,
        target_event_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[LedgerRecord]:
        """Phase 1 ledger 隐藏, 此 API 仅给内部调试 / 后续 task 用."""
        sql = ["SELECT * FROM ledger WHERE 1=1"]
        params: List[Any] = []
        if target_event_id:
            sql.append("AND target_event_id = ?")
            params.append(target_event_id)
        if action:
            sql.append("AND action = ?")
            params.append(action)
        sql.append("ORDER BY ledger_id DESC LIMIT ?")
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute("\n".join(sql), params).fetchall()
        return [LedgerRecord.from_row(r) for r in rows]

    # --------------------------------------------------------
    # ICS 导出 — RFC 5545 + X-PRISIR-* 扩展
    # --------------------------------------------------------
    def export_ics(
        self,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        *,
        include_dismissed: bool = False,
    ) -> bytes:
        """导出 ICS.

        设计 (§3.5 设计文档):
            - 默认不含 dismissed 事件 (导出"用户视角日历")
            - 含 dismissed 的事件打 X-PRISIR-DISMISSED:true 标记 (订阅方看到仍可识别)
            - 含 X-PRISIR-CHAIN-FROM (chain 起点识别对账)
            - 含 X-PRISIR-WORKPLACE (agent 改写办公地点)
            - buffer 导出为独立 VEVENT, 打 X-PRISIR-BUFFER-TYPE
            - **不导出 ledger**

        Returns:
            ICS 字节流 (UTF-8)
        """
        cal = icalendar.Calendar()
        cal.add("prodid", "-//prisIr//prisIr_calendar//EN")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("x-wr-timezone", "Asia/Shanghai")
        cal.add("x-prisir-exported-at", _utc_now_iso())

        events = self._collect_events_for_export(window_start, window_end, include_dismissed)
        for ev in events:
            cal.add_component(self._event_to_vevent(ev))

        buffers = self._collect_buffers_for_export(events)
        for buf_rec, parent_ev in buffers:
            cal.add_component(self._buffer_to_vevent(buf_rec, parent_ev))

        return cal.to_ical()

    def _collect_events_for_export(
        self,
        window_start: Optional[str],
        window_end: Optional[str],
        include_dismissed: bool,
    ) -> List[EventRecord]:
        # list_events 已按时间窗交集过滤, 这里再补一个可选的 None=全部
        if window_start and window_end:
            return self.list_events(window_start, window_end,
                                    include_dismissed=include_dismissed)
        # 全量导出: 直接查
        sql = ["SELECT * FROM events WHERE 1=1"]
        params: List[Any] = []
        if not include_dismissed:
            sql.append("AND dismissed = 0")
        sql.append("ORDER BY dtstart_utc ASC")
        with self._connect() as conn:
            rows = conn.execute("\n".join(sql), params).fetchall()
        return [EventRecord.from_row(r) for r in rows]

    def _collect_buffers_for_export(
        self, events: List[EventRecord]
    ) -> List[Tuple[BufferRecord, EventRecord]]:
        if not events:
            return []
        ids = tuple(e.event_id for e in events)
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM buffers WHERE event_id IN ({placeholders}) "
                f"ORDER BY inserted_before ASC",
                ids,
            ).fetchall()
        ev_by_id = {e.event_id: e for e in events}
        out: List[Tuple[BufferRecord, EventRecord]] = []
        for r in rows:
            buf = BufferRecord.from_row(r)
            parent = ev_by_id.get(buf.event_id)
            if parent:
                out.append((buf, parent))
        return out

    def _event_to_vevent(self, ev: EventRecord) -> icalendar.Event:
        vev = icalendar.Event()
        vev.add("uid", ev.event_id)
        vev.add("dtstamp", _parse_iso(ev.updated_at or ev.created_at))
        vev.add("dtstart", _parse_iso(ev.dtstart_utc))
        vev.add("dtend", _parse_iso(ev.dtend_utc))
        vev.add("summary", ev.summary)
        if ev.description:
            vev.add("description", ev.description)
        # X-PRISIR-* 扩展
        vev.add("X-PRISIR-SOURCE", vText(ev.source))
        if ev.dismissed:
            vev.add("X-PRISIR-DISMISSED", vBoolean(True))
            if ev.dismissed_at:
                vev.add("X-PRISIR-DISMISSED-AT", _parse_iso(ev.dismissed_at))
            if ev.dismiss_reason:
                vev.add("X-PRISIR-DISMISSED-REASON", vText(ev.dismiss_reason))
        if ev.chain_origin:
            vev.add("X-PRISIR-CHAIN-FROM", vText(ev.chain_origin))
        if ev.workplace_diff is not None:
            wd_str = (ev.workplace_diff
                      if isinstance(ev.workplace_diff, str)
                      else json.dumps(ev.workplace_diff, ensure_ascii=False))
            vev.add("X-PRISIR-WORKPLACE", vText(wd_str))
        if ev.rrule:
            vev.add("rrule", ev.rrule)
        return vev

    def _buffer_to_vevent(self, buf: BufferRecord, parent: EventRecord) -> icalendar.Event:
        vev = icalendar.Event()
        vev.add("uid", f"buffer-{buf.buffer_id}")
        vev.add("dtstamp", _parse_iso(buf.updated_at or buf.created_at))
        vev.add("dtstart", _parse_iso(buf.inserted_before))
        vev.add("dtend", _parse_iso(buf.inserted_after))
        # 用 "→ 主事件 summary" 让订阅方一眼看出缓冲归谁
        vev.add("summary", f"→ {parent.summary}")
        vev.add("description",
                f"Auto-inserted {buf.type} buffer ({buf.duration_min} min) before: {parent.summary}")
        # X-PRISIR-BUFFER-TYPE
        vev.add("X-PRISIR-SOURCE", vText("agent"))
        vev.add("X-PRISIR-BUFFER-TYPE", vText(buf.type))
        vev.add("X-PRISIR-BUFFER-FOR", vText(parent.event_id))
        if buf.metadata:
            vev.add("X-PRISIR-BUFFER-META",
                    vText(buf.metadata if isinstance(buf.metadata, str)
                          else json.dumps(buf.metadata, ensure_ascii=False)))
        return vev

    # --------------------------------------------------------
    # 异步包装 (Phase 1 业务方主要走 async, 但 ICS 导出可同步)
    # --------------------------------------------------------
    async def add_event_async(self, event: EventRecord) -> str:
        async with self._get_lock():
            return await asyncio.to_thread(self.add_event, event)

    async def dismiss_event_async(self, event_id: str, reason: str = "") -> bool:
        async with self._get_lock():
            return await asyncio.to_thread(self.dismiss_event, event_id, reason)

    async def insert_buffer_async(self, *args, **kwargs) -> str:
        async with self._get_lock():
            return await asyncio.to_thread(self.insert_buffer, *args, **kwargs)


# ============================================================
# helpers
# ============================================================
def _utc_now_iso() -> str:
    """当前 UTC ISO8601 字符串, 秒精度."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _parse_iso(s: str) -> datetime:
    """解析 ISO8601 → tz-aware datetime (假设 UTC)."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _split_statements(sql: str) -> Iterable[str]:
    """SQL 语句分割. 规则:
    - 跳过空行与整行 -- 注释
    - 行内 -- 注释要先剥除 (否则注释里的 ; 会误切)
    - 单引号字符串字面量里的 ; 不切
    - 按 ';' 切分顶层语句

    Phase 1 schema.sql 是写死的 PRAGMA / CREATE TABLE / CREATE INDEX,
    schema 里所有 ; 只在顶层语句末尾出现, 不在字符串里.
    """
    out: List[str] = []
    buf: List[str] = []
    in_string = False

    def _strip_inline_comment(line: str) -> str:
        """去掉行内 -- 注释 (不在字符串字面量内的)."""
        nonlocal in_string
        out_chars: List[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_string = not in_string
                out_chars.append(ch)
                i += 1
                continue
            if not in_string and ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                # 行内注释起点, 截断
                break
            out_chars.append(ch)
            i += 1
        return "".join(out_chars)

    for raw_line in sql.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        line_no_comment = _strip_inline_comment(raw_line)
        if not line_no_comment.strip():
            continue
        for ch in line_no_comment:
            buf.append(ch)
            if ch == ";" and not in_string:
                stmt = "".join(buf).strip()
                if stmt.endswith(";"):
                    stmt = stmt[:-1].rstrip()
                if stmt:
                    out.append(stmt)
                buf = []
                break
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out