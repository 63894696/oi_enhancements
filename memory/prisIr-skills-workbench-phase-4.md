---
name: prisIr-skills-workbench-phase-4
description: Skills 工作台 Phase 4 — EXEC ↔ tool_use 兼容 + 灰度切换 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 4 ship(2026-09-28,commit `639585e`)

承接 [[prisIr-skills-workbench-phase-3-5]] Phase 3.5 ship 主对话接入;Phase 4 解**双协议共存**:新 tool_use ship 后,老 `[[EXEC: ...]]` 标记继续可用 ≥ 1 季度,按配置项灰度切换。

## 关键设计:**EXEC 优先于 tool_use**

**强信号原则** — LLM 在 ai_text 显式写了 `[[EXEC: video.cut path="x"]]`,这是 LLM 的明确意图;tool_use 是 LLM 偶尔走协议,但 ai_text 的 EXEC 标记是确定性信号。所以 `mode="both"` 默认 **EXEC 优先 + tool_use 兜底**。

## 三个核心函数

### 1. `exec_marker_to_skill_call(marker) → SkillCall`
```
EXEC marker {capability, args} → SkillCall {skill_id, args, risk, raw}
  - capability == skill_id(在 PrisirAI 里 capability == skill)
  - risk 从 registry 自动补(默认 L0,L1+ 从 desc.index.risk)
  - 复用 phase 1 desc_skill
```

### 2. `skill_call_to_exec_marker(call) → str`
```
SkillCall → "[[EXEC: <sid> k1=\"v1\" k2=\"v2\"]]"
  - 转义 \" \\\\ \\n \\r \\t
  - 空 args 也支持
```

### 3. `route_exec(ai_text, mode, tool_calls) → dict`
3 mode × 4 输入 = 12 决策场景:

| mode | EXEC in ai_text | tool_use in tool_calls | source | 走哪 |
|------|----------------|-----------------------|--------|------|
| `exec` | ✓ | any | `exec` | 老 scan_and_exec |
| `exec` | ✗ | any | `empty` | nothing |
| `tool_use` | ✓ | ✗ | `tool_use`(warning 降级 parse_any_tool_calls) | tool_use |
| `tool_use` | ✗ | ✓ | `tool_use` | tool_use |
| `both` | ✓ | any | `exec` | **EXEC 强信号优先** |
| `both` | ✗ | ✓ | `tool_use` | tool_use 兜底 |
| `both` | ✗ | ✗ | `empty` | nothing |

## companion 集成

`ai_done` 后处理(原 1646-1658 行)替换为:
```python
fcfg_e = _load_fcontext_cfg()
exec_mode = str(fcfg_e.get("skills_exec_mode", "both"))
routed = _route_exec(ai_text, mode=exec_mode)
if routed["source"] == "exec":
    # 老 EXEC → 走老 scan_and_exec 推 ws 事件(老前端不感知)
    hook.scan_and_exec(ai_text)
elif routed["source"] == "tool_use":
    # 新 tool_use → 把 SkillCall 包成 ExecMarker 走 build_confirm_request
    # emit(同款 capability_confirm_request ws 形态,前端 0 改动)
    for c in routed["calls"]:
        em = ExecMarker(capability=c.skill_id, args={k: str(v) ...})
        ev = build_confirm_request(em)
        ws.send_json(ev)
```

## 关键文件
| 文件 | 行数 | 用途 |
|------|------|------|
| `prisIr_work/skills/exec_compat.py` | 222 | 3 转换函数 + route_exec |
| `tests/test_exec_compat.py` | 175 | 5 测试 |
| `companion/prisIragent-companion-web.py` | +35 / -3 | 配置项 + ai_done 路由集成 |

## 测试(5/5 绿)
- **#1 exec_marker_to_skill_call** — EXEC → SkillCall(risk=L1 自动补)
- **#2 parse_exec_to_skill_calls** — 多 EXEC → SkillCall list
- **#3 skill_call_to_exec_marker** — 转义 + 双向 round-trip
- **#4 route_exec** — 3 mode × 4 输入 = 12 决策场景
- **#5 EXEC 优先** — mode=both 时显式 EXEC 标记忽略 tool_calls 参数

## 配置矩阵
```
~/.prisIrai/skills.yaml:
  skills_exec_mode: both          # 默认兼容两种
  skills_exec_mode: exec          # 强制老(老 LLM / 回滚用)
  skills_exec_mode: tool_use      # 强制新(纯 tool_use 灰度)
  skills_replan_enabled: false    # 默认关
  skills_replan_auto_l1_threshold: 2
  skills_replan_timeout_sec: 8.0
```

## 兼容性矩阵
| 用户 LLM | tool_use 支持 | EXEC 支持 | 走哪(mode=both) |
|---------|---------------|-----------|------|
| Claude Opus 5 / Sonnet 5 | ✅ | ✅ | EXEC(若 ai_text 含)→ tool_use |
| GPT-4o / GPT-5 | ✅ | ✅ | 同上 |
| Qwen3-Max / DeepSeek-V3.2 | ✅ 渐进 | ✅ | 同上 |
| Qwen-Turbo / 小模型 | ❌ | ✅ | EXEC |
| 自部署 vLLM(Ollama) | ❌ | ✅ | EXEC |

## 累计测试 **117**
handraw29 + free26 + agencyA5 + SkillsP1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + P3.5 6 + **P4 5**

## 5 阶段 ship 路径总览
```
Phase 0 — 架构设计 ✓(commit implicit)
Phase 1 — skill registry + lazy loader ✓(ship 2026-09-26)
Phase 1.5 — build_messages 接入 ✓(ship 2026-09-27)
Phase 1.6 — 4 类 capability 补齐 ✓(ship 2026-09-28)
Phase 1.7 — LLM 准确率实测 ✓(ship 2026-09-27)
Phase 2 — tool_use 协议适配 ✓(ship 2026-09-28 commit f78a64d)
Phase 3 — 两阶段 replan 闸门 ✓(ship 2026-09-28 commit 58e8902)
Phase 3.5 — 接入 build_messages ✓(ship 2026-09-28 commit 01e9cce)
Phase 4 — EXEC ↔ tool_use 兼容 ✓(ship 2026-09-28 commit 639585e)
```

## 风险登记
1. **EXEC 标记 vs tool_use 抖动** — 同 prompt LLM 可能偶尔切换协议;`mode=both` 的 EXEC 优先规则保证确定性强信号胜出
2. **老 LLM 不识别 EXEC** — 默认 `mode=both`,LLM 想写啥写啥;`mode=tool_use` 强制新协议时老 LLM 写的 EXEC 会被 warning 降级解析
3. **UI 兼容性** — tool_use 路径 emit 的 capability_confirm_request 跟 EXEC 路径同款 ws 形态,前端 0 改动

**Why:** 5 阶段 ship 路径的最后一段。新 tool_use ship 后不能强制老 LLM 升级;`mode` 配置让运维按用户灰度切换。Phase 1.7 baseline 6/8(75%) + Phase 3.5 replan + Phase 4 mode 切换,完整覆盖老 + 新 LLM 用户。
**How to apply:** 默认 `mode=both`;新 LLM 灰度期用 `mode=tool_use`;回滚到老 LLM 用 `mode=exec`。配置项在 `companion_asr_settings.json` 改 `skills_exec_mode` 字段。