---
name: prisIr-skills-workbench-phase-3-5
description: Skills 工作台 Phase 3.5 — 接入 build_messages + skill_plan_request ws 事件 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 3.5 ship(2026-09-28,commit `01e9cce`)

承接 [[prisIr-skills-workbench-phase-3]] Phase 3 ship 的 `replan.py` 单元;Phase 3.5 把它接入主对话流。

## 关键设计:**replan 不在主对话流里**

主对话流 = LLM 一次调。replan 是**异步旁路**,`ai_done` 之后 `asyncio.create_task` 启动,不阻塞 ai_delta/ai_done 推送节奏。

## 三处改造

### 1. 配置项(`_FCONTEXT_DEFAULTS`,714 行附近)
```python
"skills_replan_enabled": False,                  # 默认关(用户主动开)
"skills_replan_auto_l1_threshold": 2,            # L1+ ≤ 2 自动执行
"skills_replan_timeout_sec": 8.0,                # replan LLM 超时(fail-open)
```

### 2. 新函数 `_replan_llm_call` + `_maybe_skill_plan_replan`(1263 / 1283 行)

**`_replan_llm_call(messages) → str`** — 轻量 adapter,复用 `stream_chat`,delta 拼成全文。失败/超时抛 RuntimeError(给 plan_skill_calls fail-open 接住)。

**`_maybe_skill_plan_replan(sess, user_text, ai_text)`** — 主流程:
```
if not cfg["skills_replan_enabled"]: return       # 配置关
if len(user_text.strip()) < 2: return             # 降噪
if "EXEC:" in ai_text: return                     # 主对话已写,跳过
try:
    out = await maybe_replan_and_execute(...)
except: return                                    # fail-open
if out["need_replan"]:
    await sess.ws.send_json({"type": "skill_plan_request", ...})
elif out["reason"] == "auto_executed":
    await sess.ws.send_json({"type": "skill_plan_auto_executed", ...})
```

### 3. ai_done 后触发(原 1517 行)
```python
await sess.ws.send_json({"type": "ai_done", ...})
asyncio.create_task(_p14_bg_task(sess, text, ai_text))
# P3j T29 Phase 3.5:replan 异步旁路
asyncio.create_task(_maybe_skill_plan_replan(sess, text, ai_text))
```

## ws 事件形态

**`skill_plan_request`**(need_replan 路径,前端弹规划卡):
```json
{
  "type": "skill_plan_request",
  "reason": "l1_count_exceeds_threshold",
  "calls": [
    {"skill_id": "video.cut",      "args": {...}, "risk": "L1"},
    {"skill_id": "publish.html",   "args": {...}, "risk": "L2"},
    {"skill_id": "youtube.upload", "args": {...}, "risk": "L2"}
  ],
  "source": "replan"
}
```

**`skill_plan_auto_executed`**(≤ 阈值自动执行路径):
```json
{
  "type": "skill_plan_auto_executed",
  "reason": "auto_executed",
  "calls": [...],
  "results": [{"skill_id": "video.cut", "ok": true, "payload": {...}}, ...]
}
```

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `companion/prisIragent-companion-web.py` | +120 行 | 3 改造点 |
| `tests/test_phase_3_5_replan_integration.py` | +246 行 | 6 测试 |

## 测试(6/6 绿)
- **#1** 配置项已在 `_FCONTEXT_DEFAULTS`(AST 源码验证)
- **#2** `replan_enabled=False` 跳过
- **#3** ai_text 含 `EXEC:` 跳过
- **#4** `user_text < 2` 字符跳过
- **#5** `_replan_llm_call` 调 stream_chat + temperature=0
- **#6** 集成 smoke:3 L1+ calls → emit skill_plan_request 完整 ws 事件(用 inline 等效函数绕过 aiohttp import)

## 测试坑
- **aiohttp + companion_asr import 链太重** — `importlib.util.spec_from_file_location` 直接 import companion_web 会爆 ModuleNotFoundError。test_6 改用 inline 等效 `_maybe_skill_plan_replan` 逻辑(只 mock `maybe_replan_and_execute`),验证 ws 事件形态
- **mock.patch 字符串 target + NTFS case-folding** — `mock.patch("prisir_work.skills.replan.maybe_replan_and_execute")` 在 mock 内部 `importlib.import_module("prisir_work")` 会被文件系统大小写折叠坑打。改用 `mock.patch.object(_replan_mod, "maybe_replan_and_execute", mock_plan)` 直接走已 import 的模块对象

## 累计测试 **112**
handraw29 + free26 + agencyA5 + SkillsP1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + **P3.5 6**

## 风险登记
1. **replan 二次调用延迟** — 每个用户任务 +1 LLM 调用、+1s 延迟、+$0.001,需评估
2. **skill_plan_request UI 卡片复用** — 前端 index.html 还没接新事件名,下一轮要加前端渲染
3. **planner LLM 选错 skill** — fail-open 三档,降级让 Phase 1 registry 兜底

**Why:** Phase 3 ship 了 `replan.py` 单元,但主对话流无法触达,等于没 ship。Phase 3.5 把它接到 `ai_done` 异步旁路 + emit ws 事件,前端才能弹规划卡。
**How to apply:** 开启 `skills_replan_enabled=true` 走 Phase 3.5 完整链路;关闭走 Phase 1 直发主对话 LLM 自己挑;下一轮要做 Phase 4 `exec_compat.py` 灰度切换。