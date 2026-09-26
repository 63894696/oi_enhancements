---
name: agent-reach-upstream-decisions
description: "PrisirAI × Agent-Reach 上游工具决策记录(2026-09-26 ship P3j T21-D)— 14 项盘点+ship/推迟/不做 3 档分类,防止下次重复问"
metadata:
  type: project
  originSessionId: 8770f801-490d-474f-9950-81a5d72bde7b
  modified: 2026-09-26T07:30:00.000Z
---

# Agent-Reach 上游工具集成决策(P3j T21-D,2026-09-26 ship)

## 为什么做

User asked「请将其上游工具逐一集成」→ T21-A/B/C 跑完 14 项盘点后,
**把决策结果固化**,下次不再重复讨论同一话题。

## 决策表(摘要,详见 docs/why_ai-tools-decisions-2026-09-26.md)

### ✅ 已 ship(6 项)
- feedparser(T21-A)— 零依赖,自带 ETag 条件 GET
- yt-dlp(T21-B)— 已在用 1/200,提升为通用 fetcher
- gh CLI(T21-C)— 系统 CLI 已装 + 已登录,绕过 jina/HTML
- Jina Reader(T20-I + T20-I.2)— hosted_no_key 20 RPM 免配置
- OpenAI Whisper(T11)— video_ext_creator.py 字幕
- playwright(已 ship)— wechat-publisher 视频 Tab 后端

### ⏸ 推迟(9 项,写明何时重新评估)
- Exa MCP(需 key,价值待评;若有 MCP 协议接入再评)
- OpenCLI(功能与 jina/feedparser 重叠)
- rdt-cli / twitter-cli / xhs-cli / bili-cli / linkedin-mcp(全部需鉴权,与 agent-reach 没差异)
- mcporter(无关)
- Node.js(若 PrisirAI 切 Node 后端再评)
- xueqiu / xiaoyuzhou / boss(用户未提)

### ❌ 明确不做(3 项)
- bs4 / lxml(jina markdown 已替代)
- aiohttp / httpx(stdlib urllib 够用)
- ffmpeg(转码不在范围;yt-dlp 内部已集成)

## Why

每次盘点都重复问同一批工具 = 浪费时间。固化决策表后:
- 下次再问「xhs-cli 接了吗」→ 直接看决策表 + 推迟理由
- 季度复盘时按「何时重新评估」列触发器检查

## How to apply

- 主对话 / 编程时若涉及 X 平台 / X 工具 → 看决策表 3 档分类
- 任何 1 项想重启 → 看「何时重新评估」列
- 新工具涌现 → 沿用 3 档分类法(✅ ship / ⏸ 推迟 / ❌ 不做)

## 关键 commit

- T21-A/B(feedparser + yt-dlp):`feat(p3j+t21-a/b)`(6bed7b0)
- T21-C(gh CLI):`feat(p3j+t21-c)`(dbb1781)
- T21-D(本批):docs + memory 决策记录

## 相关

- [docs/why_ai-tools-decisions-2026-09-26.md](docs/why_ai-tools-decisions-2026-09-26.md) — 完整决策表
- [memory/feedparser-prisir.md](memory/feedparser-prisir.md)
- [memory/ytdlp-prisir.md](memory/ytdlp-prisir.md)
- [memory/gh-prisir.md](memory/gh-prisir.md)