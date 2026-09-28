# PrisirAI × OpenMontage — 设计模式借鉴方案 v1(plan)

> 2026-09-28 用户拍板:**选 C(学习借鉴而非嵌入)+ 免费资源**
> 适用版本:PrisirAI v2.4.0+,单集 60 秒短片场景
> 关联:[Skills 工作台 ship 总览](prisir-skills-workbench-shipped.md)

---

## Context

### 问题(用户原文)
> 「能否基于 github.com/calesthio/OpenMontage 这样的专业项目优化视频制作能力。」

### 用户决策
- **规模**:单集 60 秒短片(规模 A)
- **集成策略**:C(学习借鉴而非嵌入)
- **资源偏好**:**优先免费资源**(Piper TTS / Pixabay 音乐 / Pexels 视频 / Archive.org)

### 选 C 的理由
1. **AGPLv3 法律风险** — OpenMontage 是 GNU AGPLv3,**vendor 整 CLI 会污染 PrisirAI 整体源码开放**,商业风险
2. **架构冲突** — OpenMontage 是「AI 编码助手当 orchestrator」(读 YAML manifest),PrisirAI 是「LLM 当 orchestrator 调 skill」,两套架构
3. **没有统一高层 API** — OpenMontage 100+ 工具散在 `tools/video/` `tools/audio/`,集成适配层成本高
4. **借鉴模式不受 AGPL 影响** — 设计模式(checkpoint / scoring / 校验)是工业实践,各项目都能用

---

## OpenMontage 借鉴清单 — 5 设计模式

| # | 设计模式 | OpenMontage 做法 | PrisirAI 落地 |
|---|---------|----------------|--------------|
| **1** | **Checkpoint 协议** | 每个 stage 落 checkpoint JSON,失败从最近恢复 | `agent_video_workflow.py` 加 `CheckpointManager` |
| **2** | **7 维度 provider scoring** | 自动打分选最优 provider | `video_provider_scoring.py`(新文件) |
| **3** | **Pre-compose 校验** | 合成前校验资产完整性 | `video_precompose_check.py`(新文件) |
| **4** | **Post-render 自审** | 帧采样 + 音频电平 + 字幕检查 | 集成进 workflow 收尾 |
| **5** | **成本预算治理** | 超预算自动降级 provider | `budget_governor.py`(新文件) |

---

## 免费资源接入清单

### 选型原则
- **零成本优先**(免 API key 或免注册)
- **无速率硬限**(比商用宽松)
- **可商用许可**(避免版权风险)
- **本地能跑**(部分用系统二进制,不依赖云)

| 类别 | 资源 | 类型 | 集成方式 |
|------|------|------|---------|
| **TTS(语音合成)** | Piper(本地二进制) | 离线 / 免费 | 子进程 + ONNX 模型下载 |
| | Edge TTS(微软公开 API) | 在线 / 免费 / 无 key | HTTP 调用,需 polyfill 限制 |
| **音乐** | Pixabay Music | 在线 API / 免费 / 需 key 注册 | HTTP 搜索 + 下载 |
| | Free Music Archive | 在线 / 免费 / 部分需注册 | HTTP 抓取 |
| | YouTube Audio Library | 在线 / 免费 / 需 Google 账号 | 内部通过 yt-dlp 抓取 |
| **视频素材** | Pexels Videos | 在线 API / 免费 / 需 key | HTTP 搜索 + 下载 |
| | Pixabay Videos | 在线 API / 免费 / 需 key | HTTP 搜索 + 下载 |
| | Coverr | 在线 / 免费 / 无 key(CC0) | HTTP 抓取 |
| **图像** | Pexels Photos | 在线 API / 免费 / 需 key | HTTP 搜索 |
| | Unsplash | 在线 API / 免费 / 需 key | HTTP 搜索 |
| **历史素材** | Archive.org | 在线 / 免费 / 无 key | 已 ship 复用 |
| **图标/插画** | unDraw | 在线 / 免费 / CC0 | HTTP 抓取 |
| **音效** | Freesound | 在线 API / 免费 / 需 key | HTTP 搜索 |

### 已 ship 的免费资源(直接复用)
- yt-dlp(YouTube 元数据 + 下载)— Phase 2026-09-26 ship
- jina reader(URL→markdown)— 已 ship
- HackerNews Algolia(免 key)— 已 ship
- Archive.org(历史素材)— 可作 stock-footage montage 源

---

## 5 Phase Ship 路径

### Phase 0 — 设计文档(本文件)
**ship**:本文档 ship + memory 登记
**耗时**:~30 分钟
**验收**:docs/prisir-openmontage-integration-design.md + MEMORY.md 已 append

### Phase 1 — Checkpoint 协议
**新增文件**:
- `prisir_work/video_checkpoint.py`(NEW,~150 行)
  - `CheckpointManager` 类:每 stage 落 JSON checkpoint
  - `checkpoint_path(workflow_id, step_id)` → Path
  - `save(workflow_id, step_id, payload)` → None
  - `load_latest(workflow_id)` → Optional[Checkpoint]
  - `resume_from(workflow_id, total_steps)` → List[StepResult]
- `tests/test_video_checkpoint.py`(NEW,5-6 测试)

**改动**:
- `prisir_work/agent_video_workflow.py:run_workflow` 加 try/except,异常时落 checkpoint

**验收**:
- 跑一个 5 step workflow,中途 kill → 重启从 step 3 恢复
- checkpoint 文件格式 `<.prisIrai/checkpoints/wf_<uuid>/step_<id>.json`

**风险**:
- checkpoint 文件膨胀 → 加 LRU 清理(保留最近 20 个 workflow)
- 步骤产出文件大 → checkpoint 只存路径 + metadata,不存视频二进制

### Phase 2 — Provider Scoring
**新增文件**:
- `prisir_work/video_provider_scoring.py`(NEW,~200 行)
  - `Score` dataclass:7 维度(quality/speed/cost/availability/quota/history_success/history_failure)
  - `score_provider(provider_name, query)` → Score
  - `pick_best(providers, query)` → str(provider_name)
  - 配置文件:`prisIr_work/video_providers.yaml`(注册 10+ provider)
- `tests/test_video_provider_scoring.py`(NEW,6-7 测试)

**7 维度打分规则**:
| 维度 | 权重 | 数据来源 |
|------|------|---------|
| quality | 0.30 | provider 官方文档 + 用户 feedback |
| speed | 0.15 | 历史 P50 延迟 |
| cost | 0.20 | 单次调用 cost(免费 = 1.0) |
| availability | 0.10 | 当前健康检查 |
| quota | 0.10 | 用户剩余配额(免费不限 = 1.0) |
| history_success | 0.10 | 30 天成功率 |
| history_failure | -0.05 | 30 天失败率(负向) |

**验收**:
- 同 query 调 2 个 provider,选得分高的
- 降级场景:最佳 provider 配额满 → 自动选次佳

**风险**:
- 维度权重需用户校准 → 默认值基于行业常识,用户可改 yaml

### Phase 3 — Pre-compose 校验
**新增文件**:
- `prisir_work/video_precompose_check.py`(NEW,~180 行)
  - `PreComposeCheck` dataclass:角色一致性 / 字幕不越界 / 音频电平 / 时长匹配 / 资产完整
  - `run_precompose_check(workflow_result)` → CheckResult(ok, warnings, errors)
  - 失败 → 自动放弃合成,返错误清单
- `tests/test_video_precompose_check.py`(NEW,5-6 测试)

**5 项校验**:
| 校验 | 工具 | 失败处理 |
|------|------|---------|
| 角色一致性 | CLIP 相似度(本地模型) | warn → 提示用户 |
| 字幕不越界 | PIL 检测文字边缘 | error → 阻断合成 |
| 音频电平 | ffmpeg volumedetect | warn → 提示调音量 |
| 时长匹配 | ffprobe | error → 阻断合成 |
| 资产完整 | 文件存在 + 字节数合理 | error → 阻断合成 |

**验收**:
- 故意构造角色不一致的图 → 校验 warn
- 故意构造字幕越界 → 校验 error,合成不执行

**风险**:
- CLIP 模型下载 ~300MB → 默认本地 lazy 加载
- ffmpeg 系统依赖 → 文档标注 prerequisites

### Phase 4 — 免费资源接入
**新增文件**:
- `prisir_work/free_resources.py`(NEW,~250 行)
  - `pixabay_search(query, type='music')` → List[Resource]
  - `pexels_search(query, type='video')` → List[Resource]
  - `archive_search(query)` → List[Resource]
  - `piper_tts(text, voice)` → Path(下载 ONNX 模型 + 本地推理)
  - `edge_tts(text, voice)` → Path(微软公开 API)
- `extensions/free-stock-resources/`(NEW,扩展 manifest + SDK 桥)
  - 走 Skills 工作台 Phase 2 已 ship 的 `_ext_rpc_call` 模式
- `tests/test_free_resources.py`(NEW,8-10 测试,mock 网络)

**provider 注册**:
| 资源类型 | 免费 provider | key 要求 |
|---------|--------------|---------|
| TTS | Piper(本地)/ Edge TTS | 都不用 |
| 音乐 | Pixabay Music / Free Music Archive | Pixabay 要 key,FMA 不用 |
| 视频 | Pexels / Pixabay / Coverr | 都要 key,Coverr 免 |
| 图像 | Pexels Photos / Unsplash | 都要 key |
| 历史 | Archive.org | 不用 |
| 音效 | Freesound | 要 key |

**验收**:
- mock 网络调用,验证 search → return Resource 列表
- Piper 本地推理生成 1 个 5 秒短音频

**风险**:
- key 注册门槛 → 用户体验门槛,**文档标注清楚哪些要 key**
- 网络依赖 → mock 测试 + 离线 fallback

### Phase 5 — 成本预算治理
**新增文件**:
- `prisir_work/budget_governor.py`(NEW,~120 行)
  - `BudgetGovernor` 类:跟踪本次 workflow cost,超阈值自动降级
  - `decide_provider(current_choice, remaining_budget)` → str(可能降级到免费)
  - 配置:用户设单次 workflow 上限(默认 ¥10)
- `tests/test_budget_governor.py`(NEW,5 测试)

**降级规则**:
- 剩余预算 < 当前 provider 成本 → 选次佳且更便宜的
- 剩余预算 < 免费 provider 成本 → 阻断,返 cost_exceeded
- 全部免费 → 全程跑(¥0)

**验收**:
- 模拟 3 次 provider 调用,剩余预算到 ¥0 时阻断
- 剩余预算 < 当前最优 provider 成本 → 自动降级

**风险**:
- 成本估算精度依赖 provider 实际收费 → 加 5% 安全边距

---

## 累计测试预期

| Phase | 测试数 | 累计 |
|-------|--------|------|
| 已有 | 156 | 156 |
| P0 | 0 | 156 |
| P1 | 5-6 | 161-162 |
| P2 | 6-7 | 167-169 |
| P3 | 5-6 | 172-175 |
| P4 | 8-10 | 180-185 |
| P5 | 5 | 185-190 |
| **总计** | — | **~190** |

---

## 不接 OpenMontage 的 5 个能力

| 不接 | 原因 |
|------|------|
| **整 CLI 运行** | AGPLv3 法律风险 + 架构冲突 |
| **Remotion/HyperFrames runtime** | Node 子进程重,集成成本高 |
| **backlot 可视化看板** | PrisirAI 主面板已有 UI |
| **YAML pipeline_defs 整套** | PrisirAI 用 JSON DAG 不用 YAML |
| **Blender 3D 渲染** | 单集 60 秒不需要 3D |

---

## 单集 60 秒短片 — ship 后能力

### 改造前(脆弱)
```
用户:「做一个 60 秒产品宣传短片」
↓
PrisirAI 单 agent 循环:
  - 脚本(LLM 一次性)
  - 生图(image-gen × N)
  - 图生视频(video.create)
  - TTS 配音
  - 烧字幕(video.burn)
  - 上传
  ↓
问题:失败重做从零 / provider 写死 / 角色不一致 / 无预算控制
```

### 改造后(稳定)
```
用户:「做一个 60 秒产品宣传短片」
↓
PrisirAI Skills 工作台触发:
  1. replan 阶段:LLM 二次调用,规划 skill 调用链
  2. pre-compose 校验:角色一致性 / 字幕边界 / 音频电平
  3. workflow 跑(DAG + checkpoint):
     - 脚本生成(LLM)
     - 场景拆分(LLM)
     - 图生视频(P2 scoring 自动选 provider)
     - TTS(P4 优先 Piper 免费,预算够才用 ElevenLabs)
     - BGM(P4 Pixabay 免费)
     - 烧字幕
     - 后处理(P3 帧采样检一致性)
  4. 成本治理(P5 超预算自动降级)
  5. 上传 + 反馈
  ↓
收益:
  - 失败从最近 checkpoint 恢复(不重头)
  - provider 自动选最优 + 自动降级
  - 角色一致性 P3 自审
  - 全部免费资源的话,整集 ¥0
```

---

## 关键复用

| 现有设施 | 复用方式 |
|---------|---------|
| `prisir_work/agent_video_workflow.py` | 直接扩展加 checkpoint(P1) |
| `prisir_work/agent_natural_video.py` | 12 video capability 不动 |
| `prisir_work/capability.py` | 注册表直接读 |
| Skills 工作台 Phase 2 已 ship 的 `_ext_rpc_call` | 扩展 stock-resources 走 SDK 桥 |
| yt-dlp(已 ship) | YouTube 音乐 / 历史素材复用 |
| jina reader(已 ship) | Pixabay / Pexels API 文档用 |
| HackerNews Algolia(已 ship) | provider 健康检查可参考 |

---

## 风险登记

1. **AGPLv3 边界** — 不 vendor 整 CLI,借鉴设计模式;若误用代码片段污染 PrisirAI,后果自负
2. **免费资源限额** — Piper 本地无限制;Pexels / Pixabay / Freesound 有月配额
3. **provider 路由复杂度** — 7 维度打分需 30 天数据校准,初期用默认权重
4. **checkpoint 体积** — 不存二进制,只存路径 + metadata
5. **网络依赖** — mock 测试覆盖离线场景

---

## 用户已拍板

- C(学习借鉴而非嵌入)
- 优先免费资源
- 不碰 AGPLv3 代码

## 反 flattery 自检

- **不假设借鉴能直接降低工作量** — OpenMontage 设计的代码量很大,借鉴模式需重构,**5 phase 估 1-2 周**
- **不假设免费资源够用** — 商用 provider 偶尔必要(高画质/低延迟),预算治理就是为这个场景
- **不假设 60 秒短片 = 单 agent** — 1-2 个 agent 协作就够,5+ agent 是过度设计
- **不假设 P0-P5 线性** — 用户可能随时调整优先级,Phase 0 设计留口子

## 关联阅读

- [Skills 工作台 ship 总览](prisir-skills-workbench-shipped.md) — workflow + checkpoint 复用基础
- [Skills 工作台配置说明](prisir-skills-workbench-config.md) — 配置入口与降级语义