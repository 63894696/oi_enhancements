# PrisirAI 日历模块技术选型设计文档

> 目标:为 `prisIr_calendar/` 模块的底层依赖、存储模型、扩展字段、与通用 CalDAV 的差异化定位提供决策依据。
> 核心原则:**唯一源、不外同步、agent 语义优先、ICS 仅作导出格式**。

---

## 1. 调研对象

### 1.1 `ics.py`(`ics-py/ics.py`)
- **定位**:Pythonic 的 iCalendar 解析/生成库,带 asyncio 支持。
- **License**:Apache-2.0,最新 0.7.2。
- **优点**:面向对象 API 直观(`Event` 对象可设字段);asyncio 友好;活跃维护。
- **缺点**:相比 `icalendar` 抽象更厚,对底层 RFC 5545 字段访问不够直接;对于 agent 自定义 `X-` 属性需要绕 `Event.extra` 容器。

### 1.2 `icalendar`(`icalendar/icalendar`)
- **定位**:RFC 5545 兼容的解析/生成库,事实标准。
- **优点**:成熟稳定;与 `recurring-ical-events` 配套使用;字段访问贴近 RFC(VEVENT/VTODO/CATEGORIES 等);社区广泛。
- **缺点**:同步 API 为主,无原生 async;学习曲线比 `ics.py` 稍陡。

> **结论**:我们倾向 `icalendar` 而非 `ics.py`,因为我们需要直接控制 RRULE / EXDATE / X- 属性,贴近 RFC 字段语义比"pythonic 包装"更重要。

### 1.3 `recurring-ical-events`(`niccokunzmann/python-recurring-ical-events`)
- **定位**:基于 RFC 5545 的循环事件展开库。
- **优点**:支持 `at()` / `between()` 时间窗查询;RECURRENCE-ID + THISANDFUTURE 编辑;X-WR-TIMEZONE 兼容;LGPL-3.0。
- **缺点**:底层依赖 `python-dateutil.rrule`,意味着会拖入 `dateutil` 而非纯 stdlib `zoneinfo`。
- **关键能力**:✅ RRULE / RDATE / EXDATE / DURATION / DST / 编辑后的循环 —— **够用**。

### 1.4 `Radicale`(Python CalDAV server)
- **定位**:轻量自托管 CalDAV/CardDAV 服务器。
- **优点**:开箱即用;支持多用户/权限。
- **缺点**:**本质是"通用 CalDAV 服务"** —— 暴露 WebDAV 接口、接受任意客户端写入、无法阻止外部客户端把数据同步走。
- **判定**:**反面参考,不用**。我们不需要"对外接受同步",只需要"对内 agent 操作"。

### 1.5 `Baïkal`(PHP CalDAV)
- **定位**:PHP 的轻量 CalDAV。
- **优点**:安装简单,适合 NAS 类场景。
- **缺点**:引入 PHP 运行时;同样是"通用 CalDAV"语义,与我们的"唯一源"定位冲突。
- **判定**:**不用**。

### 1.6 `python-caldav`(`python-caldav/caldav`)
- **定位**:CalDAV 客户端库,支持 RFC 4791/6638/6047。
- **优点**:同时支持同步(`niquests`)和异步(`httpx`)客户端;日程邀请接受等便利方法。
- **缺点**:是 **client** 不是 server,假设存在 CalDAV endpoint;我们需要的是"权威源",不需要去同步别人。
- **判定**:**不用**,仅作为"如果未来要订阅外部节假日源时再考虑"的备选。

### 1.7 "Agent-friendly 日历 schema" 思路(文献调研)
- **RFC 5545 §3.8.8**:`X-` 前缀为厂商扩展保留,解析器必须**优雅忽略未知 X- 属性**。
- **CalConnect / IETF calsify**:通过 Internet-Draft → RFC 的路径升级 X- 为标准属性(如 `VLOCATION` 走 RFC 9073)。
- **建议**:用 `X-PRISIR-` 命名空间(如 `X-PRISIR-DISMISSED`、`X-PRISIR-CHAIN-FROM`、`X-PRISIR-WORKPLACE-DIFF`),既保留导出兼容性,又避免与他人冲突。

---

## 2. 借鉴哪些组件(决策表)

| 决策 | 选择 | 理由 |
|---|---|---|
| ✅ ICS 解析/生成 | `icalendar`(PyPI) | RFC 字段直接访问、X- 属性保留;`recurring-ical-events` 配套 |
| ✅ 循环事件展开 | `recurring-ical-events`(PyPI) | `between(start, end)` API 干净;覆盖 RFC 5545 §3.3.10 |
| ✅ HTTP 服务框架 | `FastAPI` | agent 调用走 REST,Prisir 主进程内嵌即可 |
| ✅ 数据库驱动 | stdlib `sqlite3`(无需 SQLAlchemy) | 模型简单、避免 ORM 抽象污染 |
| ❌ CalDAV server (Radicale / Baïkal) | 不用 | 违反"唯一源、不接同步"定位,引入对外接口即破坏产品边界 |
| ❌ CalDAV client (python-caldav) | 不用 | 我们是权威源,不需要同步第三方 |
| ❌ 任何外部 OAuth / 同步层 | 不用 | 用户"基本只看",无设置入口,无外部账号绑定需求 |
| ➕ 自研:`insert_buffer(after, before, type)` | 自研 | 通用库只懂"存事件",不懂"塞一段交通缓冲"语义 |
| ➕ 自研:`dismiss(event_id)` | 自研 | 通用库无"软删除 + 留 ledger"概念 |
| ➕ 自研:`scan_chain(origin=None)` | 自研 | "链条起点识别"是 Prisir 专属智能,无可借鉴实现 |
| ➕ 自研:`ledger` 表 + policy 引擎 | 自研 | 用于审计 dismiss / buffer 的决策历史,可回放 |

---

## 3. 关键技术决策

### 3.1 重复事件:RRULE 处理够用吗?
- **够用**。`recurring-ical-events` 覆盖 RFC 5545 §3.3.10 的 RRULE + RDATE + EXDATE + RECURRENCE-ID(THISANDFUTURE)。
- 用户主要"只看",**不需要 RFC 7529/7953**(vCard/RFC 7986 advanced recurrence)的复杂特性。
- 我们的 `insert_buffer` 会生成**单次 VEVENT**(不带 RRULE)塞入,所以 RRULE 处理路径只在**读取既有循环事件**时触发,频次低。

### 3.2 时区:`zoneinfo` 还是 `dateutil.tz`?
- **选 `zoneinfo`**(Python 3.9+ stdlib)。
- **理由**:
  - 国内用户默认 `Asia/Shanghai`(UTC+8,无 DST),稳定;
  - stdlib 零依赖;
  - `recurring-ical-events` 内部用 `dateutil`,但**对外暴露的是普通 datetime**,我们应用层只需 `zoneinfo.ZoneInfo("Asia/Shanghai")` 包装;
  - Windows 用户必须 `pip install tzdata`,但这是轻量补丁,可接受。
- **落地**:`dtstart` / `dtend` 字段在 SQLite 中**存 UTC**,展示时按 `Asia/Shanghai` 渲染;ICS 导出时 `DTSTART;TZID=Asia/Shanghai`。

### 3.3 存储后端:SQLite 够吗?
- **够,且优先 SQLite**。
- 单用户场景下,事件量级在万级以下;`recurring-ical-events` 按需展开,不会爆。
- **WAL 模式**:`PRAGMA journal_mode = WAL;` —— 支持多读单写,前端"只看"读路径完全不阻塞。
- **不升级 Postgres 的理由**:增加运维负担、Docker 镜像膨胀、单用户无并发收益。
- **未来如果多用户**:再迁移到 Postgres + 时区表;接口层抽象 `CalendarStore` 协议,切换成本可控。

### 3.4 写入并发:agent + 用户并发写怎么办?
- **SQLite 单写者** —— WAL 模式下同时只能有一个 writer,第二个 writer 直接 `SQLITE_BUSY`。
- **我们的对策**:
  1. **应用层串行化**:agent 写和用户写都进同一个 `asyncio.Lock`(`prisIr_calendar.lock`),让排队可见、可观测;
  2. **短事务原则**:每次写入 < 10ms,dismiss / buffer 路径都是单条 INSERT;
  3. **`busy_timeout` 设为 5000ms**:极低概率撞锁,撞了就等;
  4. **不用 `BEGIN IMMEDIATE`**:默认 deferred 事务够用,只有多语句聚合写才显式提升。
- **不需要 Postgres 事务模型**:单 agent + 单用户场景,SQLite + 应用层锁已足够。

### 3.5 扩展字段:X- 前缀塞 ICS 还是只在 SQLite 存?
- **采用"双写"策略,默认走 SQLite,X- 是导出回退**。

| 字段 | 主存位置 | 是否导出到 ICS | 理由 |
|---|---|---|---|
| `dismissed` (boolean) | SQLite `events.dismissed` | ✅ `X-PRISIR-DISMISSED:true` | 用户删除语义对账时重要,导出要保留 |
| `dismissed_at` (timestamp) | SQLite | ✅ `X-PRISIR-DISMISSED-AT` | 审计需要 |
| `chained_from` (event_id) | SQLite `events.chain_origin` | ✅ `X-PRISIR-CHAIN-FROM` | chain 起点识别对账 |
| `workplace_diff` (location override) | SQLite `events.workplace` | ✅ `X-PRISIR-WORKPLACE` | agent 自动重写的办公地点 |
| `buffer_type` (travel/rest...) | SQLite `buffers.type` | ✅ `X-PRISIR-BUFFER-TYPE` | 缓冲语义 |
| `ledger` 决策日志 | SQLite `ledger` 表 | ❌ **不导出** | 内部审计,对外无意义;导出反而泄露 agent 决策细节 |

- **为什么不只存 SQLite**?—— ICS 是备份/订阅通道,外部订阅方需要看到 dismissed 标记,否则"用户删了的事件"在外部会复活(订阅重新拉取),产生不一致。
- **为什么 ledger 不导出**?—— ledger 是审计 trail,导出后会让 ICS 文件膨胀,且对最终用户无意义。

---

## 4. 与通用 CalDAV 的根本差异

> 这是产品价值所在:**Prisir 日历不是日历,是 agent 的日程大脑**。

### 4.1 接口对比

```
通用 CalDAV (Radicale / Baikal):
    store(event)              # 存就完事
    list_events(start, end)   # 列出来
    delete(uid)               # 真删
    update(uid, event)        # 覆盖

Prisir 日历:
    insert_buffer(after, before, type=travel)  # 插交通/休息缓冲
    dismiss(event_id)                          # 软删除 + 留 ledger
    scan_chain(origin=None)                    # 智能识别 chain 起点
    ledger.record(buffer_id, policy)           # 决策可回放
    get_view(user_id, scope='today')           # 渲染视角(过滤 dismissed + 合并 buffer)
```

### 4.2 能力对比表

| 能力 | 通用 CalDAV 给什么 | 我们必须自己写 | 为什么值得自研 |
|---|---|---|---|
| 存事件 | `store(event)` —— VEVENT 序列化即可 | `add(event)` 薄包装 | 通用,无差异化 |
| 列事件(时间窗) | `list_events(start, end)` | 直接用,只过滤 `dismissed=false` | 通用 |
| 删事件 | `delete(uid)` —— 真删 | `dismiss(uid)` —— **软删除 + ledger** | Prisir 的"删"是"agent 知道了",不是"消失";留 ledger 才能回放 agent 决策 |
| 重复事件展开 | `recurring-ical-events` 库 | 直接用 | 通用 |
| 插入缓冲事件 | ❌ **无概念** | `insert_buffer(after, before, type)` —— agent 根据上下文(议程冲突/天气/通勤距离)插入 | 这是 Prisir 的"大脑"特征 |
| Chain 起点识别 | ❌ **无概念** | `scan_chain(origin)` —— 在事件图中识别"用户一天从哪里开始" | 给 agent 决策"提前多久提醒" |
| Ledger 决策审计 | ❌ **无概念** | `ledger` 表 + append-only 写入 | 让 agent 决策可解释、可回滚 |
| 视角渲染 | `list_events` 之后客户端过滤 | `get_view(user, scope)` 服务端就过滤好 dismissed + 合并 buffer | 减少前端复杂度,后端是真理之源 |
| ICS 导出 | CalDAV 协议本身就支持 | `export_ics(scope, include_dismissed=false)` 自实现 | 我们要的是"备份/订阅",不是 CalDAV 协议;且能精确控制 X- 字段是否带上 |
| 多用户隔离 | CalDAV principal/ACL | 不需要 —— 单用户 | CalDAV 这套是给企业协作的,与我们"个人大脑"场景无关 |

### 4.3 为什么"自研"是合理的产品决策
1. **概念错位**:CalDAV 的语义是"事件 = 真理";Prisir 的语义是"事件 + buffer + chain + ledger = 真理"。硬塞 CalDAV 就要伪造 VEVENT 来模拟 buffer,既丑又脆。
2. **写权限泛滥**:CalDAV server 接受任何合规客户端的写入,我们无法阻止用户在手机端恢复 dismissed 事件。
3. **运维成本倒挂**:CalDAV 需要 WebDAV 兼容存储、ACL、Discovery,这些都是为多用户协作付的税;我们只有一个人。
4. **agent 接口是产品差异**:Radicale / Baikal 的价值在"我能给多个客户端同步",Prisir 的价值在"agent 帮我安排"。两件事是不同物种。

---

## 5. 总结决策(一句话回扣三节)

- **库**:用 `icalendar` + `recurring-ical-events` 做 ICS 双向,`sqlite3`(WAL)+ stdlib `zoneinfo` 做存储。
- **不要**:CalDAV server/client 全部不上 —— 与"唯一源、不接同步"的产品定位根本冲突。
- **自研**:`insert_buffer / dismiss / scan_chain / ledger` 四个语义操作 + `X-PRISIR-` 命名空间扩展,这是产品差异点,通用库不可替代。

---

## 6. 待澄清问题(不超过 5 个)

1. **多设备读取入口**:虽然不接 CalDAV 同步,但用户是否需要在自己手机上**只读**查看?如果有,导出 ICS 走 WebDAV 静态文件订阅(坚果云/OneDrive 公开链接)是否够?还是需要 push 通知?
2. **事件生命周期终点**:用户用 `dismiss` 软删后,多久之后可以从主表移除、转存冷存储?是否会设置"30 天后归档"的策略?
3. **buffer 冲突解决**:agent 插入的 buffer 与用户手动添加的事件冲突时(用户也加了 9:00 咖啡),agent 应该让位还是合并提示?需要人工标注吗?
4. **节假日源**:`Asia/Shanghai` 节假日(春节/国庆调休)目前没有 RFC 5545 标准源。是否每年手工注入?还是订阅第三方?
5. **ledger 的对外可见性**:用户在前端是否能查看"为什么那天 agent 给我塞了 30 分钟交通缓冲"?如果可见,需要什么粒度?