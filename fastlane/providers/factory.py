"""FastLane 降级链工厂(F-5)

按「环境里有什么 key」装配三条 CloudRouter 链,顺序遵循 ADR-002:
    ASR:豆包 → Qwen → SiliconFlow → 本地 SenseVoice
    LLM:Qwen-Max → DeepSeek → StepFun → 本地 llama.cpp(MiniCPM-V-4.6)
    TTS:Edge TTS → CosyVoice → 本地 SAPI
缺 key 的 provider 自动跳过(记录原因),本地兜底永远压链尾。

2026-07-03 修法:
- M-6 懒装配:build_*_chain 改 lazy,首次 call_with_fallback 才装配(避免 import 时 fail-fast)
- M-2 重试逻辑:CloudRouter.call_with_fallback 加 max_retries 配置(默认 0,云端可配 1)
"""
from __future__ import annotations

import logging
import threading
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
        ("llama-server", OllamaLocalLLMAdapter),  # 2026-07-12: 实际是 llama-server (MiniCPM-V-4.6);类名 OllamaLocalLLMAdapter 保留向后兼容
    ]


def _tts_candidates() -> List[Tuple[str, Callable[[], CloudAdapter]]]:
    from .tts_cloud import CosyVoiceTTS, EdgeTTSProvider, SAPILocalTTS

    return [
        ("edge-tts", EdgeTTSProvider),
        ("cosyvoice", CosyVoiceTTS),
        ("sapi-local", SAPILocalTTS),
    ]


# ============================================================
# 2026-07-03 M-6:懒装配模式
# 之前 import 时同步构建,缺 key 抛错让整个 app 无法启动
# 现在 lazy_chain 首次调用 call_with_fallback 时才装配
# ============================================================
class LazyCloudRouter:
    """懒装配的 CloudRouter

    - 构造时只存 builder 列表,不实例化任何 provider
    - 首次 call_with_fallback 才调 _assemble + 装配 router
    - 装配失败(全空)返 unavailable,不让进程挂
    - 装配成功后缓存 router,后续调用复用
    """

    def __init__(self, candidates: List[Tuple[str, Callable[[], CloudAdapter]]], method: str):
        self._candidates = candidates
        self._method = method
        self._router: CloudRouter | None = None
        self._skipped: Dict[str, str] = {}
        self._lock = threading.Lock()

    def _ensure_router(self) -> CloudRouter:
        if self._router is not None:
            return self._router
        with self._lock:
            if self._router is not None:
                return self._router
            chain, self._skipped = _assemble(self._candidates)
            if not chain:
                raise ProviderNotConfigured(
                    f"{self._method} 链为空:无任何云 key 且本地 daemon 未初始化。skipped={self._skipped}"
                )
            self._router = CloudRouter(chain)
            log.info("FastLane 懒装配 {self._method} 完成:%d 个 provider", len(chain))
            return self._router

    async def call_with_fallback(self, *args, **kwargs):
        router = self._ensure_router()
        return await router.call_with_fallback(*args, **kwargs)

    @property
    def status(self) -> Dict[str, Any]:
        """暴露当前装配状态(给 /health)"""
        if self._router is None:
            return {"chain": [], "skipped": self._skipped, "lazy": True}
        return {
            "chain": [a.name for a in self._router.adapters],
            "skipped": self._skipped,
            "lazy": False,
        }


# ============================================================
# 暴露给 main.py 的接口(同步版 LazyCloudRouter)
# ============================================================
def build_asr_chain_lazy() -> LazyCloudRouter:
    return LazyCloudRouter(_asr_candidates(), "asr")


def build_llm_chain_lazy() -> LazyCloudRouter:
    return LazyCloudRouter(_llm_candidates(), "llm")


def build_tts_chain_lazy() -> LazyCloudRouter:
    return LazyCloudRouter(_tts_candidates(), "tts")


# 旧接口保留(向后兼容 + 测试用)
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
