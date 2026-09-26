---
name: hn-prisir
description: HackerNews Algolia API 集成 — T22-B 第 8 个 web_search provider
metadata:
  type: project
---

# HackerNews 集成(P3j T22-B,2026-09-26)

HackerNews 通过 Algolia API 直接调(免 key、JSON、稳定)成为 web_search
第 8 个 provider,跟 DDG/Baidu/Bing/Exa/gh/Tavily/Serper 并发 RRF 融合。

**Why:** T22-B 原本计划接 Reddit,但实测 agent-reach 当前 index 只有
rss+youtube,reddit channel 不存在(原 README 列的 14 平台是夸大);
DDG/Baidu/Bing 已能搜 reddit.com 兜底;改去接 HackerNews — Algolia API
免 key + 全文 + tag 过滤 + 实时,价值更高且零配置。

**How to apply:** 主对话「HN 上 ... 怎么评价」「HN 热门 AI 帖」自动
走 web.hn.search/top。verify 真探活,跟 [[exa-prisir]] 并列 provider
池。Reddit 留作未来有真 API key/cookie 时的重启项目。