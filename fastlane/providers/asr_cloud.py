"""F-1:云端 ASR provider(降级链:豆包 → Qwen → SiliconFlow → 本地 SenseVoice)

统一契约(给 CloudRouter 用):
    async transcribe_wav(wav_bytes: bytes) -> str
所有 provider 共享这一个方法名 → CloudRouter.call_with_fallback("transcribe_wav", ...)
"""
from __future__ import annotations

import base64
import uuid
from typing import Any, Dict

from ..adapters import CloudAdapter
from .base import ProviderNotConfigured, require_env, tls13_client


class DoubaoFlashASR(CloudAdapter):
    """火山豆包大模型录音识别(极速版,一次 REST 调用)

    需要火山「语音技术」控制台的 App Key + Access Key(与 ARK_API_KEY 不同体系):
        DOUBAO_ASR_APP_KEY / DOUBAO_ASR_ACCESS_KEY
    未配置 → ProviderNotConfigured → 工厂跳过(不进降级链)。
    """

    ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    RESOURCE_ID = "volc.bigasr.auc_turbo"

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "doubao-flash-asr")
        cfg.setdefault("endpoint", self.ENDPOINT)
        super().__init__(cfg)
        self.app_key = cfg.get("app_key") or require_env("DOUBAO_ASR_APP_KEY")
        self.access_key = cfg.get("access_key") or require_env("DOUBAO_ASR_ACCESS_KEY")

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        headers = {
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.RESOURCE_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        payload = {
            "user": {"uid": "fastlane"},
            "audio": {"format": "wav", "data": base64.b64encode(wav_bytes).decode()},
            "request": {"model_name": "bigmodel"},
        }
        async with tls13_client(timeout_s=60) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        text = (data.get("result") or {}).get("text", "")
        if not text:
            raise RuntimeError(f"豆包 ASR 空结果:{str(data)[:200]}")
        return text


class QwenASR(CloudAdapter):
    """阿里 DashScope qwen-audio-asr(BAILIAN_API_KEY,base64 data URI 一次调用)"""

    ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "qwen-asr")
        cfg.setdefault("endpoint", self.ENDPOINT)
        super().__init__(cfg)
        self.api_key = cfg.get("api_key") or require_env("BAILIAN_API_KEY", "DASHSCOPE_API_KEY")
        self.model = cfg.get("model", "qwen-audio-asr")

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        data_uri = "data:audio/wav;base64," + base64.b64encode(wav_bytes).decode()
        payload = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": [{"audio": data_uri}]}]},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with tls13_client(timeout_s=60) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            content = data["output"]["choices"][0]["message"]["content"]
            text = content[0]["text"] if isinstance(content, list) else str(content)
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Qwen ASR 响应解析失败:{str(data)[:200]}") from e
        if not text:
            raise RuntimeError("Qwen ASR 空结果")
        return text


class SiliconFlowASR(CloudAdapter):
    """SiliconFlow OpenAI 兼容 /audio/transcriptions(SenseVoice 云端版)

    与本地 SenseVoice 同模型不同部署 —— 云端快、本地零外发,正好一对。
    """

    ENDPOINT = "https://api.siliconflow.cn/v1/audio/transcriptions"

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "siliconflow-sensevoice")
        cfg.setdefault("endpoint", self.ENDPOINT)
        super().__init__(cfg)
        self.api_key = cfg.get("api_key") or require_env("SILICONFLOW_API_KEY")
        self.model = cfg.get("model", "FunAudioLLM/SenseVoiceSmall")

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}
        async with tls13_client(timeout_s=60) as client:
            r = await client.post(self.endpoint, headers=headers, files=files, data=data)
            r.raise_for_status()
            out = r.json()
        text = out.get("text", "")
        if not text:
            raise RuntimeError(f"SiliconFlow ASR 空结果:{str(out)[:200]}")
        return text


class LocalSenseVoiceASR(CloudAdapter):
    """本地兜底:调 ADR-001 asr daemon 的 /transcribe(loopback REST)

    daemon 没跑时抛错 → CloudRouter 宣告全链失败(FastLane 的最后一环就是它,
    它挂了说明本机语音栈没起,属于显式故障而不是静默降级)。
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "local-sensevoice")
        cfg.setdefault("endpoint", "http://127.0.0.1:8731/transcribe")
        super().__init__(cfg)
        self.auth_token = cfg.get("auth_token", "")

    def _load_token(self) -> str:
        if self.auth_token:
            return self.auth_token
        from pathlib import Path

        f = Path.home() / ".voice_input" / "auth_token"
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        raise ProviderNotConfigured("本地 voice_input auth_token 不存在(daemon 从未启动过?)")

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self._load_token()}"}
        payload = {"audio_b64": base64.b64encode(wav_bytes).decode()}
        # loopback 明文 —— enforce_https 的 loopback 例外
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("text", "")
