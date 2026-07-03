"""FastLane 降级链工厂(F-5)

按「环境里有什么 key」装配三条 CloudRouter 链,顺序遵循 ADR-002:
    ASR:豆包 → Qwen → SiliconFlow → 本地 SenseVoice
    LLM:Qwen-Max → DeepSeek → StepFun → 本地 Ollama
    TTS:Edge TTS → CosyVoice → 本地 SAPI
缺 key 的 provider 自动跳过(记录原因),本地兜底永远压链尾。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

from ..adapters import CloudAdapter, CloudRouter
from .base import ProviderNotConfigured

log = logging.getLogger("fastlane.factory")


def _assemble(candidates: List[Tuple[str, Callable[[], CloudAdapter]]]) -> Tuple[List[CloudAdapter], Dict[str, str]]:
    chain: List[CloudAdapter] = []
    skipped: Dict[str, str] = {}
    for name, builder in candidates:
        try:
            chain.append(builder())
        except ProviderNotConfigured as e:
            skipped[name] = str(e)
            log.info("FastLane 跳过 %s:%s", name, e)
    return chain, skipped


def _asr_candidates() -> List[Tuple[str, Callable[[], CloudAdapter]]]:
    from .asr_cloud import DoubaoFlashASR, LocalSenseVoiceASR, QwenASR, SiliconFlowASR

    return [
        ("doubao-flash-asr", DoubaoFlashASR),
        ("qwen-asr", QwenASR),
        ("siliconflow-sensevoice", SiliconFlowASR),
        ("local-sensevoice", LocalSenseVoiceASR),
    ]


def _llm_candidates() -> List[Tuple[str, Callable[[], CloudAdapter]]]:
    from .llm_cloud import OllamaLocalLLMAdapter, deepseek_llm, qwen_llm, stepfun_llm

    return [
        ("qwen-max", qwen_llm),
        ("deepseek-v3.2", deepseek_llm),
        ("stepfun", stepfun_llm),
        ("ollama-local", OllamaLocalLLMAdapter),
    ]


def _tts_candidates() -> List[Tuple[str, Callable[[], CloudAdapter]]]:
    from .tts_cloud import CosyVoiceTTS, EdgeTTSProvider, SAPILocalTTS

    return [
        ("edge-tts", EdgeTTSProvider),
        ("cosyvoice", CosyVoiceTTS),
        ("sapi-local", SAPILocalTTS),
    ]


def build_asr_chain() -> CloudRouter:
    chain, _ = _assemble(_asr_candidates())
    if not chain:
        raise ProviderNotConfigured("ASR 链为空:无任何云 key 且本地 daemon 未初始化")
    return CloudRouter(chain)


def build_llm_chain() -> CloudRouter:
    chain, _ = _assemble(_llm_candidates())
    if not chain:
        raise ProviderNotConfigured("LLM 链为空")
    return CloudRouter(chain)


def build_tts_chain() -> CloudRouter:
    chain, _ = _assemble(_tts_candidates())
    if not chain:
        raise ProviderNotConfigured("TTS 链为空")
    return CloudRouter(chain)


def provider_status() -> Dict[str, Any]:
    """三条链的装配报告(给 /health、预热脚本、launcher UI 用)"""
    report: Dict[str, Any] = {}
    for kind, candidates in (
        ("asr", _asr_candidates()),
        ("llm", _llm_candidates()),
        ("tts", _tts_candidates()),
    ):
        chain, skipped = _assemble(candidates)
        report[kind] = {
            "chain": [a.name for a in chain],
            "skipped": skipped,
        }
    return report
