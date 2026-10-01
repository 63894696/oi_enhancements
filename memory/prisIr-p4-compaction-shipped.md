---
name: prisIr-p4-compaction-shipped
description: P4-Compaction 上下文压缩(借鉴 jcode-compaction-core,80%/95% 双阈值 + 中文摘要)
metadata:
  type: project
---

P4-Compaction(2026-10-02 ship):
- `memory/compaction.py` —— 新增 `CompactionManager`(578 行)
- `memory/tests/test_compaction.py` —— **14/14 PASS**
- `companion/prisIragent-companion-web.py:build_messages` —— 在 return msgs 之前插入 compaction 钩子

13 个常量沿用 jcode:
- `DEFAULT_TOKEN_BUDGET = 200_000`
- `COMPACTION_THRESHOLD = 0.80` / `CRITICAL_THRESHOLD = 0.95`
- `RECENT_TURNS_TO_KEEP = 10` / `MIN_TURNS_TO_KEEP = 2`
- `IMAGE_TOKEN_COST = 1_600`(避免图片反复触发连续压缩)
- `EMERGENCY_IMAGE_MAX_CHARS = 1_024` / `EMERGENCY_TOOL_RESULT_MAX_CHARS = 4_000`
- `SYSTEM_OVERHEAD_TOKENS = 18_000`
- `PAYLOAD_IMAGE_CHAR_BUDGET = 12MB`(应对 413 失败)
- `CHARS_PER_TOKEN = 4`

`SUMMARY_PROMPT` 中文化 4 段(沿用 jcode 结构):
- 上下文 / 已做 / 当前状态 / 用户偏好

`CompactionManager` API:
- `estimate_tokens(messages)` —— text chars/4 + IMAGE_TOKEN_COST×图片数 + SYSTEM_OVERHEAD
- `needs_compaction(messages)` —— 返 (bool, CompactionAction)
- `compact(messages, llm_call=None)` —— 主入口;`llm_call=None` 时跳过软压只走紧急硬压
- `emergency_truncate_tool_result(content)` / `emergency_truncate_image(payload)` —— 4000 / 1024 截断

**Step 7 简化设计**: 无同步 llm_call 通道,跳过 80% 软压,**只走 95% 紧急硬压**。Step 7.1 后续可加同步 LLM 调用实现 80%+ 软压。

**Why**: 长会话(50+ 轮)主对话会撞 200K token 窗口;不压缩直接 413。jcode 4 档阈值(80% / 95% / 10% / 紧急)+ IMAGE_TOKEN_COST 平摊是已 ship 的稳定范式。

**How to apply**: `build_messages` 在 msgs 拼好之后、return msgs 之前调 `CompactionManager.compact()`;失败 fallback:任何异常 → log.exception + 返原 msgs。

边界:
- 不照搬 BackgroundStarted 异步(PrisirAI 同步 summary)
- 不照搬 Confirm + Reflect gate(无二次 LLM)
- 不照搬 SUMMARY_PROMPT 原文(中文 4 段)
- Step 7 退一步只做 hard 压缩(无同步 LLM 通道)

配置项(`prisirmp.config.yaml`):
```yaml
compaction_enabled: true
compaction_threshold: 0.80
compaction_critical: 0.95
compaction_recent_turns: 10
```

参考 [[prisIr-p3-hookrisk-shipped]] + [[prisIr-p1-instincts-shipped]]