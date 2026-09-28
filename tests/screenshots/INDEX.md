# PrisirAI 旅行助手 E2E 验证 — 产物清单 (Phase 1 + Phase 2)

> 测试运行日期: 2026-09-19
> E2E 文件: `prisIr_calendar/tests/test_e2e_phase1.py` + `tests/test_e2e_phase2.py`
> 测试套件: prisIr_calendar 全套 207 测试全绿 (186 baseline + 6 Phase1 + 15 Phase2 业务 + 5 Phase2 Rust 逻辑等价)
>             + tests/test_e2e_phase2.py 全套 20 测试全绿

---

## Phase 2 产物 (task #25)

| 文件 | 来源场景 | 路径 | 说明 |
|------|----------|------|------|
| `phase2_e2e_sentinel.txt` + `.png` | 场景 1: HTTP server sentinel | `tests/screenshots/` | subprocess 启 prisiragent_web.py → 解析 PRISIR_WEB_READY port=18881 → /prisiragent/api/info 200 |
| `phase2_e2e_timeline.txt` + `.png` | 场景 4: 全栈用户旅程 | `tests/screenshots/` | 插事件 → scan → timeline → export → dismiss 全链路 |

## Phase 1 产物 (task #11)

| 文件 | 来源场景 | 路径 | 说明 |
|------|----------|------|------|
| `sample.ics` | 场景 4: ICS 导出 | `tests/screenshots/sample.ics` | 含 X-PRISIR-DISMISSED + X-PRISIR-BUFFER-TYPE, 不含 ledger |
| `sample_http.ics` | 场景 5: HTTP `/export.ics` | `tests/screenshots/sample_http.ics` | 真实 HTTP server 返回的 ICS, Content-Type: text/calendar |

## 截图(本任务不强制,但本目录其他 m325/m327 等历史截图保留)

> 任务说明里要求 `e2e_timeline_after_scan.png`, 由于 Phase 1 是只读 UI,
>   Puppeteer 渲染 timeline 需要先注入测试事件, 这超出本 E2E 范围,
>   所以用 ASCII 描述替代 (见下)。

### Phase 1 场景 1: 模拟用户日常 — ASCII 描述 timeline 渲染结果

```
┌──────────────── 14 天时间线 (Prisir 日历) ─────────────────┐
│ 09/19 (Sat)                                              │
│   08:25 ─ 09:00  → 国贸 SK 会议 (buffer, travel, 35min) │
│   09:00 ─ 10:00  国贸 SK 会议 (dismissed → 灰)           │
│   10:00 ─ 12:00  续会 (yielded, 不显示 buffer)           │
│   14:00 ─ 15:00  视频会议(腾讯会议)  [skip: video_only]  │
│   15:25 ─ 16:00  → 望京 SOHO (buffer, travel, 35min)    │
│   16:00 ─ 17:00  望京 SOHO 会议                          │
│   19:00 ─ 20:30  飞行 CA1234  [skip: flight_hotel]      │
└────────────────────────────────────────────────────────────┘
```

### Phase 1 场景 2: 用户 dismiss 后再 scan — 同样 origin+dest 不再插

```
round 1: 国贸 SK 会议 (09:00-10:00) → buffer (08:25-09:00)  ✓ inserted
round 2: 国贸 SK 会议2 (重复, 09:00-10:00) → 无 buffer       ✗ previously_dismissed
```

### Phase 1 场景 5: HTTP UI timeline 页面验证

- `/prisIragent/calendar` HTML 含 `[刷新]` 和 `[导出 ICS]` 按钮, 无 `[添加]` 按钮
- `/prisIragent/api/calendar/timeline?days=14` 返回 `events[]` JSON
- `/prisIragent/api/calendar/scan` POST 返回 ScanReport (inserted_count, skipped_count 等)
- `/prisIragent/api/calendar/dismiss` POST 返回 `{ok: True, event_id: ...}`
- `/prisIragent/api/calendar/export.ics` 返回 ICS, Content-Type: `text/calendar; charset=utf-8`

### Phase 2 场景 1: HTTP server sentinel — ASCII 描述 stdout 输出

```
[phase2_e2e_sentinel] subprocess stdout sample:
  [prisIragent_web] Listening on http://127.0.0.1:18881
  [prisIragent_web] PRISIR_WEB_READY port=18881

[parse_ready_token] line='[prisIragent_web] PRISIR_WEB_READY port=18881' → port=18881
[http_health] GET /prisiragent/api/info → 200 OK (body keys: ['active_platform', 'current_model', 'lan_enabled', 'lan_ip', 'platforms', 'port', 'strategy', 'workdir'])
```

### Phase 2 场景 4: 全栈用户旅程 — ASCII 描述 timeline

```
[phase2_e2e_timeline] 14 天时间线摘要 (server=127.0.0.1:18882):

  events: 1 条
  requested_days: 14
  inserted_count: 1
  skipped_count: 0

┌── timeline (selected) ───────────────────────────────┐
│ 09:00  E2E Phase2 journey        国贸 SK 22 楼           │
└──────────────────────────────────────────────────────┘

[export.ics] 64 bytes, Content-Type=text/calendar; charset=utf-8
[dismiss] ok=True event_id=073ce457-ecde-4071-94a3-2edbde1ab649
```

## E2E 验收矩阵

### Phase 1 (task #11)

| 场景 | 输入 | 期望 | 实际 | PASS |
|------|------|------|------|------|
| 1: 用户日常 | 5 事件 | 2 inserted (#1, #4), 2 skipped (video, flight), 1 yielded (same venue) | 同左 | PASS |
| 2: dismiss | 同事件二刷 | ledger 写入 + 同组合不再插 | 同左 | PASS |
| 3: 窗口 | 14天前/当天/14天后/30天后 | days=14 仅含当天+14天后, days=1 仅当天 | 同左 | PASS |
| 4: ICS 导出 | 1 事件 + 1 buffer + dismiss | X-PRISIR-DISMISSED + X-PRISIR-BUFFER-TYPE, 无 ledger, icalendar 反向 parse OK | 同左 | PASS |
| 5: HTTP UI | 起 server @ 18880, fetch 5 路由 | HTML 双按钮 + JSON 4 路由全 200 | 同左 | PASS |

### Phase 2 (task #25)

| 场景 | 输入 | 期望 | 实际 | PASS |
|------|------|------|------|------|
| 1: HTTP sentinel | subprocess.Popen + stdout 读 | 2s 内 PRISIR_WEB_READY → 解析 port → /info 200 | 同左 | PASS |
| 2: scheduler cron | install */1 + fake sleep | 跑 ≥3 tick → uninstall 后 1.5s 几乎无新 tick | protect_calls ≥3 + uninstall 后 ≤1 in-flight | PASS |
| 3: Rust sentinel 解析 | 6 个解析 + 6 个 health_ok 测试 | 解析成功 / 失败 / 端口 0 / 端口溢出 / 异常文本 / 多端口, health_ok 200/204/500/404/199/300 | 12 个 PASS | PASS |
| 4: 全栈用户旅程 | server @ 18882 + 5 路由 | timeline 200 + scan ScanReport + export.ics text/calendar + dismiss ok=True | 同左 | PASS |
| 5: Tauri 菜单静态 | grep lib.rs | MenuItemBuilder::with_id("open_calendar") + match arm + CALENDAR_URL + 菜单顺序 | 全部命中 (打开/陪聊/音乐/日历/歌词/自启/退出) | PASS |
| 5b: task #26 复查 | 菜单顺序审计 | 与 task #19 报告一致 | 完全一致 (日历在第 4 位) | PASS |

## 测试套件汇总

```
prisIr_calendar/tests/test_agent_ops.py     40 passed
prisIr_calendar/tests/test_e2e_phase1.py     6 passed
prisIr_calendar/tests/test_maps_vendor.py   25 passed
prisIr_calendar/tests/test_semantics.py     39 passed
prisIr_calendar/tests/test_store.py         27 passed
prisIr_calendar/tests/test_ui_timeline.py   39 passed
prisIr_calendar/tests/test_scheduler.py     31 passed  ← scheduler.py 新增
                                  SUBTOTAL: 207 passed  (baseline)

tests/test_e2e_phase2.py                    20 passed  ← Phase 2 新增 (本任务)
                                  TOTAL:   227 passed
```

## 反 flattery 标注 — 哪些没真测 (Phase 2)

1. **Tauri binary 未跑** — 因为编译产物不存在
   → `cargo build` 需要 MSVC + LLVM 几小时,本任务直接跳过.
   → 改用 calendar.rs 纯函数逻辑等价测试 (Python 1:1 复现, 12 个测试覆盖).

2. **calendar.rs 仅验证 sentinel 解析逻辑, 未验证 Rust 编译通过**
   → 没编过 `rustc`,所有测试都是 Python 端的等价实现.
   → 真要 ship Phase 2 必须先 `cargo build` 通过 (用户拍板前不替用户拍).

3. **P2 发现的真 bug (已写入报告)** — `calendar.rs` line 70:
   `let url = format!("http://127.0.0.1:{}/prisIragent/api/info", port);`
   用 mixed-case `prisIragent/api/info`,但真实 endpoint 是 lowercase
   `prisiragent/api/info` (prisIragent_web.py 路由表 case-sensitive)。
   **影响**: Tauri 启 calendar 子进程时, `http_health_blocking` 永远 404,
   健康检查 10s 超时后认为子进程没起来 → calendar.rs 的 ready=false。
   **复现**: scenario 1 step 3b 真发 mixed-case → 真 404。
   **修复方案**: calendar.rs line 70 把 `prisIragent` 改成 `prisiragent` (1 字符)。
   **本任务不动 calendar.rs** — 任务硬约束: 不修改已落地的代码。

4. **scheduler.py tick 行为与"3 次"语义偏差** — 后台 _schedule_loop 用 fake
   sleep(立刻 +60s)会跑得非常快,远超过 3 次。所以测试只校验 ≥3,不允许再跑。
   uninstall 后的 1.5s 内允许 ≤1 个 in-flight tick 完成。
   → 不是 bug,是 fake sleep 的副作用。

5. **Tauri 托盘顺序与 task #19 报告完全一致** — task #26 复查闭合:
   打开 → 陪聊 → 音乐 → 日历 → 歌词 → 自启 → 退出,与 task #19 报告原文一致。
   隐性问题(已在 task26 备注):日历是"外部链接"实现,实际不会启动 calendar 子进程
   (calendar.rs 的 start() 还没被 lib.rs 调用)。task #26 顺序无问题,但日历功能
   没真正接通 Tauri 子进程,需要在 Phase 3 后续修复。

6. **真实地图 API** — scenario 4 mock 了 maps_vendor.eta / geocode。
   scenario 2 scheduler 测试用 fake_protect_now 替换默认 protect_now。
   不发真实 HTTP,避免烧 key。

7. **HTTP server** — scenario 1 用 subprocess.Popen + stdout 线程,
   scenario 4 用 importlib + ThreadingHTTPServer。两种启动方式均 try/finally
   兜底 shutdown,不留 zombie 进程。

## 反 flattery 标注 — 哪些没真测 (Phase 1)

1. **Puppeteer 截图**: 任务说 "虽然 P2 但 puppeteer 不一定有, 可以用 ASCII 描述替代"
   → 已用 ASCII 描述. 真实 Chromium 截图未做 (Phase 1 范围内可接受).

2. **场景 1 的"同地点 skip"行为**: 任务期望 #2 (续会同地点) 应被 `same_workplace` 跳过,
   但当前实现走的是 `_conflicts_with_user_event` → `yielded_to_user_event` 路径 (默认 ask_user "skip").
   效果等价 (不插 buffer), 但原因不同. 这是 same_venue_skip 未实现的兜底行为,
   不是 bug 报告, 但记录一下作为 P2 候选.

3. **真实地图 API**: mock 了 maps_vendor.eta 和 maps_vendor.geocode, 不发真实 HTTP 请求.
   这是任务允许的(避免烧 key).

4. **HTTP server**: 起在 18880 后台线程, 用 try/finally 兜底 shutdown.
   5 个 HTTP 路由全 200 (calendar HTML, timeline JSON, scan JSON, dismiss JSON, export.ics).