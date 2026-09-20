-- prisIr_calendar schema.sql — Phase 1 DDL
-- 4 张表: events / buffers / ledger / venues
--
-- 设计原则 (见 docs/prisIr-calendar-design.md):
--   - 所有时间字段存 UTC ISO8601 文本(便于 SQLite 排序 / 比较)
--   - UUID 主键 → TEXT COLLATE NOCASE 不合适, 用 PRIMARY KEY 直接保证
--   - append-only: ledger 表不允许 UPDATE / DELETE (应用层断言)
--   - 主索引: (dtstart_utc, dtend_utc) 复合, 时间窗查询是热点
--
-- 升级说明:
--   - Phase 1 不接节假日源(留空)
--   - Phase 1 不实现 dismiss 冷存储(主表保留所有 dismissed)
--   - Phase 1 buffer 与用户手动事件冲突时: buffer 让位 + 写 ledger
--   - Phase 1 ledger 对外不可见(导出 ICS 不带)

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;       -- 多读单写,前端"只看"读路径不阻塞
PRAGMA busy_timeout = 5000;      -- 撞锁等 5s (ms)

-- ============================================================
-- events 表: 主事件表(用户告知 + agent 自动)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,            -- UUID v4
    summary         TEXT NOT NULL,               -- 显示标题
    description     TEXT,                        -- 可选描述(用户原话/agent 决策说明)

    -- 时间 (UTC ISO8601 字符串, e.g. "2026-09-19T15:00:00+00:00")
    dtstart_utc     TEXT NOT NULL,
    dtend_utc       TEXT NOT NULL,
    timezone        TEXT NOT NULL DEFAULT 'Asia/Shanghai',  -- 显示时区

    -- 地点 (venues 表外键,但允许 NULL 让"无地点"事件能存)
    venue_id        TEXT,                        -- FK -> venues.venue_id

    -- Agent 专用字段
    source          TEXT NOT NULL DEFAULT 'user' -- 'user' | 'agent' | 'ai_extracted'(P2.5+8)
                        CHECK (source IN ('user', 'agent', 'ai_extracted')),
    chain_origin    TEXT,                        -- FK -> events.event_id, "这条是 agent 从哪条派生的"
    workplace_diff  TEXT,                        -- JSON, agent 改写的办公地点 (location override)
    dismissed       INTEGER NOT NULL DEFAULT 0   -- boolean 0/1, 软删除标记
                        CHECK (dismissed IN (0, 1)),
    dismissed_at    TEXT,                        -- UTC ISO8601, 仅 dismissed=1 时有值
    dismiss_reason  TEXT,                        -- 用户甩手时的语义(让 ledger 之外也能查)

    -- 循环事件 (RFC 5545 RRULE)
    rrule           TEXT,                        -- 可选, e.g. "FREQ=DAILY;COUNT=3"

    -- 元数据
    created_at      TEXT NOT NULL,               -- UTC ISO8601
    updated_at      TEXT NOT NULL,               -- UTC ISO8601

    FOREIGN KEY (chain_origin) REFERENCES events(event_id) ON DELETE SET NULL
);

-- 时间窗查询热点
CREATE INDEX IF NOT EXISTS idx_events_window
    ON events (dtstart_utc, dtend_utc);

-- 链式起点识别 (chain_origin 倒排)
CREATE INDEX IF NOT EXISTS idx_events_chain_origin
    ON events (chain_origin);

-- 按 source 过滤 (agent 自动事件查询)
CREATE INDEX IF NOT EXISTS idx_events_source
    ON events (source);

-- dismissed 过滤 (默认 list_events 不返回)
CREATE INDEX IF NOT EXISTS idx_events_dismissed
    ON events (dismissed);


-- ============================================================
-- buffers 表: agent 插入的缓冲(交通/休息/...)
-- ============================================================
-- Phase 1 决策: buffer 与用户手动事件冲突时, agent 让位 + 写 ledger
-- buffer 是 event_id 引用而非新事件, 这样 dismiss 一条 buffer = dismiss 对应事件
CREATE TABLE IF NOT EXISTS buffers (
    buffer_id       TEXT PRIMARY KEY,            -- UUID v4
    event_id        TEXT NOT NULL,              -- FK -> events.event_id, 缓冲依附的事件
    type            TEXT NOT NULL                -- 缓冲类型
                        CHECK (type IN ('travel', 'rest', 'preparation', 'cooldown')),
    duration_min    INTEGER NOT NULL,           -- 缓冲时长 (分钟)
    inserted_after  TEXT NOT NULL,               -- UTC ISO8601, 缓冲在事件前的结束时刻
    inserted_before TEXT NOT NULL,               -- UTC ISO8601, 缓冲在事件前的开始时刻(after 之前)

    metadata        TEXT,                        -- JSON, agent 上下文(高德/Google ETA / 通勤距离 / 天气)

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_buffers_event
    ON buffers (event_id);


-- ============================================================
-- ledger 表: append-only 决策审计
-- ============================================================
-- agent 每次"做出决策"(dismiss / buffer 冲突让位 / chain 识别...) 都写一条
-- 不可 UPDATE / DELETE (应用层 assert, 不靠 DB trigger 因为 SQLite 触发器复杂)
-- Phase 1 对外不可见 (导出 ICS 不带 ledger)
CREATE TABLE IF NOT EXISTS ledger (
    ledger_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     TEXT NOT NULL,               -- UTC ISO8601
    actor           TEXT NOT NULL                 -- 'agent' | 'user'
                        CHECK (actor IN ('agent', 'user')),
    action          TEXT NOT NULL                 -- 'dismiss' | 'insert_buffer' | 'buffer_yield' | 'chain_origin_set' | 'workplace_diff' | 'add_event'
                        CHECK (action IN ('dismiss', 'insert_buffer', 'buffer_yield',
                                          'chain_origin_set', 'workplace_diff', 'add_event')),
    target_event_id TEXT,                        -- 操作的事件 (FK -> events.event_id, 可空:某些全局动作)
    target_buffer_id TEXT,                       -- 操作的 buffer (FK -> buffers.buffer_id, 可空)
    policy_snapshot TEXT,                        -- JSON, 当时生效的 policy 摘要(让回放可重现)
    rationale       TEXT,                        -- 自然语言理由 ("workplace→国贸 SK 老司机, 不再加前置")

    FOREIGN KEY (target_event_id) REFERENCES events(event_id) ON DELETE SET NULL,
    FOREIGN KEY (target_buffer_id) REFERENCES buffers(buffer_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_target_event
    ON ledger (target_event_id);
CREATE INDEX IF NOT EXISTS idx_ledger_action
    ON ledger (action);


-- ============================================================
-- venues 表: 已识别地址缓存 (避免重复识别)
-- ============================================================
-- Maps ETA / POI 解析结果的去重表, agent 提到 "国贸 SK" 时先查这里
CREATE TABLE IF NOT EXISTS venues (
    venue_id        TEXT PRIMARY KEY,            -- UUID v4
    raw_name        TEXT NOT NULL,               -- 用户/agent 口语化名称 "国贸 SK"
    canonical_name  TEXT,                        -- 标准化名 "国贸 SK大厦"
    address         TEXT,                        -- 高德/Google 反查地址
    latitude        REAL,
    longitude       REAL,
    vendor          TEXT,                        -- 'amap' | 'google' | 'manual'
    vendor_place_id TEXT,                        -- vendor 侧 ID (高德 poi_id / Google place_id)

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_venues_raw_name
    ON venues (raw_name);