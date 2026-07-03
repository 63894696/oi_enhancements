"""F-1:云端 ASR provider(降级链:豆包 → Qwen → SiliconFlow → 本地 SenseVoice)

统一契约(给 CloudRouter 用):
    async transcribe_wav(wav_bytes: bytes) -> str
所有 provider 共享这一个方法名 → CloudRouter.call_with_fallback("transcribe_wav", ...)

2026-07-03 修法:
- M-1:白名单域名解析 IP 加 TTL(默认 5 分钟),过期重新解析
- M-8:auth_token 缓存到实例属性(不再每次读盘)
"""
from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..adapters import CloudAdapter
from .base import ProviderNotConfigured, require_env, tls13_client


# 2026-07-03 M-1 修法:白名单域名解析 IP 的 TTL(秒)
# 之前白名单 IP 永久缓存,域名后续解析到不同 IP 时旧 IP 仍放行
_RESOLVED_IP_TTL_S = 5 * 60  # 5 分钟


class _TTLResolvedIPs:
    """M-1:带 TTL 的解析 IP 缓存

    每次解析记录 ts,过期清空 → 重新解析
    """

    def __init__(self) -> None:
        self._ips: Dict[str, str] = {}
        self._ts: Dict[str, float] = {}

    def get(self, host: str) -> Optional[str]:
        if host in self._ips:
            if time.time() - self._ts[host] < _RESOLVED_IP_TTL_S:
                return self._ips[host]
            # 过期
            del self._ips[host]
            del self._ts[host]
        return None

    def set(self, host: str, ip: str) -> None:
        self._ips[host] = ip
        self._ts[host] = time.time()

    def clear(self) -> None:
        self._ips.clear()
        self._ts.clear()


class DoubaoFlashASR(CloudAdapter):
    """火山豆包大模型录音识别(极速版,一次 REST 调用)

    需要火山「语音技术」控制台的 App Key + Access Key(与 ARK_API_KEY 不同体系):
        DOUBAO_ASR_APP_KEY / DOUBAO_ASR_ACCESS_KEY
    未配置 → ProviderNotConfigured → 工厂跳过(不进降级链)。

    2026-07-03 H-7 修法:transcribe_wav 调用 tls13_client 必须传 endpoint(统一修法)
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
        # 2026-07-03 H-7:传 endpoint 让 tls13_client 按 host 策略
        async with tls13_client(timeout_s=60, endpoint=self.endpoint) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        text = (data.get("result") or {}).get("text", "")
        if not text:
            raise RuntimeError(f"豆包 ASR 空结果:{str(data)[:200]}")
        return text


class QwenASR(CloudAdapter):
    """阿里 DashScope qwen-audio-asr(BAILIAN_API_KEY,base64 data URI 一次调用)

    2026-07-03 H-7 修法:transcribe_wav 调用 tls13_client 必须传 endpoint
    2026-07-03 H-8 修法:空 content / 解析失败 抛错(让 CloudRouter 降级)
    """

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
        async with tls13_client(timeout_s=60, endpoint=self.endpoint) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            content = data["output"]["choices"][0]["message"]["content"]
            text = content[0]["text"] if isinstance(content, list) else str(content)
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Qwen ASR 响应解析失败:{str(data)[:200]}") from e
        if not text:
            # 2026-07-03 H-8:云端空 content 必须抛错,让 CloudRouter 降级
            raise RuntimeError("Qwen ASR 空结果")
        return text


class SiliconFlowASR(CloudAdapter):
    """SiliconFlow OpenAI 兼容 /audio/transcriptions(SenseVoice 云端版)

    与本地 SenseVoice 同模型不同部署 —— 云端快、本地零外发,正好一对。

    2026-07-03 H-7 修法:transcribe_wav 调用 tls13_client 必须传 endpoint,
    否则 SiliconFlow TLS 1.3 不兼容问题(H-1)修法未生效
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
        # 2026-07-03 H-7:传 endpoint,让 tls13_client 走 TLS 1.2 降级
        async with tls13_client(timeout_s=60, endpoint=self.endpoint) as client:
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
        # 2026-07-03 M-8 修法:缓存 token + mtime,避免每次请求读盘
        self._cached_token: Optional[str] = cfg.get("auth_token") or None
        self._token_mtime: Optional[float] = None
        self._token_path = Path.home() / ".voice_input" / "auth_token"

    def _load_token(self) -> str:
        if self._cached_token and self._token_path.exists():
            mtime = self._token_path.stat().st_mtime
            if self._token_mtime is None or mtime != self._token_mtime:
                self._cached_token = self._token_path.read_text(encoding="utf-8").strip()
                self._token_mtime = mtime
        elif not self._cached_token:
            if not self._token_path.exists():
                raise ProviderNotConfigured("本地 voice_input auth_token 不存在(daemon 从未启动过?)")
            self._cached_token = self._token_path.read_text(encoding="utf-8").strip()
            self._token_mtime = self._token_path.stat().st_mtime
        return self._cached_token

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self._load_token()}"}
        payload = {"audio_b64": base64.b64encode(wav_bytes).decode()}
        # loopback 明文 —— enforce_https 的 loopback 例外
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        # 2026-07-03 H-4 修法:本地兜底空字符串必须抛错(让 CloudRouter 降级)
        text = data.get("text", "")
        if not text:
            raise RuntimeError(
                f"本地 SenseVoice ASR 空结果(text=''),daemon 响应:{str(data)[:200]}"
            )
        return text
