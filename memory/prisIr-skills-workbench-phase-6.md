---
name: prisIr-skills-workbench-phase-6
description: Skills 工作台 Phase 6 — PrisirAI 主面板(prisIragent_web.py)集成 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 6 ship(2026-09-28,commit `a7830eb`)

承接 [[prisIr-skills-workbench-phase-5]] ship 至 companion 的发现:**Phase 3.5/4/5 的 ws 事件从未传到 PrisirAI 主面板**(`prisIragent_web.py`,14580 行)。本 Phase 解这架构 gap。

## 关键发现(用户原话)

> 「讲一下语伴本身只是一个扩展功能,虽然也能当前端发任务到 PrisirAI 执行,但真正主要对话窗口默认是 PrisirAI 的那个左侧有历史对话列表,上端各相关功能调用按钮一排还有菜单功能的窗口,检查一下是否没做配置。」

PrisirAI 主面板 vs companion 是两套独立架构:
- **主面板** = `prisIragent_web.py` 14580 行,stdlib `BaseHTTPRequestHandler` + `ThreadingHTTPServer`,**前端走 polling**(不是 ws/SSE)
- **companion** = `companion/prisIragent-companion-web.py`,aiohttp + ws 事件

Phase 3.5/4/5 只 ship 到 companion,主面板 0 接入。

## 设计

### 1. 复用层 `prisIr_work/skills/integration.py`
- `skills_index_block()` — 包装 describe_registry_compact,失败静默
- `SkillPlanQueue` — singleton FIFO + 32 上限 + thread-safe,沿用 `_INJECT_QUEUE` 范式
- `push_skill_plan_request/auto_executed/confirm_ack` — 3 事件类型
- `maybe_skill_plan_replan(user_text, answer, session_id, plan_llm_call, ...)` — 主面板 replan 入口
  - 6 决策矩阵:replan_disabled / text_too_short / exec_marker_present / empty_plan / needs_confirm / auto_executed
  - fail-open(异常 → 跳过)
  - 风险从 registry 取(不信任 LLM JSON 的 risk)

### 2. 主面板钩子(`prisIragent_web.py`)
- `_shell_system_prompt` 末尾追加 `skills_index` 段(env `PRISIRAI_SKILLS_INDEX` 默认 1=开)
- `_run_chat_thread` 在 `chat_done` 之前调 `maybe_skill_plan_replan`(env `PRISIRAI_SKILLS_REPLAN` 默认 0=关,异步旁路 `asyncio.create_task`)
- 3 HTTP 端点:`GET /api/skill_plan/peek`(session_id 过滤)、`GET /api/skill_plan/ack`(id 校验)、`POST /api/skill_plan/confirm`(顺序 execute_skill → emit ack)

### 3. 前端(`_PAGE`)
- CSS:6 skill-plan-* 类 + L0/L1/L2/L3 4 档风险配色(沿用 Phase 5 #6b8e7f/#c79a3a/#b65c5c)
- `_setupSkillPlanPolling` IIFE,沿用 external_inject 范式,每 900ms peek
- `showPlanCard` 弹卡 + `renderAutoExec` sys 卡 + `renderAck` 系统提示
- 按钮 click → POST /api/skill_plan/confirm

### 默认配置(灰度)
- `skills_index_enabled=true`(LLM 看得到 80 skill 名)
- `skills_replan_enabled=false`(L0 直发,不弹卡)
- 用户手动开 replan 才走两阶段

## E2E 验证(puppeteer + demo server)

- 模拟 push 3 项 skill plan
- 前端 polling 拉到 → 弹卡
- 截图确认:🧩 规划卡标题 + 3 项 + L1 绿 + skill_id + args 缩进 + 双按钮
- 点「我确认」→ closeCard + fetch confirm

## 测试(8/8 绿)
- **#1** integration.py 顶层 API + Queue 闭环(push×3/peek×2/ack)
- **#2** skills_index_block 返 12969c 索引
- **#3** 6 replan 决策矩阵
- **#4** GET /peek 端点
- **#5** GET /ack 端点
- **#6** POST /confirm 端点(execute_skill + approved + user_cancelled)
- **#7** _shell_system_prompt 末尾 skills_index(env 开关)
- **#8** _PAGE CSS(6 类 + 4 档配色) + polling + 3 事件 handler + 按钮

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/skills/integration.py` | +342 行(NEW) | 主面板集成层(singleton Queue + skills_index + replan) |
| `prisIr_work/skills/__init__.py` | +25 行 | 顶层 API 导出 |
| `prisIragent_web.py` | +290 行 | _shell_system_prompt / _run_chat_thread / 3 HTTP 端点 / CSS / JS polling |
| `tests/test_phase_6_main_panel_skill_plan.py` | +274 行(NEW) | 8 测试 |

## 累计测试 **141**
PRIO 89 + P1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + P3.5 6 + P4 5 + P5 8 + **P6 8**

## 7 阶段 ship 路径总览
```
Phase 0 — 架构设计 ✓
Phase 1 — skill registry + lazy loader ✓(2026-09-26)
Phase 1.5 — build_messages 接入 ✓(2026-09-27)
Phase 1.6 — 4 类 capability 补齐 ✓(2026-09-28)
Phase 1.7 — LLM 准确率实测 ✓(2026-09-27)
Phase 2 — tool_use 协议适配 ✓(commit f78a64d)
Phase 3 — 两阶段 replan 闸门 ✓(commit 58e8902)
Phase 3.5 — 接入 build_messages ✓(commit 01e9cce)
Phase 4 — EXEC ↔ tool_use 兼容 ✓(commit 639585e)
Phase 5 — 前端渲染 + 后端 confirm ✓(commit 9f87c0f,companion)
Phase 6 — 主面板集成 ✓(commit a7830eb,2026-09-28)
```

## 风险登记
1. **fail-open 全开** — replan LLM 失败 / parse 失败 / 超时 8s 全部静默降级,用户无感
2. **execute_skill confirm 闸门** — Phase 6 confirm 端点 force=True 直接调,跳过用户确认(配套 needs_confirm 弹卡已前置)
3. **风险从 registry 取** — LLM JSON 给的 risk 被忽略(registry 是 single source of truth)。这与 companion Phase 3.5 行为一致
4. **polling 900ms 延迟** — 跟 companion 的 ws 即时推送相比,主面板有 ≤900ms 感知延迟
5. **demo server 截取 _PAGE bug** — 直接用 `find('"""')` 会切错位置,真实后端用 `ast.Constant.value` 提取(后续 main panel 测试 helper 复用)

**Why:** Phase 5 ship 后用户纠正"主面板 ≠ companion",Phase 6 把 Skills 工作台集成到真正的主对话窗口,完成架构闭环。
**How to apply:** 灰度开启 PRISIRAI_SKILLS_REPLAN=1 观察 replan 命中率;Phase 7 可考虑 LLM 实测验证主面板 skills_index 准确率(沿用 Phase 1.7 baseline 6/8 hit 模式)。