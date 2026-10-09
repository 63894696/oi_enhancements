---
name: artcraft-skip-decision
description: ArtCraft (storytold/artcraft) 不 ship Phase A 桥接扩展的决策 — 云端 SaaS + 隐私红线 + API gated 违反 PrisirAI 哲学
metadata:
  type: reference
---

# ArtCraft (storytold/artcraft) 跳过决策

**2026-10-08 调研;决策:不 ship artcraft-bridge-status 扩展**。

## 项目概要
- **GitHub**: https://github.com/storytold/artcraft(10,361 stars / 1,541 forks / Rust 1.37 GB)
- **主页**: https://getartcraft.com
- **类型**: Rust 桌面 IDE("the IDE for artists"),用于 AI 图像/视频/3D 创作
- **最新版**: artcraft-v0.41.0(2026-09-26)
- **License**: NOASSERTION / "Other"(自定义非标准,商业集成风险)
- **API 文档**: https://storytold-docs.netlify.app/ + 仓内 _docs/artcraft_omni_api.md
- **62 个模型**: 16 image / 视频 / 音频 / mesh / splat 跨 ArtCraft/Grok/Midjourney/Sora/World Labs

## API 表面
- **基础 URL**: `https://api.storyteller.ai`(云) / `http://localhost:12345`(dev)
- **鉴权**: `Authorization: Bearer artcraft_api_xxx`(53 字符,Crockford-base32)
- **主端点**:
  - `POST /v1/omni_api/generate/{video,image,audio,mesh,splat}`
  - `GET /v1/omni_api/job_status/job/{token}`(轮询)
  - `GET /v1/omni_api/job_status/batch?tokens=...`
- **API 访问 gated per account**:需联系 ArtCraft 团队开通,**无 self-service**

## 接入路径评估

### 路径 A:接 Omni 云端 API — **不 ship**
**否决原因:**

1. **隐私红线冲突(P3.10b)**:云端 SaaS,用户输入 prompt + 上传图片/视频/音频到 ArtCraft 服务器
   - 违反 "0 上传/外传" 红线
   - 违反 P3.10b"用户隐私阈值:0 上传/外传,不只是无 Key"
   - PrisirAI 已有专门路径处理云端 AI 服务:见 [PrisirAI Agent 视频能力 P3j T16](prisIr-agent-main-chat-hook.md)(T16-A/B/C/D ship 全部走云端 AI 服务的统一路径)

2. **API 访问 gated**:不是 self-service,不能 ship 给普通用户(每用户需联系 ArtCraft 团队单独开通)

3. **License NOASSERTION**:自定义非标准,商业集成有风险,法律审查成本高

4. **战略错位**:PrisirAI 12 个 Phase A 扩展**全部**是"自托管 + 100% 本地",ArtCraft 是云端 SaaS,完全不同的集成模式

### 路径 B:接 ArtCraft 桌面应用 — **不 ship**
**否决原因:**

- ArtCraft 桌面是 Rust 写的,**不提供 HTTP API 给第三方**
- 没有 REST endpoint、没有 token-based auth surface
- 仅 CLI / GUI 操作
- 第三方只能通过 UI 自动化(剪贴板/截图)**不是 API 集成**
- 范畴完全不同(类似"接 ComfyUI local server"vs"调 Midjourney API")

## 用户原话与判断
用户原话:**"插一下,看看这个项目能不能接入。相信即便有同类扩展竞争力未必能比过这个。"**

- 用户已正确识别:ArtCraft(10K stars,155 用户)竞争优势极强,**单纯 ship 一个 metadata 桥接扩展意义不大**
- 即便我们 ship 桥接扩展,只是把"用户 ArtCraft 账户里有 5 个模型"这种 metadata 暴露给 LLM,完全不触 ArtCraft 的核心能力(image-to-3D / 3D compositing / 角色 pose / scene blocking)
- ArtCraft 真正的 5 大能力(canvas 2D/3D 编辑 / 角色姿态 / 场景 kitbashing / 角色身份迁移 / 背景去除)**没有 API 表面**
- ship 一个"metadata 桥接扩展"是 product 噪音,不是用户价值

## 推荐替代(用户价值更高)

1. **「工具发现」Tab 借鉴**:ArtCraft 桌面 + Krita + Blender + ComfyUI 等开源创作工具,在 PrisirAI 内做"本地创作工具目录"Tab
   - 路径:不 ship 扩展,走 `[ext-inventory-injected]` 路径(已有 inventory 注入)
   - 优势:不锁死某个工具,给用户选择权

2. **云端 AI 能力路由**:ArtCraft 用的 Grok/Midjourney/Sora/World Labs,**这些走 PrisirAI 已有 P3j T16 主对话接视频能力**(已 ship)

3. **Open-source ArtCraft-like 工具调研**:ArtCraft 是商业闭源(custom license),找开源替代(compose-studio / krita-ai / stable-ui)更适合 PrisirAI 哲学

## 决策
**不 ship artcraft-bridge-status 扩展**。在 `memory/artcraft-skip-decision.md` 留档,**用户被问及时给本条链接**。

## 相关
- 用户问询时间: 2026-10-08
- 同期已 ship 13 个自托管扩展(Linkwarden/Habitica/Paperless 等)
- 跨入域累计 13 类(媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签/习惯/文档)
- ArtCraft 模型集参考价值:62 模型分(image/video/audio/mesh/splat),可以作为 PrisirAI 自身多媒体能力规划的对照表
