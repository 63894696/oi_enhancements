---
name: exa-prisir
description: Exa MCP 集成到 PrisirAI — T22-A semantic search provider
metadata:
  type: project
---

# Exa MCP 集成(P3j T22-A,2026-09-26)

Exa 语义搜索(embedding + LLM 重排序)接进 PrisirAI web_search
provider 池,跟 DDG/Baidu/Bing 并发 RRF 融合。env `EXA_API_KEY`
在才注册,不在 graceful 跳过。

**Why:** 用户系统环境变量有 Exa key 可用(2026-09-26),Exa 适合
研究 / 调研类查询($0.005/次),与 jina reader + gh CLI 互补。

**How to apply:** 推迟项决策遵循 [[agent-reach-upstream-decisions]] —
Exa 因 key+付费被推迟,本批重启后直接 ship。4 capability:
web.exa.{health,search,find_similar,answer} 全部 L0。