---
name: agent-tool-survey
description: 2026-09-26 GitHub agent 能力层项目调研 — 决策:Playwright MCP P2 推迟,其它多不接(重叠 Easel/付费/产品边界不符)
metadata:
  type: project
---

# GitHub Agent 能力层项目调研(P3j T24,2026-09-26)

用户原话:「把 github 上 agent 能力层项目搜一遍,选一些高星项目来分析有什么我们可以
直接接入,有什么以类似 Easel 接入」。

**调研结果**(8 大类,50+ 项目):

**🟢 已 ship**(T20/T21/T22 全 ship,无需新增):
- Tavily / Exa / HN Algolia / feedparser / yt-dlp / gh CLI / jina / YouTube Data API

**🟡 类似 Easel**(T20/T10-T12 已 ship,无需新增):
- Agent-Reach → agent_reach_bridge.py(T20)
- Easel → easel_bridge.py + video_creator.py(T10-T12)

**⏸ 推迟**(有 value 但优先级低):
- **Playwright MCP**(~18.5k stars)→ M3.7 web_fetch 增强,优先级 **P2**(2026-09-26 起算)
- browser-use(~115k stars)→ 优先级 P3
- agent-screenshot / vision-skills → 优先级 P3

**🔴 不做**(跟 Easel 重叠 / 付费 / 产品边界不符):
- chinese-social-mcp / TikHub SDK / RedFox SDK / social-auto-upload(全跟 Easel 重叠或付费)
- Cline / Continue / Aider / OpenHands(整 IDE Coding Agent,跟「Web 壳 + 主对话」定位不符)
- Blender / AWS / Notion / K8s MCP(跟「个人 AI 助手」边界不符)
- Perplexity / Kagi(消费产品,API 暂未公开 agent 接入)

**Why 不做**:
- Tavily/Exa/HN/feedparser/yt-dlp/gh CLI/jina/YouTube Data API 全 ship — 边际价值递减
- 整 IDE Coding Agent 跟 PrisirAI 主对话 + web_fetch 链路是替代关系,不是互补
- Easel 已经覆盖 6 平台发布 + 视频,任何新平台封装项目都跟它重叠
- 付费服务(TikHub / RedFox)跟「零成本开放接入」原则冲突

**How to apply**:
- 用户问「要不要接 X agent 工具」 → 先查这份决策表;X 已 ship 就跳,X 推迟就报 hook,X 不做就给 1 行原因
- 若用户强烈要 browser 交互能力 → 触发 [[agent-tool-survey]] 「M3.7 Playwright 桥接」任务
- 若用户强烈要 coding agent 体验 → 先讨论产品边界(可能要单开新项目,不在 PrisirAI 范围)

详细 4 档分类 + 数据源 → [docs/agent-tool-survey-2026-09-26.md](docs/agent-tool-survey-2026-09-26.md)