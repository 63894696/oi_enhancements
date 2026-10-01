---
name: prisIr-p2-rules-shipped
description: P2-Rules AGENTS.md 解析 + build_messages 注入(完全 ECC 对齐)
metadata:
  type: project
---

P2-Rules(2026-10-01 ship):
- `prisir_work/rules.py` —— ProjectRules dataclass + AGENTS.md frontmatter 解析(无 PyYAML 依赖)
- `prisir_work/tests/test_rules.py` —— 17/17 PASS
- `companion/prisIragent-companion-web.py:build_messages` 注入段(在 SYSTEM_PROMPT 之后、preset 之前)
- 配置项:`rules_enabled`(默认 True)、`rules_max_chars`(默认 4000)
- 失败 fallback:任何 IO/解析异常 → 静默,不抛

**Why:** 项目级规范 + 用户级偏好首次零代码生效(改 AGENTS.md 即可);与 ECC/Cursor/Codex/OpenCode 通用。
**How to apply:** 用户写 `AGENTS.md` 沿 frontmatter + markdown body;`load_rules_for_project(cwd)` 返 (user, project) → `merge_rules` 项目级覆盖用户级 → `format_rules_for_prompt` 输出截断到 4000 字符。

边界:
- 不照搬 ECC `rules_max_chars=8000`:PrisirAI 主对话长文本诉求强,默认 4000
- 不照搬 6 原语全做:只做 Rules/Hooks/Instincts(Skills/Agents/Memory 已 ship)

参考 [[prisIr-p2-hooks-shipped]] + [[prisIr-p1-instincts-shipped]]