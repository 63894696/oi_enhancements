---
name: prisir-openmontage-recon
description: calesthio/OpenMontage 项目调研 + AGPLv3 合规 + 借鉴 5 设计模式决策(2026-09-28)
metadata:
  type: project
---

# OpenMontage 调研 + 借鉴决策(2026-09-28)

承接用户决策:
> 「规模选 A(单集 60 秒短片),能否基于 github.com/calesthio/OpenMontage
>  这样的专业项目优化视频制作能力。」
> → 「选 C(学习借鉴而非嵌入),还有免费资源拿来用。」

## 项目核心事实

**calesthio/OpenMontage** — 2026 年 GitHub 增长最快的 AI 视频制作 agent 框架。

- **没有自己的 orchestrator** ——「你的 AI 编码助手 = orchestrator」哲学(读 YAML manifest + Markdown skill)
- **agent-based 架构** —— 工具按 BaseTool 契约注册,registry 自动发现
- **能力完整**:调研 / 参考视频解析 / 脚本 / 分镜 / 图生视频(20+ provider)/ 图生图(15+)/ 配音(10+)/ 音乐 / 字幕 / 剪辑合成 / 后期 / 质检
- **技术栈**:Python 3.10+ / Node.js 18+ (Remotion+HyperFrames) / FFmpeg / 30+ AI 模型
- **许可**:**GNU AGPLv3**(强 copyleft,网络服务运行也要开放源码)

## 用户决策:**C + 免费资源**

**C(学习借鉴而非嵌入)** 的 3 个理由:
1. **AGPLv3 法律风险** — vendor 整 CLI 污染 PrisirAI 整体源码,商业风险
2. **架构冲突** — OpenMontage 「AI 编码助手当 orchestrator」 vs PrisirAI 「LLM 当 orchestrator 调 skill」,两套架构
3. **没有统一高层 API** — 100+ 工具散在子目录,集成适配层成本高

**免费资源优先** — Piper TTS / Pixabay / Pexels / Archive.org / Coverr / Freesound / Edge TTS

## 5 借鉴设计模式(不受 AGPL 影响)

| 模式 | PrisirAI 落地文件 | 估时 |
|------|----------------|------|
| **1. Checkpoint 协议** | `prisIr_work/video_checkpoint.py`(NEW)+ agent_video_workflow 改造 | 1-2 天 |
| **2. 7 维度 provider scoring** | `prisIr_work/video_provider_scoring.py`(NEW)+ video_providers.yaml | 1-2 天 |
| **3. Pre-compose 校验** | `prisIr_work/video_precompose_check.py`(NEW)(角色一致性 / 字幕越界 / 音频电平 / 时长 / 资产完整) | 1-2 天 |
| **4. Post-render 自审** | 集成进 workflow 收尾(ffprobe + 帧采样 + 字幕检查) | 半天 |
| **5. 成本预算治理** | `prisIr_work/budget_governor.py`(NEW)(超预算自动降级) | 1 天 |

## 5 Phase ship 路径

| Phase | 内容 | 估时 | 累计测试 |
|------|------|------|---------|
| **P0** | 设计文档 + memory 登记(本文件 ship) | 30 分 | 156 |
| **P1** | checkpoint 协议(失败可恢复) | 1-2 天 | 161-162 |
| **P2** | provider scoring(自动选最优) | 1-2 天 | 167-169 |
| **P3** | pre-compose 校验(角色一致性 / 字幕越界等 5 项) | 1-2 天 | 172-175 |
| **P4** | 免费资源接入(Piper/Pixabay/Pexels/Edge TTS) | 1-2 天 | 180-185 |
| **P5** | 成本预算治理(超预算自动降级免费) | 1 天 | 185-190 |

**总估时**:1-2 周 ship 完 5 phase,累计 ~190 测试

## 不接的 5 个能力

| 不接 | 原因 |
|------|------|
| 整 CLI 运行 | AGPLv3 法律风险 + 架构冲突 |
| Remotion/HyperFrames runtime | Node 子进程重,集成成本高 |
| backlot 可视化看板 | PrisirAI 主面板已有 UI |
| YAML pipeline_defs 整套 | PrisirAI 用 JSON DAG 不用 YAML |
| Blender 3D 渲染 | 单集 60 秒不需要 3D |

## 单集 60 秒短片 — ship 后能力对比

**改造前(脆弱)**:
- 脚本(LLM 一次性)+ 生图 + 图生视频 + TTS + 烧字幕 + 上传
- 失败重做从零 / provider 写死 / 角色不一致 / 无预算控制

**改造后(稳定)**:
- replan 阶段 + pre-compose 校验 + workflow DAG(checkpoint 恢复)
- provider scoring 自动选最优 / P5 超预算自动降级
- P3 角色一致性自审 / P4 免费资源 ¥0 跑通
- 失败从最近 checkpoint 恢复

## 关键复用(0 行修改)

- `prisIr_work/agent_video_workflow.py` — 直接扩展加 checkpoint
- `prisIr_work/agent_natural_video.py` — 12 video capability 不动
- `prisIr_work/capability.py` — 注册表直接读
- Skills 工作台 Phase 2 已 ship 的 `_ext_rpc_call` — 扩展 stock-resources 走 SDK 桥
- yt-dlp / jina reader / HackerNews Algolia(已 ship) — API 文档 / 数据源

## 风险登记

1. **AGPLv3 边界** — 不 vendor 整 CLI,借鉴设计模式;误用代码片段污染 PrisirAI 后果自负
2. **免费资源限额** — Piper 本地无限制;Pexels/Pixabay/Freesound 有月配额
3. **provider 路由复杂度** — 7 维度打分需 30 天数据校准,初期用默认权重
4. **checkpoint 体积** — 不存二进制,只存路径 + metadata
5. **网络依赖** — mock 测试覆盖离线场景

**How to apply:**
- 用户问「视频制作怎么稳定」→ 答「5 借鉴模式 + 免费资源,详见 P1-P5 ship 计划」
- 用户问「为什么不用 OpenMontage」→ 答「AGPLv3 法律风险 + 架构冲突,借鉴模式不碰代码」
- 用户问「单集 60 秒短片能不能做」→ 答「现状能但脆弱,P1+P2 ship 后稳定,P3+P4+P5 后全自动」
- 实施 P1-P5 时,每 phase 必跑测试 + 写 memory