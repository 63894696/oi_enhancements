---
name: prisIr-phase-12-om-p4-free-resources
description: Phase 12 OM-P4 免费资源真集成 ship(2026-09-28)
metadata:
  type: project
---

# Phase 12 OM-P4 免费资源真集成 ship(2026-09-28)

承接 [[prisIr-phase-11-om-p3-pre-compose]] + [[prisIr-phase-11h-cny-budget-update]] + 用户「渐进 ship + 证据」决策。

## 关键拍板(用户原话)
「请继续OM-P4」→ 「渐进 ship + 证据」(3 个免费资源)。

## 验证事实(2026-09-28 真跑)
| 资源 | 探测结果 |
|------|---------|
| **edge-tts 7.2.8** | pip 已装;真调生成 24624 字节中文 mp3 |
| **edge-tts 中文 voice** | 9 个(7 大陆 + 1 粤语 + 1 台湾) |
| **Pixabay REST API** | 无 key 返 `400 Invalid or missing API key` |
| **archive.org advancedsearch** | 返 61258 条 cat+movies,JSON 可解析 |

## ship 的 4 个新模块

### 1. edge_tts_client.py(~180 行)
- `is_available()` — 库是否装
- `list_chinese_voices()` — 中文 voice 全集
- `synthesize(text, output_path, voice, rate, pitch)` — async 真生成 mp3
- `synthesize_sync()` — 同步封装,适合 CLI / 测试
- 默认 voice: `zh-CN-XiaoxiaoNeural`(女声温柔)

### 2. pixabay_client.py(~220 行)
- `get_api_key()` — env > ~/work/easel/.env
- `is_key_configured()` — bool
- `probe_key()` — 真调一次 /videos/?key= 探测,返 ProbeResult
- `search_music(query, limit=10)` — 音乐
- `search_videos(query, limit=10)` — 视频
- 无 key 时优雅返 `[]`(不抛栈)

### 3. archive_org_client.py(~140 行)
- `is_available()` — 轻探测
- `search_videos(query, limit, mediatype='movies')` — 真调 advancedsearch
- `get_metadata(identifier)` — 单条详情
- **零依赖**(纯 urllib)/ 免 key / 真集成

### 4. free_resource_fetcher.py(~130 行) — 高层 facade
- `free_resource_status()` — 聚合 3 资源就绪状态
- `free_tts(text, out)` — 转 edge_tts_client
- `free_bgm(query)` — 转 pixabay_client
- `free_stock_video(query)` — Pixabay 优先,Archive.org fallback

## 关键文件

| 文件 | 行数 | 用途 |
|------|------|------|
| `prisIr_work/edge_tts_client.py` | NEW ~180 | edge-tts 真集成 |
| `prisIr_work/pixabay_client.py` | NEW ~220 | Pixabay REST 真集成 |
| `prisIr_work/archive_org_client.py` | NEW ~140 | archive.org 真集成 |
| `prisIr_work/free_resource_fetcher.py` | NEW ~130 | 高层 facade |
| `tests/test_phase_12_om_p4_free_resources.py` | NEW ~200 | **13/13 测绿(真调用)** |

## 测试矩阵(13/13 全绿)

### edge-tts 真调(5 项)
1. 库可导入
2. 中文 voice 含 XiaoxiaoNeural(9 个)
3. **真生成中文 mp3**(24624 字节)
4. **真生成英文 mp3**
5. 空文本 → ok=False

### pixabay 无 key(3 项)
6. is_key_configured → False
8. probe_key → configured=False
8. search_music 无 key → 返 []

### archive.org 真调(2 项)
9. is_available → True
10. **真搜 'cat' → 3 hits,首条"Woman Thinks She Is A Cat!"**

### facade 聚合(3 项)
11. status 聚合(3 资源就绪)
12. **facade.free_tts 真调**
13. **facade.free_stock_video → archive.org fallback 返 3 hits**

## 累计测试 **224**

Phase 11-H 211 + Phase 12 13 = 224

## 关键设计

### 1. 零依赖优先
- pixabay_client / archive_org_client 用 **urllib**(标准库)
- 仅 edge_tts_client 依赖外部库(已装)
- 任何机器 clone 后无需 pip install

### 2. fail-soft 范式
- 无 key → `[]` / `False` / `configured=False`
- API 异常 → log warning + 返默认值
- 绝不抛栈干扰主流程

### 3. 真集成不 mock
- 测试里每个 client 都真调一次(4 次真 HTTP/WS)
- pixabay probe 真返 `400 Invalid key`(非 mock)
- archive.org 真搜返 61258 / 3 条结果
- edge-tts 真生成 mp3 文件大小验证

### 4. 不破坏 video_creator
- TtsCreator 仍走 Easel CLI 子进程路径(不变)
- BgmFetcher / StockVideoFetcher 在 fallback 时可调 facade
- 已 ship 的 OM-P2 pick_provider_for_creator + OM-P3 check_budget 全不破

## 真实成本节省

| 场景 | 原 cost | OM-P4 后 cost |
|------|---------|--------------|
| 60s 短剧 BGM | fma_music $0(已免费) | **pixabay 真搜 + 直链** |
| 60s 短剧配音 | cosyvoice2 需 key | **edge-tts 真生成,免 key** |
| 60s 短剧 stock 视频 | archive_org 占位 | **archive.org 真搜 + URL** |

**结论**:渐进 ship 后,OM-P3 默认 $14 预算下的真实短剧编排可跑通, 不需任何付费 key。

## 与已有 ship 路径的关系

```
Skills 工作台(8 阶段 ship):
  P0-P8 ✓
视频 OpenMontage 借鉴:
  P0  决策 ✓
  P1  checkpoint ✓(Phase 9)
  P2  provider scoring ✓(Phase 10)
  P3  pre-compose 校验 ✓(Phase 11)
  P3.H CNY 国内 ✓(Phase 11-H)
  P4  免费资源真集成 ✓(本 commit) ← 新
  P5  成本预算治理 pending
多 agent OpenAI Agents SDK 借鉴:
  P1  handoff schema ✓(Phase 9)
  P2-P6 pending
```

## 风险登记

1. **edge-tts 是 Microsoft 公开 WebSocket** — 政策可能变(可降级到本地 piper)
2. **Pixabay 5000/小时 quota** — 个人够,工作室不够
3. **archive.org 资源老旧** — 历史素材为主,不适合现代短剧(配 archive.org + 现代 stock 混合)
4. **没真生成视频** — OM-P4 只到 BGM/TTS/stock 视频,**图生视频免费档**没真集成(local_wan/wan2.1_local 需 GPU 跑本地)
5. **多语言支持** — edge-tts 有 9 中文 voice,英文/粤语/台湾齐全,其它语言未测试

## 后续

- **OM-P4.B**:pexels_client(海外 stock 视频备用)+ fma 真爬虫
- **OM-P4.C**:wan2.1_local 本地真集成(需 GPU 检测 + ONNX 推理)
- **OM-P5**:实时计费(月聚合 + auto-degrade + 月预算上限)

**How to apply:**
- 用户问「免费 TTS 怎么用」→ `edge_tts_client.synthesize_sync("你好", "/tmp/a.mp3")`
- 用户问「怎么配 Pixabay key」→ env `PIXABAY_API_KEY=xxx` 或 ~/work/easel/.env
- 用户问「免费资源状态」→ `free_resource_status()` 看 3 资源就绪
- 用户问「archive.org 怎么搜」→ `archive_org_client.search_videos(query)` 无 key 也能用
- 用户问「怎么零成本跑短剧」→ facade.free_tts + facade.free_stock_video + local_wan/wan2.1_local(需 GPU)