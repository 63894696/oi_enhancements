---
name: audit-direct-vs-indirect
description: PrisirAI 间接接入审计(T23) — agent-reach / Easel / video_creator 子进程桥全是设计意图,无需直接化
metadata:
  type: project
---

# PrisirAI 间接接入 → 直接接入 审计(P3j T23,2026-09-26)

用户原问:「接 agent-reach 之前,可能也接过别的类似的能力层项目而没有直接接上游
工具,可以对 PrisirAI 做一次接入检查」。

**审计结论**:
- 4 处子进程桥(agent-reach / Easel 6 脚本 / Easel 9 video creator / port_registry)
  全是 ship 时已固化的设计意图,**无需也不应该改直接调**
- 7 项「直接调上游工具」已 ship(feedparser / yt-dlp / gh CLI / jina / HN / Exa / YouTube API)
  见 [[agent-reach-upstream-decisions]] + [[exa-prisir]] + [[hn-prisir]]

**Why**:
- 「能力层/中间件」分两类:
  - 真上游工具(库/API)→ 应该直接调,已全 ship
  - 平台专属能力(扫码/cookie/反检测/平台 API)→ 这不是「上游工具」,子进程桥是唯一正确设计
- agent-reach 内部已经直接调 feedparser/yt-dlp;我们的直接整合已 ship;
  子进程 = 版本/异常隔离
- Easel = 平台能力工厂(playwright + Chromium + cookie + 扫码 + AI 反检测);
  「直接 import easel」=「pip install easel」= 25+ 依赖 + 强制 Playwright,违反模块化
- video_creator 9 个 creator 强依赖 Easel 工程目录 + .env(SILICONFLOW_KEY)+ Orchesrator pipeline
- port_registry 是 PrisirAI 自身跨子服务 IPC,不是上游工具概念

**How to apply**:
- 新接「能力层项目」前先问:「它内部是直接调上游还是又包了一层?」
- 若用户再说「agent-reach 子进程能改成 Python 库吗」→ 答:agent-reach 没有 Python 库,
  纯 CLI;我们也已 ship 直接调它内部的 feedparser/yt-dlp
- 若用户说「Easel 脚本能直接 import 调吗」→ 答:那等于 pip install easel,
  25+ 依赖 + Playwright + Chromium;Easel 脚本内的 cookie/扫码/反检测都是平台能力,非「上游工具」
- 详细 4 处评估 + 何时重新评估触发器 → [[audit-direct-vs-indirect]] docs