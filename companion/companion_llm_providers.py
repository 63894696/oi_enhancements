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

import os
from dataclasses import dataclass, field
from typing import Any, Optional


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
    kind: str               # "openai" / "anthropic" / "ollama"
    base_url: str           # 默认 base_url(用户在 UI 可改)
    default_model: str      # 默认 model(用户在 UI 可改)
    fields: list[LlmProviderField] = field(default_factory=list)
    note: str = ""          # 用途说明
    local: bool = False     # M3.22.3 — 本地(隐私)分类;True → dropdown 进「💻 本地」组


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
# 公开 API
# ============================================================
def list_llm_providers() -> list[dict]:
    """给前端:返回每个平台的字段定义(secret 字段不返 key 明文)。
    M3.22.3 — 加 local 字段;前端 dropdown 按 local 分组(☁ 云端 / 💻 本地)。"""
    out = []
    for pid, spec in LLM_PROVIDERS.items():
        out.append({
            "platform_id": spec.platform_id,
            "display": spec.display,
            "kind": spec.kind,
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
    """从 UI POST 拿到的 form_data(已 unmask)→ 写 keys.db。

    Args:
        keystore: PrisirKeyStore 实例(已在 companion_llm.py 引用)
        platform_id: 用户选的 platform_id
        form_data: {api_key, model, endpoint?, ...}
    Returns:
        写入后的 cfg dict(给前端确认)
    """
    spec = get_spec(platform_id)
    if spec is None:
        raise RuntimeError(f"未知 LLM 平台:{platform_id}")
    api_key = (form_data.get("api_key") or "").strip()
    model = (form_data.get("model") or spec.default_model).strip()
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
            "model": model, "api_key_len": len(api_key)}
