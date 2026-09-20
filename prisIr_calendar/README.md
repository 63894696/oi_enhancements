# prisIr_calendar — Phase 1

> PrisirAI 日历模块 (Phase 1 数据模型 + storage 层).

## 文件清单

| 文件 | 行数 | 作用 |
|---|---|---|
| `schema.sql` | 146 | 4 表 DDL: events / buffers / ledger / venues |
| `store.py` | 642 | CalendarStore 封装 sqlite3+WAL+asyncio.Lock+ICS 导出 |
| `tests/test_store.py` | 526 | 27 个单元测试 (含 E2E 场景串) |

## 依赖

- `icalendar>=7.0`        — ICS 序列化 (RFC 5545)
- `recurring-ical-events>=3.0` — 循环事件展开 (Phase 1 留 API, Phase 2 启用)
- stdlib: `sqlite3`, `zoneinfo`, `asyncio`, `uuid`, `dataclasses`, `json`

(由项目根 `requirements.txt` 统一管, Phase 1 不强装, 不创建依赖文件.)

## 拍板决策 (来自 docs/prisIr-calendar-design.md)

- 节假日源: Phase 1 不接, venues 表留接口
- dismiss 冷存储: Phase 1 不实现, 主表永久保留
- buffer 与用户手动事件冲突: Phase 1 让位 + 写 ledger
- ledger 可见性: Phase 1 隐藏 (导出 ICS 不带 ledger)

## Phase 2 集成

- 新增 `http_api.py`: FastAPI 暴露, 沿用 `companion/music/port_registry.py` 模式
- Tauri 侧加 `prisIragent-tauri/src-tauri/src/calendar.rs` (仿 `music.rs` ~80 行)
- `installer/prisirai.nsi` 不需要改 (Phase 1 跟着 `prisiragent_web.py` 进 `PrisirAI.exe`)