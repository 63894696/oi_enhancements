"""F-3:云端 TTS provider(Edge TTS → CosyVoice → 本地 SAPI 兜底)

统一契约:async synthesize(text) -> dict
    {"status": "ok", "provider": ..., "audio": bytes|None, "played": bool}
Edge/CosyVoice 返回音频字节(mp3),SAPI 直接本机播放(played=True)。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from ..adapters import CloudAdapter
from .base import ProviderNotConfigured, require_env, tls13_client


class EdgeTTSProvider(CloudAdapter):
    """Edge TTS(微软云,免费,不要 key;需要 edge-tts 包)

    ADR-002 F-3 首选。注意:GhostLine 禁止它(走微软云);FastLane 允许。
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "edge-tts")
        # edge-tts 包内部管理 endpoint(wss://speech.platform.bing.com);
        # 不设 endpoint,健康检查按「包是否可导入」报告
        super().__init__(cfg)
        self.voice = cfg.get("voice", "zh-CN-XiaoxiaoNeural")
        try:
            import edge_tts  # noqa: F401
        except ImportError as e:
            raise ProviderNotConfigured("edge-tts 包未安装(pip install edge-tts)") from e

    async def synthesize(self, text: str) -> Dict[str, Any]:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice=self.voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        audio = b"".join(chunks)
        if not audio:
            raise RuntimeError("Edge TTS 返回空音频")
        return {"status": "ok", "provider": self.name, "audio": audio, "played": False}


class CosyVoiceTTS(CloudAdapter):
    """BAILIAN CosyVoice(DashScope,付费 token,声音质量高)

    2026-07-03 H-7 修法:synthesize 调用 tls13_client 必须传 endpoint
    """

    ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "cosyvoice")
        cfg.setdefault("endpoint", self.ENDPOINT)
        super().__init__(cfg)
        self.api_key = cfg.get("api_key") or require_env("BAILIAN_API_KEY", "DASHSCOPE_API_KEY")
        self.model = cfg.get("model", "qwen3-tts-flash")
        self.voice = cfg.get("voice", "Cherry")

    async def synthesize(self, text: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "input": {"text": text, "voice": self.voice},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with tls13_client(timeout_s=60, endpoint=self.endpoint) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        audio_info = (data.get("output") or {}).get("audio") or {}
        url = audio_info.get("url", "")
        b64 = audio_info.get("data", "")
        if b64:
            decoded = base64.b64decode(b64)
            if not decoded:
                raise RuntimeError("CosyVoice b64 返回空音频")
            return {"status": "ok", "provider": self.name,
                    "audio": decoded, "played": False}
        if url:
            # 2026-07-03 H-6 修法:CosyVoice 返回 url 字段时,先 enforce_https + 域名白名单
            from urllib.parse import urlparse

            from ..adapters import enforce_https

            try:
                safe_url = enforce_https(url)
            except ValueError as e:
                raise RuntimeError(f"CosyVoice URL 拒绝(F-6 明文 HTTP 违规):{e}")

            host = (urlparse(safe_url).hostname or "").lower()
            _allowed_hosts_conditions = (
                host == "dashscope.aliyuncs.com"
                or host == "dashscope-result.aliyuncs.com"
                or (host.startswith("oss-") and host.endswith(".aliyuncs.com"))
            )
            if not _allowed_hosts_conditions:
                raise RuntimeError(
                    f"CosyVoice URL host 不在白名单:{host}(必须 DashScope 域名)。"
                    f"防 SSRF 拒绝。"
                )

            # 2026-07-03 H-7:GET URL 也传 endpoint
            async with tls13_client(timeout_s=60, endpoint=safe_url) as client:
                audio = (await client.get(safe_url)).content
            if not audio:
                raise RuntimeError(f"CosyVoice URL 返回空音频:{safe_url}")
            return {"status": "ok", "provider": self.name, "audio": audio, "played": False}
        raise RuntimeError(f"CosyVoice 无音频输出:{str(data)[:200]}")


class SAPILocalTTS(CloudAdapter):
    """本地兜底:调 ADR-001 tts daemon /synthesize(SAPI,直接本机播放)

    2026-07-03 M-8 续:auth_token 缓存 + mtime 检查
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "sapi-local")
        cfg.setdefault("endpoint", "http://127.0.0.1:8732/synthesize")
        super().__init__(cfg)
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
                raise ProviderNotConfigured("本地 voice_input auth_token 不存在")
            self._cached_token = self._token_path.read_text(encoding="utf-8").strip()
            self._token_mtime = self._token_path.stat().st_mtime
        return self._cached_token

    async def synthesize(self, text: str) -> Dict[str, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {self._load_token()}"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(self.endpoint, json={"text": text}, headers=headers)
            r.raise_for_status()
        return {"status": "ok", "provider": self.name, "audio": None, "played": True}
