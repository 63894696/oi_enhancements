---
name: prisIr-m366-dropdown-category
description: M3.66 dropdown category 分组设计 — LLM / tts / image / music / video 5 类清晰区分(2026-09-28)
metadata:
  type: project
---

# M3.66 dropdown category 分组设计 ship(2026-09-28)

承接 [[prisIr-phase-9-om-p1-and-ma-p1]] ~ [[prisIr-phase-12b-om-p4-pixabay-fix]] 用户「后续不加新功能,只预配置端点」决策。

## 关键拍板(用户原话)
- 「后续功能不用加了,只是新加的模型可以把端点预配置在,快速下拉模型平台选项中」
- 「就是因为这些模型和 LLM 模型不同,才要清晰区分开,不让用户以为填个视频模型的 key 就能对话了,所以要专门分一个类出来加在下面」
- 「预设端点是让用户只用选端点,再填对应 key 省事的,理论上参考 LLM 模型选择和填写 key 同样配置」

## 现状(2026-09-28 之前)
两个 dropdown 已 ship:
- `companion_asr_providers.py` — ASR 模型 dropdown(语伴用)
- `companion_llm_providers.py` — LLM 模型 dropdown(主对话用,15 平台)

**第三类「专业模型 dropdown」未存在** — 用户原话触发新建 image/video/music/tts 共 21 个 spec 加到 LLM dropdown 同文件,通过 `category` 字段分组,**不新建 dropdown 文件**(不违反「不加新功能」)。

## 设计要点

### 1. 同款 schema(对齐 LLM dropdown,用户最熟悉)
专业模型 spec 的 `fields` 跟 LLM 完全一致:
- 必有 `api_key` 字段(secret + required,需 key 的)
- 必有 `model` 字段(可改,预填 default_model)
- **不暴露 endpoint 字段**(`base_url` 锚死在 spec,跟 LLM dropdown 同)

### 2. `category` 字段 5 值
| category | dropdown 分组 | 用途 |
|----------|---------------|------|
| `"llm"` | 🧠 大语言模型 | 主对话(stream_chat 调) |
| `"tts"` | 🗣️ 语音合成 | edge_tts/elevenlabs/cosyvoice/piper |
| `"image"` | 🎨 图像生成 | openai_dalle/stability/wanx/pixabay |
| `"music"` | 🎵 音乐生成 | suno/udio/archive_org_audio |
| `"video"` | 🎬 视频生成 | kling/runway/pika/luma/veo3 + 5 国内 |

### 3. 双 store 防污染(关键 bug 修复)
- LLM (category=llm) → 写 `keys.db`(PrisirKeyStore 主路由表)
- 专业模型 (category≠llm) → 写 `~/.prisIrai/media_keys.json/_prisir_key` 子键

**为何要子键**:`~/.prisIrai/media_keys.json` 早被 `siliconflow/dashscope/openai/whisper` 其它工具占用顶层字段(如 `providers.siliconflow`),直接覆写会**毁掉它们的配置**。第一次实现用 `_load_media_keys()` → 直接整个 dict 写回,会被 bug。修复后用 `_prisir_key` 子 dict 隔离 + 原子写(tmp + replace)。

### 4. 后端新增 `get_media_key(platform_id)` / `list_media_providers()`
供 video_creator / image_gen / tts_creator 内部调用:
- 不读 keys.db(防 LLM 路由误读)
- 不污染已有 media_keys.json 顶层结构
- 返回完整 cfg `{api_key, base_url, model, category, kind, updated}`

## ship 后 dropdown 总览

| category | 平台数 | list |
|----------|--------|------|
| 🧠 llm | 16 | anthropic / openai / gemini / grok / llama / openrouter / groq / nemotron / deepseek / yunbailian / kimi / zhipu / minimax / agnes / ollama / llama-server |
| 🎬 video | 10 | kling / runway / pika / luma / veo3 + 5 国内(kling_cn/jimeng/vidu/cogvideox/hailuo) |
| 🎨 image | 4 | openai_dalle / stability / dashscope_image / pixabay |
| 🗣️ tts | 4 | edge_tts / elevenlabs_tts / cosyvoice2 / piper_tts |
| 🎵 music | 3 | suno / udio / archive_org_audio |
| **总计** | **37** | (原 15 + 新 22) |

## 关键文件

| 文件 | 改动 |
|------|------|
| `companion/companion_llm_providers.py` | 加 `category` 字段 + 22 个新 spec + 双 store + 原子写 + 隔离子键 |

## 测试覆盖
- ✅ Phase 9 OM-P1+MA-P1:14/14 绿
- ✅ Phase 10 OM-P2:13/13 绿
- ✅ Phase 11+11-H OM-P3 国内 CNY:22/22 绿
- ✅ Phase 12 OM-P4 免费资源:18/18 绿
- ✅ 临时 HOME E2E 测试:
  - LLM(deepseek)→ 写 keys.db,ks.saved 有 deepseek
  - 视频(kling_cn)→ 写 media_keys.json/_prisir_key,ks.saved 未污染
  - 已有 siliconflow/dashscope/openai/whisper 顶层字段**仍存在**
  - get_media_key 正确读取子键

## 风险登记

1. **前端未改造** — 当前 `list_llm_providers` 已带 `category` 字段,前端若按老逻辑展示会**全部列在一个 dropdown**,用户会误以为可对话。需前端按 category 分组(🧠🗣️🎨🎵🎬 五组),或加 visual 标记
2. **get_media_key 后端调用点未接通** — video_creator / image_gen / tts_creator 仍走老的 video_provider_scoring;新 dropdown 写入的 key 暂不被自动读到,需后续在 OM-P2 scoring 接入 media_keys.json
3. **Pixabay 在 LLM 和 OM 都有** — dropdown 1 个 + OM-P4 video_provider_scoring 1 个,platform_id 都叫 `pixabay`,keys 不同 path 用不同来源
4. **专业模型 category 字段是字符串,不是 enum** — 前端需自己字符串比较;未来若加 `category: str = "llm"` enum 化,需提前兼容

## 后续(若用户要求)

- OM-P5:把 video_creator / image_gen / tts_creator 的 key 读取**优先**从 `get_media_key()` 拿(用户 dropdown 配的优先),降级到 scoring registry 默认
- 前端 dropdown 按 `category` 字段分 5 组,加视觉分组标签(「🧠 LLM」「🎬 专业模型 - 视频」)

**How to apply:**
- 用户问「dropdown 上怎么区分 LLM 和视频模型」→ `category` 字段:`llm` vs `video/image/music/tts`
- 用户问「专业模型 key 存哪」→ `~/.prisIrai/media_keys.json` 的 `_prisir_key` 子键(不污染顶层 siliconflow 等)
- 用户问「为什么 LLM dropdown 里突然多了 20+ 选项」→ 用户拍板「同款 dropdown 不同 category」;前端按 category 分组避免视觉混乱
- 设计任何 dropdown 时:**同款 schema + 清晰 category 分组 + 隔离 store 防污染**