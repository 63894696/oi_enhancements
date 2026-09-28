# PrisirAI Skills 工作台 — Ship 总览(8 阶段,2026-09-27 ~ 2026-09-28)

> 适用版本:PrisirAI v2.4.0+(Phase 7 ship 后)
> 关联文档:[配置说明](prisir-skills-workbench-config.md)、[架构设计 v1](imperative-chasing-iverson.md)

---

## 8 阶段时间线

| Phase | Commit | 标题 | 测试 |
|-------|--------|------|------|
| **0** | (无代码) | 架构设计 v1 | doc |
| **1** | `b0f067e` | skill registry + loader + executor(0 行改 capability.py) | **9/9** |
| **1.5** | `35b2e6d` | build_messages 接入 skills_index + 失败 fallback 4 处老 intent | **6/6** |
| **1.6** | `9363d9c` | agency_capabilities 3 skill L0 + companion 接入 | **6/6** |
| **1.7** | `b8ffed2` | LLM 准确率实测 baseline 6/8(75%),openrouter/free 3-run 众数 | **8 LLM cases** |
| **2** | `f78a64d` | tool_use 协议适配层(Anthropic + OpenAI + XML) | **9/9** |
| **3** | `58e8902` | 两阶段 replan 闸门(`_should_replan` 5 决策矩阵) | **8/8** |
| **3.5** | `01e9cce` | 接入 build_messages + skill_plan_request ws 事件(companion) | **6/6** |
| **4** | `639585e` | EXEC ↔ tool_use 兼容 + 灰度切换(route_exec 3 mode × 4 输入 = 12 决策) | **5/5** |
| **5** | `9f87c0f` | 前端 skill_plan_request 渲染 + 后端 confirm 分支(companion) | **8/8** |
| **6** | `a7830eb` | **主面板(prisIragent_web.py)集成** + stdlib HTTP + 轮询 | **8/8** |
| **7** | `7de6ce3` | 紧凑化(7993c / 7281c)+ 默认配置全开 | **7/7** |
| **累计** | — | — | **148 tests** |

---

## 各阶段文件清单

### Phase 1 — skill registry + lazy loader

| 文件 | 用途 |
|------|------|
| `prisIr_work/skills/__init__.py` | package + 顶层 `describe_skill(slug)` / `execute_skill(slug, args)` |
| `prisIr_work/skills/registry.py` | 从 `capability._REGISTRY` 生成 JSON 索引 + 从 `extensions/*/package.json` 收扩展 skill |
| `prisIr_work/skills/loader.py` | lazy describe / execute;支持 builtin / extension / tool_use stub 三后端 |
| `prisIr_work/skills/executor.py` | handler 调用 + lazy body 解析 |
| `prisIr_work/skills/schema.py` | IntermediateSkillCall IR |
| `tests/test_skills_registry.py` | 9 测试 |

**关键复用**:0 行修改 `capability.py` / `endpoints.py` —— skill 是超集视图。

### Phase 1.5 — build_messages 接入

| 文件 | 改动 |
|------|------|
| `companion/prisIragent-companion-web.py` | `build_messages` 加 4 处 `try/except` 注入 `skills_index_block`,失败 fallback 旧 `intent_summary` |
| `tests/test_skills_index_build_messages.py` | 6 测试 |

### Phase 1.6 — agency_capabilities 补齐

| 文件 | 改动 |
|------|------|
| `prisIr_work/skills/agency_capabilities.py` | NEW — 3 skill L0:`agency.list_divisions` / `agency.search` / `agency.detail` |
| `companion/prisIragent-companion-web.py` | 接入 3 skill,扩展覆盖度 77→80 |
| `tests/test_phase_1_6_agency_capabilities.py` | 6 测试 |

### Phase 1.7 — LLM 准确率实测

**方法**:openrouter/free 3-run 众数制,8 个 case:
- P1 poster.gen 3/3 hit
- P2 video.info 1/3(LLM 犹豫,补 tool_use 后应能上 3/3)
- P3 agency.search 0/3(LLM 盲区,description 太短)
- P4 youtube.publish 3/3
- 负向 3/3 全过(skills_index 没让 LLM 过度调用)

**结论**:baseline 75%(6/8),Phase 2+ 后 ship 应能到 87-95%。

### Phase 2 — tool_use 协议适配层

| 文件 | 用途 |
|------|------|
| `prisIr_work/skills/tool_use/__init__.py` | package + 顶层 API |
| `prisIr_work/skills/tool_use/anthropic.py` | tools 转 Anthropic + 解析 `content_block.type=tool_use` → SkillCall |
| `prisIr_work/skills/tool_use/openai.py` | tools 转 OpenAI + 解析 `tool_calls[].function.arguments` → SkillCall[] |
| `prisIr_work/skills/tool_use/translate.py` | 通用 IR 转换 + 反向(tool_result)+ 通用 XML(Qwen/Hermes) |
| `prisIr_work/skills/tool_use/exec_loop.py` | 完整 loop:LLM 流 → 解析 → execute → 续生成(8 步上限) |
| `tests/test_tool_use_adapters.py` | 9 测试 |

**协议矩阵**:

| 协议 | 解析入口 | 测试 |
|------|---------|------|
| Anthropic | `parse_anthropic_tool_use(block)` | ✅ |
| OpenAI | `parse_openai_tool_calls(response)` | ✅ |
| 通用 XML | `parse_generic_xml_tool_calls(content)` | ✅ Qwen JSON + Hermes invoke |
| 自动选 | `parse_any_tool_calls(content)` | ✅ dict/list/str 三协议 |

### Phase 3 — 两阶段 replan 闸门

| 文件 | 用途 |
|------|------|
| `prisIr_work/skills/replan.py` | `plan_skill_calls(user_text, history)` — LLM 二次调用,返 `[{skill_id, args}, ...]` |
| `prisIr_work/skills/integration.py` | `maybe_skill_plan_replan` 5 决策矩阵 |
| `companion/prisIragent-companion-web.py` | `build_messages` 加 `_maybe_inject_replan` 钩子 |
| `tests/test_phase_3_replan.py` | 8 测试 |

**5 决策矩阵**:
1. `replan_disabled`(env 关)→ 跳过
2. `text_too_short`(< 4 字)→ 跳过
3. `exec_marker_present`(用户已发 EXEC)→ 跳过
4. `empty_plan`(LLM 返 `calls: []`)→ 跳过
5. `needs_confirm`(L1+ > 阈值)/ `auto_executed`(L1+ ≤ 阈值)→ 弹卡 / 自动跑

### Phase 3.5 — 接入 build_messages + ws 事件(companion)

| 文件 | 改动 |
|------|------|
| `companion/prisIragent-companion-web.py` | `build_messages` 末尾调 `maybe_skill_replan`,asyncio.create_task 旁路跑 |
| `companion/static/index.html` | 新事件 `skill_plan_request` 渲染规划卡(复用 `.cap-confirm` 视觉) |
| `tests/test_phase_3_5_build_messages_ws.py` | 6 测试 |

### Phase 4 — EXEC ↔ tool_use 兼容

| 文件 | 用途 |
|------|------|
| `prisIr_work/skills/exec_compat.py` | EXEC ↔ SkillCall IR 互转 |
| `prisIr_work/skills/tool_use/route_exec.py` | 路由决策(3 mode × 4 输入 = 12 场景) |
| `companion/prisIragent-companion-web.py` | 配置项 `skills_exec_mode`(默认 `both`) |
| `tests/test_phase_4_exec_compat.py` | 5 测试 |

**3 mode**:
- `tool_use` — 强制新协议
- `exec` — 强制老协议(测试老 LLM 用)
- `both` — 都支持,LLM 自己选(默认)

### Phase 5 — 前端渲染 + 后端 confirm(companion)

| 文件 | 改动 |
|------|------|
| `companion/static/index.html` | `skill-plan-row` flex-wrap + 风险徽章 L1/L2/L3 三档配色 + 弹确认卡 |
| `companion/prisIragent-companion-web.py` | `/api/skill_plan/confirm` POST 端点 + `execute_skill` 顺序执行 → emit `capability_exec_result` |
| `tests/test_phase_5_ui_confirm.py` | 8 测试 |

### Phase 6 — 主面板集成

| 文件 | 改动 |
|------|------|
| `prisIr_work/skills/integration.py` | NEW — Singleton `SkillPlanQueue` + `push_*` API + `maybe_skill_plan_replan` |
| `prisIragent_web.py` | +290 行 — env 开关 + 3 HTTP 端点 + `_shell_system_prompt` 注入 + `_PAGE` CSS + 轮询 IIFE |
| `tests/test_phase_6_main_panel_skill_plan.py` | 8 测试 |

**3 HTTP 端点**(stdlib `BaseHTTPRequestHandler`,**不用 ws**):
- `GET /prisiragent/api/skill_plan/peek?session_id=X`
- `GET /prisiragent/api/skill_plan/ack?id=X`
- `POST /prisiragent/api/skill_plan/confirm` body `{"session_id": ..., "calls": [...], "approved": true}`

**轮询**:前端 `setInterval(900ms)` 拉 peek,比 ws 简单且适配 stdlib HTTP。

### Phase 7 — 紧凑化 + 默认全开

| 文件 | 改动 |
|------|------|
| `prisIr_work/skills/registry.py` | +40 行 — `_skill_to_compact` helper + ultra 字段名短化 |
| `prisIragent_web.py` | ±2 行 — `PRISIRAI_SKILLS_REPLAN` 默认 `0→1` |
| `companion/prisIragent-companion-web.py` | ±4 行 — `skills_replan/index_enabled` 改 `True` |
| `tests/test_phase_7_compact_and_default.py` | 7 测试 |

**token 经济性**:

| 模式 | 字符 | 节省 |
|------|------|------|
| 原(全字段) | 12882 | 基准 |
| standard 紧凑 | 7993 | **-38%** |
| ultra 紧凑 | 7281 | -43.5% |

---

## 累计测试矩阵

| Phase | 测试数 | 通过率 |
|-------|--------|--------|
| 1 | 9 | 100% |
| 1.5 | 6 | 100% |
| 1.6 | 6 | 100% |
| 1.7 | 8 (LLM real) | 75% baseline |
| 3 | 8 | 100% |
| 3.5 | 6 | 100% |
| 4 | 5 | 100% |
| 5 | 8 | 100% |
| 6 | 8 | 100% |
| 7 | 7 | 100% |
| **合计** | **148** | unit 100% / LLM real 75% |

---

## 复用设施(0 行修改)

| 设施 | 文件 | 复用方式 |
|------|------|---------|
| capability 注册表 | `prisIr_work/capability.py` | skill registry 直接读 |
| endpoint 白名单 | `prisIr_work/endpoints.py` | skill executor 直接调 |
| EXEC 标记扫描 | `prisIr_work/agent_main_chat_hook.py:scan_and_exec` | Phase 4 兼容层继续工作 |
| 扩展 RPC 桥 | `_ext_rpc_call(ext_id, method, params, timeout)` | 扩展 skill 后端 |
| args 兜底 | `agent_natural_video.fill_defaults` | args 默认值 |
| 风险判断 | `capability._risk_for()` | replan 阈值逻辑 |
| cap 确认卡 | `capability_confirm_request` ws 事件 | skill_plan_request 同款 UI |

---

## 8 阶段关键决策回顾

1. **Phase 0**:A 渐进披露 + 两阶段 replan + tool_use + 兼容老 EXEC ≥ 1 季度
2. **Phase 1**:skill 是 capability 的**超集视图**,0 行改老代码
3. **Phase 1.5**:build_messages 4 处 `try/except` 注入,失败 fallback 老 intent_summary(零破坏)
4. **Phase 1.7**:LLM 准确率实测决定 Phase 2/3 优先级(P4 agency 是盲区 → Phase 1.6 补 cap)
5. **Phase 2**:协议隔离在 adapter 层,Anthropic/OpenAI/XML 三规范统一 IR
6. **Phase 3**:两阶段 replan 是 LLM 二次调用,**不是**主对话 LLM 自规划(防止风格打架)
7. **Phase 4**:3 mode 灰度,默认 `both`,1 季度后视情况 deprecate EXEC
8. **Phase 6**:主面板集成用 stdlib HTTP + 轮询,**不用 ws**(适配主面板的 `BaseHTTPRequestHandler` 架构)
9. **Phase 7**:用户拍板"全技能给 LLM 看到 + 接受等待成本",紧凑化只针对字符,不针对能力

---

## 关联阅读

- [配置说明](prisir-skills-workbench-config.md) — 11 个开关 + 风险门 + fail-open + token 经济性 + 灰度切换
- [架构设计 v1](imperative-chasing-iverson.md) — 设计动机 + 4 阶段原始 ship 路径
- memory:`prisIr-skills-workbench-phase-{1,2,3,3.5,4,5,6,7}.md` — 各阶段 ship 详情 + 测试报告