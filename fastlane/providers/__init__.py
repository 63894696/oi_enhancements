"""FastLane 云 provider 实现(ADR-002 F-1/F-2/F-3 落地,2026-07-03)

设计原则:
- 每个 provider 都是 fastlane.adapters.CloudAdapter 子类 → 直接进 CloudRouter
  降级链(F-5),失败自动切下一个。
- 全部走 HTTPS + TLS1.3(F-6:构造期 enforce_https;httpx verify 用
  make_ssl_context,禁 verify=False)。
- API key 从环境变量读(用户注册表 Environment 已有 BAILIAN/ARK/SILICONFLOW
  等 key);缺 key 的 provider 构造时抛 ProviderNotConfigured,工厂跳过它,
  不进降级链。
- 本地兜底(SenseVoice/SAPI/Ollama)永远在链尾(ADR-002 §9.3:纯云端是
  false choice)。

模块:
- asr_cloud   F-1:Qwen(DashScope)ASR / 豆包 ASR / 本地 SenseVoice 兜底
- llm_cloud   F-2:OpenAI 兼容协议 ×(Qwen/DeepSeek/…)/ 本地 Ollama 兜底
- tts_cloud   F-3:Edge TTS / CosyVoice / 本地 SAPI 兜底
- factory     按环境变量装配三条降级链
"""
from .factory import (  # noqa: F401
    LazyCloudRouter,
    ProviderNotConfigured,
    build_asr_chain,
    build_asr_chain_lazy,
    build_llm_chain,
    build_llm_chain_lazy,
    build_tts_chain,
    build_tts_chain_lazy,
    provider_status,
)
