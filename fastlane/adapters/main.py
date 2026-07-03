"""FastLane 云端聚合服务(2026-07-03 provider 实装版)

跑法:uvicorn fastlane.adapters.main:app --port 8760

变化(相对骨架版):
- /transcribe 接受 {"audio_b64": ...} → 真降级链(豆包→Qwen→SiliconFlow→本地)
  仍兼容旧骨架格式 {"audio_data": [...]}(chunk dict 列表)
- /generate → LLM 降级链(Qwen-Max→DeepSeek→StepFun→Ollama)
- 新增 /synthesize → TTS 降级链(Edge→CosyVoice→SAPI)
- /health 返回三条链的装配报告(哪些 provider 进链、哪些缺 key 被跳过)
"""
from __future__ import annotations

import base64

from fastapi import FastAPI, HTTPException

from . import CloudASRClient, CloudIMClient, CloudLLMClient, CloudRouter
from ..providers import build_asr_chain, build_llm_chain, build_tts_chain, provider_status


class CloudAPIService:
    def __init__(self, config: dict):
        self.config = config
        # 骨架 adapter 保留:/send_message 与向后兼容的 chunk 式 /transcribe
        self.asr_client = CloudASRClient(config.get("asr", {}))
        self.llm_client = CloudLLMClient(config.get("llm", {}))
        self.im_client = CloudIMClient(config.get("im", {}))
        # F-5 真降级链(工厂按环境 key 装配;本地兜底保证链非空)
        self.asr_router: CloudRouter = build_asr_chain()
        self.llm_router: CloudRouter = build_llm_chain()
        self.tts_router: CloudRouter = build_tts_chain()
        self.app = FastAPI(title="FastLane Cloud Service")
        self._setup_routes()

    def _setup_routes(self):
        self.app.get("/health")(self._health_check)
        self.app.post("/transcribe")(self._transcribe)
        self.app.post("/generate")(self._generate)
        self.app.post("/synthesize")(self._synthesize)
        self.app.post("/send_message")(self._send_message)

    async def _health_check(self):
        return {
            "service": "fastlane",
            "chains": provider_status(),
            "im": await self.im_client.health_check(),
        }

    async def _transcribe(self, request: dict):
        if "audio_b64" in request:
            try:
                wav = base64.b64decode(request["audio_b64"])
            except Exception as e:
                raise HTTPException(400, f"audio_b64 解码失败:{e}")
            try:
                text = await self.asr_router.call_with_fallback("transcribe_wav", wav)
            except RuntimeError as e:
                raise HTTPException(503, f"FastLane ASR 全链失败:{e}")
            return {"status": "ok", "text": text}
        # 旧骨架格式兼容
        chunks = request.get("audio_data", [])
        texts = await self.asr_client.transcribe_chunks(chunks)
        return {"status": "ok", "texts": texts}

    async def _generate(self, request: dict):
        prompt = request.get("prompt") or request.get("messages")
        if not prompt:
            raise HTTPException(400, "缺 prompt / messages")
        try:
            text = await self.llm_router.call_with_fallback("generate", prompt)
        except RuntimeError as e:
            raise HTTPException(503, f"FastLane LLM 全链失败:{e}")
        return {"status": "ok", "text": text}

    async def _synthesize(self, request: dict):
        text = request.get("text", "")
        if not text:
            raise HTTPException(400, "缺 text")
        try:
            result = await self.tts_router.call_with_fallback("synthesize", text)
        except RuntimeError as e:
            raise HTTPException(503, f"FastLane TTS 全链失败:{e}")
        audio = result.pop("audio", None)
        if audio:
            result["audio_b64"] = base64.b64encode(audio).decode()
        return result

    async def _send_message(self, request: dict):
        return await self.im_client.send_request("/message", request)


# 导出 FastAPI app 对象
cloud_service = CloudAPIService({})
app = cloud_service.app
