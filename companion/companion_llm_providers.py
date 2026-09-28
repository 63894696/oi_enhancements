# -*- coding: utf-8 -*-
# companion_llm_providers.py — LLM 平台 spec 注册表(M3.18 2026-09-16)
#
# 目的:把 companion_asr_providers 的「下拉选厂商+填 key」模式搬到 LLM 配置。
# 之前 PrisirAI LLM 平台只能手动 SQLite 写 keys.db ——
# 用户既不知道该填 base_url 也不知道 model 名。
# 现在每个平台预填 base_url / 默认 model / 鉴权字段 / 协议(OpenAI / Anthropic),
# UI GET /api/llm/providers 拿列表,POST 时按 spec 解析 → 写 keys.db。
#
# 设计:
#   - 平台 platform_id 与 keys.db platform_keys.platform 一致(已是 router 用)
#   - 默认 model/base_url 仅作 placeholder — 用户可在 UI 覆盖(每个平台可能多个模型)
#   - kind: "openai" / "anthropic" / "ollama" — 决定 stream_chat 走 _stream_openai_compat 还是 _stream_anthropic
#   - 不暴露"端点"成 UI 字段(同 ASR 设计)— 改要改源码(锚死避免用户配错协议)
#   - 不动 router / PrisirKeyStore — 只新增「平台 catalog」概念
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("prisiragent-companion.llm_providers")


@dataclass
class LlmProviderField:
    key: str
    label: str
    secret: bool = False
    required: bool = False
    default: Any = ""
    placeholder: str = ""


@dataclass
class LlmProviderSpec:
    platform_id: str        # 与 keys.db platform_keys.platform 一致
    display: str            # 中文显示名
    kind: str               # "openai" / "anthropic" / "ollama" / "video" / "image" / "tts" / "music"
    base_url: str           # 默认 base_url(用户在 UI 可改)
    default_model: str      # 默认 model(用户在 UI 可改)
    fields: list[LlmProviderField] = field(default_factory=list)
    note: str = ""          # 用途说明
    local: bool = False     # M3.22.3 — 本地(隐私)分类;True → dropdown 进「💻 本地」组
    # M3.66(2026-09-28)— 加 category 字段,清晰区分 LLM vs 专业模型(防误用)
    # category="llm" → 写 keys.db,stream_chat 可调
    # category ∈ {"tts","image","music","video"} → 仅展示端点信息,不写 keys.db,
    #   stream_chat 不调,前端独立分组(🎬 专业模型),不与 LLM 混淆
    category: str = "llm"


# ============================================================
# 平台注册表(2026-09)
# M3.22.3 排序按 OpenRouter token 用量榜(2026-Q2~Q3):
#   国际用量:Anthropic > OpenAI > Google > xAI > Meta(参考 OpenRouter weekly rankings)
#   国内用量:DeepSeek > Qwen > Kimi > GLM > 字节豆包 > 阶跃星辰 > MiniMax
#     (DeepSeek V4 Flash 持续 #1,Qwen3.5/Kimi K3/GLM 5.2 进 top 10)
#   本地:ollama / llama-server
# default_model 严格按 https://openrouter.ai/rankings#top-models 页 Sep 16 2026 抓到的
# top-10 原文写入 — 该页无 token 数值,只列 top 型号。注:页面 top-N 不一定是厂商当前
# 真实 API 名(可能是 preview / 限量 / 实验室),用户在 UI 改即可。端点锚死,只填 key。
# ============================================================

LLM_PROVIDERS: dict[str, LlmProviderSpec] = {}


def _register(spec: LlmProviderSpec) -> None:
    LLM_PROVIDERS[spec.platform_id] = spec


# ========== 国际云端 LLM(按 OpenRouter 用量排)==========
# default_model 是该型号在 OpenRouter 上实际用得多的型号(不是厂商文档,文档常滞后)。

# 1. Anthropic Claude — OpenRouter #top-models #1 (claude-fable-5.1, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="anthropic",
    display="Anthropic Claude(原生 messages API)",
    kind="anthropic",
    base_url="https://api.anthropic.com/v1",
    default_model="claude-fable-5.1",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-ant-..."),
        LlmProviderField("model", "模型", default="claude-fable-5.1",
                          placeholder="claude-fable-5.1 / claude-fable-5 / claude-opus-5 / claude-sonnet-4.5"),
    ],
    note="OpenRouter #top-models #1 · 原生 Anthropic 协议(非 OpenAI 兼容)· 模型在 UI 改",
))

# 2. OpenAI GPT — OpenRouter #top-models #3 (gpt-6-astra, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="openai",
    display="OpenAI 官方",
    kind="openai",
    base_url="https://api.openai.com/v1",
    default_model="gpt-6-astra",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-..."),
        LlmProviderField("model", "模型", default="gpt-6-astra",
                          placeholder="gpt-6-astra / gpt-5.6-sol / gpt-5 / gpt-5-codex / gpt-4o"),
    ],
    note="OpenRouter #top-models #3 · OpenAI 兼容协议 · 模型在 UI 改",
))

# 3. Google Gemini — OpenRouter 用量 top
_register(LlmProviderSpec(
    platform_id="gemini",
    display="Google Gemini(OpenAI 兼容)",
    kind="openai",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    default_model="gemini-2.5-pro",
    fields=[
        LlmProviderField("api_key", "Google AI Studio API Key", secret=True, required=True,
                          placeholder="AIza-..."),
        LlmProviderField("model", "模型", default="gemini-2.5-pro",
                          placeholder="gemini-2.5-pro / gemini-2.5-flash / gemini-2.5-flash-lite"),
    ],
    note="2M context 多模态 · OpenAI 兼容(/v1beta/openai 后缀必带) · 模型在 UI 改",
))

# 4. xAI Grok — OpenRouter #top-models #9 (grok-4.6, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="grok",
    display="xAI Grok(OpenAI 兼容)",
    kind="openai",
    base_url="https://api.x.ai/v1",
    default_model="grok-4.6",
    fields=[
        LlmProviderField("api_key", "xAI API Key", secret=True, required=True,
                          placeholder="xai-..."),
        LlmProviderField("model", "模型", default="grok-4.6",
                          placeholder="grok-4.6 / grok-4 / grok-4-fast / grok-3-mini"),
    ],
    note="OpenRouter #top-models #9 · 实时数据 · 价格低 · OpenAI 兼容 · 模型在 UI 改",
))

# 5. Meta Llama(云端托管)
_register(LlmProviderSpec(
    platform_id="llama",
    display="Meta Llama 云端(OpenAI 兼容)",
    kind="openai",
    base_url="https://api.meta.ai/v1",
    default_model="llama-3.3-70b-instruct",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="..."),
        LlmProviderField("model", "模型", default="llama-3.3-70b-instruct",
                          placeholder="llama-3.3-70b-instruct / llama-4-scout"),
    ],
    note="开源权重 · OpenAI 兼容 · Meta 官方云端 · 模型在 UI 改",
))

# 6. OpenRouter — 多模型聚合
_register(LlmProviderSpec(
    platform_id="openrouter",
    display="OpenRouter(多模型聚合 OpenAI 兼容)",
    kind="openai",
    base_url="https://openrouter.ai/api/v1",
    default_model="openrouter/free",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-or-..."),
        LlmProviderField("model", "模型", default="openrouter/free",
                          placeholder="openrouter/free / anthropic/claude-sonnet-4.5 / meta/llama-3.3-70b-instruct"),
    ],
    note="一 key 通吃多家 · 含 free 模型 · 模型在 UI 改",
))

# 7. Groq — 超快推理(代理 Llama/Mixtral)
_register(LlmProviderSpec(
    platform_id="groq",
    display="Groq(超快推理 OpenAI 兼容)",
    kind="openai",
    base_url="https://api.groq.com/openai/v1",
    default_model="llama-3.3-70b-versatile",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="gsk_..."),
        LlmProviderField("model", "模型", default="llama-3.3-70b-versatile",
                          placeholder="llama-3.3-70b-versatile / mixtral-8x7b"),
    ],
    note="超快推理速度 · Llama/Mixtral · OpenAI 兼容 · 模型在 UI 改",
))

# 8. NVIDIA Nemotron — OpenRouter Sep 15 2026 top-10 #7 (3.63T tokens, free tier)
_register(LlmProviderSpec(
    platform_id="nemotron",
    display="NVIDIA Nemotron(OpenAI 兼容)",
    kind="openai",
    base_url="https://integrate.api.nvidia.com/v1",
    default_model="nemotron-3-ultra",
    fields=[
        LlmProviderField("api_key", "NVIDIA API Key", secret=True, required=True,
                          placeholder="nvapi-..."),
        LlmProviderField("model", "模型", default="nemotron-3-ultra",
                          placeholder="nemotron-3-ultra / nemotron-3-super / llama-3.3-nemotron-49b"),
    ],
    note="OpenRouter Sep 15 #7 · 含 free tier · OpenAI 兼容 · 模型在 UI 改",
))


# ========== 国内云端 LLM(按 OpenRouter 用量排)==========

# 1. DeepSeek — OpenRouter Sep 15 2026 全榜 #4 (deepseek-v4-flash-0731, 12.3T tokens)
_register(LlmProviderSpec(
    platform_id="deepseek",
    display="DeepSeek(官方 OpenAI 兼容)",
    kind="openai",
    base_url="https://api.deepseek.com/v1",
    default_model="deepseek-v4-flash-0731",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-..."),
        LlmProviderField("model", "模型", default="deepseek-v4-flash-0731",
                          placeholder="deepseek-v4-flash-0731 / deepseek-v4-flash-0423 / deepseek-v4-pro / deepseek-chat"),
    ],
    note="OpenRouter Sep 15 全榜 #4 (12.3T tokens) · V4 Flash 系列 · 代码/推理首选 · OpenAI 兼容 · 模型在 UI 改",
))

# 2. 阿里云百炼 Qwen — OpenRouter #top-models #2 (qwen3.8-max, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="yunbailian",
    display="阿里云百炼(DashScope OpenAI 兼容)",
    kind="openai",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    default_model="qwen3.8-max",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          default=os.environ.get("BAILIAN_API_KEY", ""),
                          placeholder="sk-... (同百炼 key)"),
        LlmProviderField("model", "模型", default="qwen3.8-max",
                          placeholder="qwen3.8-max / qwen3.8-max-0902 / qwen-max / qwen-plus / qwen-coder-plus"),
    ],
    note="OpenRouter #top-models #2 · Qwen 系列 · OpenAI 兼容 · 模型在 UI 改",
))

# 3. 月之暗面 Kimi — OpenRouter #top-models #10 (kimi-k3, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="kimi",
    display="月之暗面 Kimi(Moonshot OpenAI 兼容)",
    kind="openai",
    base_url="https://api.moonshot.cn/v1",
    default_model="kimi-k3",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-..."),
        LlmProviderField("model", "模型", default="kimi-k3",
                          placeholder="kimi-k3 / moonshot-v1-128k / moonshot-v1-32k"),
    ],
    note="OpenRouter #top-models #10 · 长上下文 · 模型在 UI 改",
))

# 4. 智谱 GLM — OpenRouter #top-models #8 (glm-5.3, Sep 16 2026)
_register(LlmProviderSpec(
    platform_id="zhipu",
    display="智谱 GLM(OpenAI 兼容)",
    kind="openai",
    base_url="https://open.bigmodel.cn/api/paas/v4",
    default_model="glm-5.3",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="..."),
        LlmProviderField("model", "模型", default="glm-5.3",
                          placeholder="glm-5.3 / glm-4.5 / glm-z1-air"),
    ],
    note="OpenRouter #top-models #8 · GLM 系列 · 模型在 UI 改",
))

# 5. MiniMax
_register(LlmProviderSpec(
    platform_id="minimax",
    display="MiniMax(OpenAI 兼容)",
    kind="openai",
    base_url="https://api.MiniMax.chat/v1",
    default_model="MiniMax-M3",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="..."),
        LlmProviderField("model", "模型", default="MiniMax-M3",
                          placeholder="MiniMax-M3 / MiniMax-Text-01"),
    ],
    note="国产 · MiniMax 全系 · abab 系列",
))

# 6. Agnes(用户实评不错)— Agnes AI (Sapiens AI, Singapore)
_register(LlmProviderSpec(
    platform_id="agnes",
    display="Agnes AI(新加坡 3.0 Flash OpenAI 兼容)",
    kind="openai",
    base_url="https://apihub.agnes-ai.com/v1",
    default_model="agnes-3-0-flash",
    fields=[
        LlmProviderField("api_key", "Agnes API Key", secret=True, required=True,
                          placeholder="..."),
        LlmProviderField("model", "模型", default="agnes-3-0-flash",
                          placeholder="agnes-3-0-flash / agnes-2-5-pro / agnes-2-5-flash"),
    ],
    note="新加坡 Sapiens AI · 262K-512K context · 用户实评不错 · OpenAI 兼容 · 模型在 UI 改",
))

# ========== 本地优先(隐私)==========
_register(LlmProviderSpec(
    platform_id="ollama",
    display="本地 Ollama(隐私优先)",
    kind="ollama",
    base_url="http://127.0.0.1:11434/v1",
    default_model="local",
    fields=[
        LlmProviderField("endpoint", "Ollama 服务地址",
                          default="http://127.0.0.1:11434/v1",
                          placeholder="http://127.0.0.1:11434/v1"),
        LlmProviderField("model", "模型标签", default="local",
                          placeholder="local / qwen3:8b / llama3.3:8b / gemma3:27b(以 ollama pull 的为准)"),
    ],
    note="本地 · 完全离线 · 隐私优先 · key 字段可留空(ollama 默认不要鉴权) · default=local(无预设,以 ollama 实际 pull 的为准)",
    local=True,
))

_register(LlmProviderSpec(
    platform_id="llama-server",
    display="本地 llama-server(llama.cpp / GGUF)",
    kind="openai",
    base_url="http://127.0.0.1:8080/v1",
    default_model="local",
    fields=[
        LlmProviderField("endpoint", "llama-server 地址",
                          default="http://127.0.0.1:8080/v1",
                          placeholder="http://127.0.0.1:8080/v1"),
        LlmProviderField("model", "模型标识", default="local",
                          placeholder="local / 任意字符串(llama-server 不校验)"),
    ],
    note="本地 · llama.cpp 起的 server · OpenAI 兼容",
    local=True,
))


# ============================================================
# M3.66(2026-09-28)— 专业模型 dropdown(image / video / music / tts)
#   设计原则(用户原话):
#     「预设端点是让用户只用选端点,再填对应 key 省事的,
#       理论上参考 LLM 模型选择和填写 key 同样配置」
#   - 跟 LLM dropdown 同款 schema:fields 只有 api_key + model(无 endpoint 字段)
#   - base_url / default_model 预填但不暴露给 UI
#   - category=非 llm → upsert_key_from_form 不写 keys.db,
#     改写 ~/.prisIrai/media_keys.json(独立 store,防污染 LLM 路由表)
# ============================================================


# ============================================================
# 🎨 图像生成(image) — 4 个
# ============================================================

_register(LlmProviderSpec(
    platform_id="openai_dalle",
    display="OpenAI DALL·E 3(图像)",
    kind="image",
    category="image",
    base_url="https://api.openai.com/v1",
    default_model="dall-e-3",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-..."),
        LlmProviderField("model", "模型", default="dall-e-3",
                          placeholder="dall-e-3 / dall-e-2"),
    ],
    note="OpenAI DALL·E 3 · 顶配文生图",
))

_register(LlmProviderSpec(
    platform_id="stability",
    display="Stability AI(SDXL/SD3 图像)",
    kind="image",
    category="image",
    base_url="https://api.stability.ai/v2beta",
    default_model="stable-image-core",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="sk-..."),
        LlmProviderField("model", "模型", default="stable-image-core",
                          placeholder="stable-image-core / stable-image-ultra / sdxl-1-0"),
    ],
    note="Stability AI · SDXL/SD3 · 顶配开源",
))

_register(LlmProviderSpec(
    platform_id="dashscope_image",
    display="阿里云百炼·通义万相(图像)",
    kind="image",
    category="image",
    base_url="https://dashscope.aliyuncs.com/api/v1",
    default_model="wanx-v1",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          default=os.environ.get("BAILIAN_API_KEY", ""),
                          placeholder="sk-... (同百炼 key)"),
        LlmProviderField("model", "模型", default="wanx-v1",
                          placeholder="wanx-v1 / wanx2.1-t2i-turbo / qwen-image"),
    ],
    note="阿里云百炼 · 通义万相 · 中文 prompt 友好",
))

_register(LlmProviderSpec(
    platform_id="pixabay",
    display="Pixabay(stock 图片/视频)",
    kind="image",
    category="image",
    base_url="https://pixabay.com/api/",
    default_model="image",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          default=os.environ.get("PIXABAY_API_KEY", ""),
                          placeholder="Pixabay 后台 → /api/key/ 获取"),
    ],
    note="Pixabay stock 图片/视频 · 100 请求/60 秒 · 网页有 Music 但 API 无音频端点",
))


# ============================================================
# 🎬 视频生成(video) — 10 个
# ============================================================

_register(LlmProviderSpec(
    platform_id="kling",
    display="Kling 可灵(快手 国际版)",
    kind="video",
    category="video",
    base_url="https://api.klingai.com/v1",
    default_model="kling-v1-5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="kling 控制台 → API Key"),
        LlmProviderField("model", "模型", default="kling-v1-5",
                          placeholder="kling-v1-5 / kling-v1 / kling-v1-6"),
    ],
    note="Kling 国际版(海外账号)",
))

_register(LlmProviderSpec(
    platform_id="runway",
    display="Runway Gen-3",
    kind="video",
    category="video",
    base_url="https://api.dev.runwayml.com/v1",
    default_model="gen3a_turbo",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="Runway 控制台"),
        LlmProviderField("model", "模型", default="gen3a_turbo",
                          placeholder="gen3a_turbo / gen3a / gen4_turbo"),
    ],
    note="Runway 官方 · 商用质量高",
))

_register(LlmProviderSpec(
    platform_id="pika",
    display="Pika Labs",
    kind="video",
    category="video",
    base_url="https://api.pika.art/v1",
    default_model="pika-1.5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="Pika 控制台"),
        LlmProviderField("model", "模型", default="pika-1.5",
                          placeholder="pika-1.5 / pika-1.0"),
    ],
    note="Pika Labs 官方",
))

_register(LlmProviderSpec(
    platform_id="luma",
    display="Luma Dream Machine",
    kind="video",
    category="video",
    base_url="https://api.lumalabs.ai/v1",
    default_model="ray-2",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="Luma 控制台"),
        LlmProviderField("model", "模型", default="ray-2",
                          placeholder="ray-2 / ray-flash-2 / ray-1-6"),
    ],
    note="Luma 官方",
))

_register(LlmProviderSpec(
    platform_id="veo3",
    display="Veo 3(Google)",
    kind="video",
    category="video",
    base_url="https://generativelanguage.googleapis.com/v1beta",
    default_model="veo-3.0-generate-preview",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="AIza-..."),
        LlmProviderField("model", "模型", default="veo-3.0-generate-preview",
                          placeholder="veo-3.0-generate-preview / veo-2.0-generate-001"),
    ],
    note="Google AI Studio · 8 秒顶配",
))

# 国内 5 大视频生成平台(M3.66 2026-09-28 国内 CNY 决策后 ship)
_register(LlmProviderSpec(
    platform_id="kling_cn",
    display="可灵 国内版(快手)",
    kind="video",
    category="video",
    base_url="https://api.klingai.com/v1",
    default_model="kling-v1-5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="快手控制台 → 国内版 key"),
        LlmProviderField("model", "模型", default="kling-v1-5",
                          placeholder="kling-v1-5 / kling-v1-6"),
    ],
    note="可灵国内版 · 中文场景最佳",
))

_register(LlmProviderSpec(
    platform_id="jimeng",
    display="即梦 Dreamina(火山)",
    kind="video",
    category="video",
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    default_model="jimeng-1.5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="火山控制台"),
        LlmProviderField("model", "模型", default="jimeng-1.5",
                          placeholder="jimeng-1.5 / jimeng-1.0"),
    ],
    note="即梦 Dreamina · 火山方舟 · 性价比高",
))

_register(LlmProviderSpec(
    platform_id="vidu",
    display="Vidu(生数科技)",
    kind="video",
    category="video",
    base_url="https://api.vidu.studio/v1",
    default_model="vidu-1.5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="生数科技控制台"),
        LlmProviderField("model", "模型", default="vidu-1.5",
                          placeholder="vidu-1.5 / vidu-1.0"),
    ],
    note="Vidu · 角色一致性好",
))

_register(LlmProviderSpec(
    platform_id="cogvideox",
    display="智谱 CogVideoX",
    kind="video",
    category="video",
    base_url="https://open.bigmodel.cn/api/paas/v4",
    default_model="cogvideox-2",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="智谱开放平台"),
        LlmProviderField("model", "模型", default="cogvideox-2",
                          placeholder="cogvideox-2 / cogvideox"),
    ],
    note="智谱 CogVideoX · 开源可本地部署",
))

_register(LlmProviderSpec(
    platform_id="hailuo",
    display="海螺 AI(MiniMax Video)",
    kind="video",
    category="video",
    base_url="https://api.MiniMax.chat/v1",
    default_model="MiniMax-video-01",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="MiniMax 控制台"),
        LlmProviderField("model", "模型", default="MiniMax-video-01",
                          placeholder="MiniMax-video-01 / hailuo-02"),
    ],
    note="海螺 AI · 中文指令理解好",
))


# ============================================================
# 🎵 音乐生成(music) — 3 个
#   Pixabay 无音频 API;Music 只能手动下或 archive.org 公开 mp3
# ============================================================

_register(LlmProviderSpec(
    platform_id="suno",
    display="Suno AI(文生歌/BGM)",
    kind="music",
    category="music",
    base_url="https://api.suno.ai/v1",
    default_model="chirp-v4",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="Suno 控制台"),
        LlmProviderField("model", "模型", default="chirp-v4",
                          placeholder="chirp-v4 / chirp-v3-5"),
    ],
    note="Suno 官方 · 商用质量高",
))

_register(LlmProviderSpec(
    platform_id="udio",
    display="Udio AI",
    kind="music",
    category="music",
    base_url="https://api.udio.com/v1",
    default_model="udio-1.5",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="Udio 控制台"),
        LlmProviderField("model", "模型", default="udio-1.5",
                          placeholder="udio-1.5 / udio-1.0"),
    ],
    note="Udio 官方",
))

_register(LlmProviderSpec(
    platform_id="archive_org_audio",
    display="Archive.org 公开音频(免 key)",
    kind="music",
    category="music",
    base_url="https://archive.org/advancedsearch.php",
    default_model="audio",
    fields=[
        LlmProviderField("note_field", "(免 key)",
                          placeholder="无需 API Key · 历史素材 mp3"),
    ],
    note="Archive.org 公开 mp3 · OM-P4 真集成 · 历史素材为主",
))


# ============================================================
# 🗣️ 语音合成(tts) — 4 个
# ============================================================

_register(LlmProviderSpec(
    platform_id="edge_tts",
    display="Edge TTS(微软公开 · 免 key)",
    kind="tts",
    category="tts",
    base_url="wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1",
    default_model="zh-CN-XiaoxiaoNeural",
    fields=[
        LlmProviderField("voice", "语音", default="zh-CN-XiaoxiaoNeural",
                          placeholder="zh-CN-XiaoxiaoNeural / zh-CN-YunxiNeural / en-US-JennyNeural"),
    ],
    note="Edge TTS · 9 中文 voice · 免 key · WebSocket · OM-P4 真集成",
))

_register(LlmProviderSpec(
    platform_id="elevenlabs_tts",
    display="ElevenLabs TTS(商用高质量)",
    kind="tts",
    category="tts",
    base_url="https://api.elevenlabs.io/v1",
    default_model="eleven_multilingual_v2",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          placeholder="ElevenLabs 控制台"),
        LlmProviderField("model", "模型", default="eleven_multilingual_v2",
                          placeholder="eleven_multilingual_v2 / eleven_turbo_v2_5"),
    ],
    note="ElevenLabs 官方 · 商用质量极高",
))

_register(LlmProviderSpec(
    platform_id="cosyvoice2",
    display="CosyVoice2(阿里通义 · 中文)",
    kind="tts",
    category="tts",
    base_url="https://dashscope.aliyuncs.com/api/v1",
    default_model="cosyvoice-300m-sft",
    fields=[
        LlmProviderField("api_key", "API Key", secret=True, required=True,
                          default=os.environ.get("BAILIAN_API_KEY", ""),
                          placeholder="sk-... (同百炼 key)"),
        LlmProviderField("model", "模型", default="cosyvoice-300m-sft",
                          placeholder="cosyvoice-300m-sft / cosyvoice-300m"),
    ],
    note="阿里通义 · 中文场景最佳",
))

_register(LlmProviderSpec(
    platform_id="piper_tts",
    display="Piper TTS(本地推理 · 免 key)",
    kind="tts",
    category="tts",
    base_url="http://127.0.0.1:59125",
    default_model="zh_CN-huayan-medium",
    fields=[
        LlmProviderField("voice", "模型", default="zh_CN-huayan-medium",
                          placeholder="zh_CN-huayan-medium / en_US-lessac-medium"),
    ],
    note="Piper TTS · 本地推理 · 中文支持",
    local=True,
))


# ============================================================
# 公开 API
# ============================================================
def list_llm_providers() -> list[dict]:
    """给前端:返回每个平台的字段定义(secret 字段不返 key 明文)。
    M3.22.3 — 加 local 字段;前端 dropdown 按 local 分组(☁ 云端 / 💻 本地)。
    M3.66(2026-09-28)— 加 category 字段,前端按 category 分组:
      "llm"   → 🧠 大语言模型(默认)
      "tts"   → 🗣️ 语音合成
      "image" → 🎨 图像生成
      "music" → 🎵 音乐生成
      "video" → 🎬 视频生成
      专业模型不写 keys.db,前端独立分组避免与 LLM 混淆。
    """
    out = []
    for pid, spec in LLM_PROVIDERS.items():
        out.append({
            "platform_id": spec.platform_id,
            "display": spec.display,
            "kind": spec.kind,
            "category": spec.category,
            "base_url": spec.base_url,
            "default_model": spec.default_model,
            "note": spec.note,
            "local": bool(spec.local),
            "fields": [
                {"key": f.key, "label": f.label, "secret": f.secret,
                 "required": f.required, "default": f.default,
                 "placeholder": f.placeholder}
                for f in spec.fields
            ],
        })
    return out


def get_spec(platform_id: str) -> Optional[LlmProviderSpec]:
    return LLM_PROVIDERS.get(platform_id)


def upsert_key_from_form(keystore, platform_id: str,
                          form_data: dict) -> dict:
    """从 UI POST 拿到的 form_data(已 unmask)→ 写 keys.db 或专业模型 store。

    Args:
        keystore: PrisirKeyStore 实例(已在 companion_llm.py 引用)
        platform_id: 用户选的 platform_id
        form_data: {api_key, model, endpoint?, ...}
    Returns:
        写入后的 cfg dict(给前端确认)

    M3.66(2026-09-28):category ∈ {tts,image,music,video} 的平台**不写 keys.db**,
      改写 ~/.prisIrai/media_keys.json(单独存专业模型 key),避免污染 LLM 路由表
      并防止用户误以为「填视频模型 key 就能对话」。
    """
    spec = get_spec(platform_id)
    if spec is None:
        raise RuntimeError(f"未知 LLM 平台:{platform_id}")
    api_key = (form_data.get("api_key") or "").strip()
    model = (form_data.get("model") or spec.default_model).strip()

    # 专业模型(tts/image/music/video)→ 不写 keys.db,改写 media_keys.json
    if spec.category != "llm":
        return _upsert_media_key(platform_id, spec, api_key, model)

    # LLM 平台 → 走老路径,写 keys.db
    # 端点策略:ollama 用 endpoint,其它用 spec.base_url(用户可改但 UI 不暴露)
    if spec.kind == "ollama":
        base_url = (form_data.get("endpoint") or spec.base_url).strip()
    elif platform_id == "llama-server":
        base_url = (form_data.get("endpoint") or spec.base_url).strip()
    else:
        base_url = spec.base_url  # 锚死,UI 不暴露
    meta = {"proto": "anthropic"} if spec.kind == "anthropic" else {}
    keystore.set_key(platform_id, api_key, base_url, model, meta=meta)
    return {"platform": platform_id, "base_url": base_url,
            "model": model, "api_key_len": len(api_key),
            "category": "llm"}


# ============================================================
# 专业模型 key 持久化(2026-09-28 ship)
# ============================================================
# 与 keys.db 完全隔离,防止视频模型 key 被 LLM 路由误用。
# 存到 ~/.prisIrai/media_keys.json,plain JSON,权限位本地读写。

_MEDIA_KEYS_PATH = Path.home() / ".prisIrai" / "media_keys.json"


def _load_media_keys() -> dict:
    """读 ~/.prisIrai/media_keys.json(全文件,可能含其它工具的字段)。

    注意:此文件已被 siliconflow/dashscope/openai/whisper 等其它工具使用,
    我们的数据放 _PRISIR_KEY 下,避免污染它们的顶层字段。
    """
    try:
        if _MEDIA_KEYS_PATH.is_file():
            return json.loads(_MEDIA_KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_media_keys(data: dict) -> None:
    """原子写:tmp 文件 + replace,避免崩溃中途损坏文件。"""
    try:
        _MEDIA_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MEDIA_KEYS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(_MEDIA_KEYS_PATH)
    except Exception as e:
        log.warning("media_keys 持久化失败: %s", e)


def _upsert_media_key(platform_id: str, spec, api_key: str, model: str) -> dict:
    """专业模型 key 写 ~/.prisIrai/media_keys.json 下 _prisir_key 子键(不写 keys.db)。

    ⚠ M3.66-fix(2026-09-28):此文件已被 siliconflow/dashscope 等其它工具占用顶层字段,
    我们用 _prisir_key 子 dict 隔离,避免互相覆盖。
    """
    data = _load_media_keys()
    if "_prisir_key" not in data or not isinstance(data.get("_prisir_key"), dict):
        data["_prisir_key"] = {}
    data["_prisir_key"][platform_id] = {
        "api_key": api_key,
        "base_url": spec.base_url,
        "model": model,
        "category": spec.category,
        "kind": spec.kind,
        "updated": int(time.time()),
    }
    _save_media_keys(data)
    return {"platform": platform_id, "base_url": spec.base_url,
            "model": model, "api_key_len": len(api_key),
            "category": spec.category, "store": "media_keys.json/_prisir_key"}


def get_media_key(platform_id: str) -> Optional[dict]:
    """读专业模型 key(给 video_creator / image_gen / tts_creator 内部调用)。"""
    return _load_media_keys().get("_prisir_key", {}).get(platform_id)


def list_media_providers() -> list[dict]:
    """列所有已配 key 的专业模型(供 video_creator 等模块读用)。"""
    sub = _load_media_keys().get("_prisir_key", {})
    return [{"platform_id": k, **v} for k, v in sub.items()]
