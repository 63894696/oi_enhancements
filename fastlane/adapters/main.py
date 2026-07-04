"""FastLane 云端聚合服务(2026-07-03 provider 实装版)

跑法:uvicorn fastlane.adapters.main:app --port 8760

变化(相对骨架版):
- /transcribe 接受 {"audio_b64": ...} → 真降级链(豆包→Qwen→SiliconFlow→本地)
  仍兼容旧骨架格式 {"audio_data": [...]}(chunk dict 列表)
- /generate → LLM 降级链(Qwen-Max→DeepSeek→StepFun→Ollama)
- 新增 /synthesize → TTS 降级链(Edge→CosyVoice→SAPI)
- /health 返回三条链的装配报告(哪些 provider 进链、哪些缺 key 被跳过)
- 2026-07-03 H-2 修法:加 Bearer auth middleware,复用 ghostline 仓 auth_token 机制
- 2026-07-03 M-6 修法:用懒装配,import 时不再 fail-fast
- 2026-07-03 M-9 修法:用 Pydantic v2 model 校验请求体(若 pydantic 已装)
"""
from __future__ import annotations

import base64
import os
import warnings
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from . import CloudASRClient, CloudIMClient, CloudLLMClient, CloudRouter
from ..providers import (
    LazyCloudRouter,
    build_asr_chain_lazy,
    build_llm_chain_lazy,
    build_tts_chain_lazy,
    provider_status,
)

# 2026-07-03 M-9 修法:可选 Pydantic v2 schema 校验
try:
    from pydantic import BaseModel, Field, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore
    Field = lambda *a, **k: None  # type: ignore
    ValidationError = Exception


if PYDANTIC_AVAILABLE:
    class TranscribeRequest(BaseModel):
        audio_b64: Optional[str] = None
        audio_data: Optional[list] = None
        language: Optional[str] = Field(default="zh", description="zh / en / auto")

    class GenerateRequest(BaseModel):
        prompt: Optional[str] = None
        messages: Optional[list] = None
        temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0)
        max_tokens: Optional[int] = Field(default=None, ge=1, le=32000)

    class SynthesizeRequest(BaseModel):
        text: str = Field(..., min_length=1, max_length=10000)
        voice: Optional[str] = None

    class SendMessageRequest(BaseModel):
        text: str = Field(..., min_length=1)
        target: str = Field(..., min_length=1, pattern=r"^[@#]")


# 2026-07-03 H-2 修法:复用 ghostline 仓的 auth_token 机制
AUTH_TOKEN_FILE = Path(
    os.environ.get("FASTLANE_HOME", Path.home() / ".voice_input")
) / "auth_token"


def _load_auth_token() -> str:
    """读 auth_token(跟 ghostline 仓 / voice_input 仓共用同一份文件)"""
    try:
        return AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FastLane auth_token 不存在:{AUTH_TOKEN_FILE}(运行 base daemon 启动或手动生成)。{e}",
        )


async def _verify_bearer_token(request: Request) -> None:
    """2026-07-03 H-2:FastAPI 全端点鉴权,除 /health 外必须带 Bearer token"""
    if request.url.path == "/health":
        return
    auth = request.headers.get("authorization", "")
    expected_prefix = "Bearer "
    if not auth.startswith(expected_prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="FastLane 鉴权:缺 Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    provided = auth[len(expected_prefix):].strip()
    expected = _load_auth_token()
    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="FastLane 鉴权:token 不匹配",
            headers={"WWW-Authenticate": "Bearer"},
        )


# 2026-07-03 M-7:CloudLLMClient / CloudASRClient / CloudIMClient 是 @deprecated 骨架
# 它们还保留供旧 client 向后兼容,但新代码应该走 CloudRouter
def _warn_skeleton(name: str) -> None:
    warnings.warn(
        f"CloudAdapter.{name} 是 @deprecated 骨架 adapter,新代码应走 CloudRouter。"
        f"计划 v1.0 移除。",
        DeprecationWarning,
        stacklevel=3,
    )


class CloudAPIService:
    def __init__(self, config: dict):
        self.config = config
        # 2026-07-03 M-6:用 LazyCloudRouter 替代同步 build_*
        # 首次调用时装配,缺 key 时返 unavailable 而非让进程挂
        self.asr_router = build_asr_chain_lazy()
        self.llm_router = build_llm_chain_lazy()
        self.tts_router = build_tts_chain_lazy()
        # 2026-07-03 M-7:CloudLLMClient 等骨架 adapter 标 deprecated
        self._llm_client_skeleton = CloudLLMClient(config.get("llm", {}))
        self._asr_client_skeleton = CloudASRClient(config.get("asr", {}))
        self.im_client = CloudIMClient(config.get("im", {}))
        self.app = FastAPI(title="FastLane Cloud Service")
        # 2026-07-03 H-2:注册 Bearer auth 鉴权中间件
        # 2026-07-04 修法:中间件捕 HTTPException 时必须传 status_code,
        # 否则 FastAPI 默认 503,导致 401 错返 503
        @self.app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            try:
                await _verify_bearer_token(request)
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,  # 2026-07-04 修法:显式传 401 不是 503
                    content={"detail": e.detail},
                    headers=e.headers or {},
                )
            return await call_next(request)

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
            "chains": {
                "asr": self.asr_router.status,
                "llm": self.llm_router.status,
                "tts": self.tts_router.status,
            },
            "im": await self.im_client.health_check(),
        }

    def _validate(self, model_cls, request: dict):
        """2026-07-03 M-9:Pydantic schema 校验(可选,fallback 到 no-op)"""
        if not PYDANTIC_AVAILABLE:
            return request
        try:
            return model_cls(**request).model_dump(exclude_none=True)
        except ValidationError as e:
            raise HTTPException(422, f"请求体 schema 校验失败:{e.errors()}")

    async def _transcribe(self, request: dict):
        # 2026-07-03 M-9:schema 校验
        request = self._validate(TranscribeRequest, request)
        if request.get("audio_b64"):
            try:
                wav = base64.b64decode(request["audio_b64"])
            except Exception as e:
                raise HTTPException(400, f"audio_b64 解码失败:{e}")
            try:
                text = await self.asr_router.call_with_fallback("transcribe_wav", wav)
            except RuntimeError as e:
                raise HTTPException(503, f"FastLane ASR 全链失败:{e}")
            return {"status": "ok", "text": text}
        # H-5:旧 audio_data 路径标 deprecated
        _warn_skeleton("CloudASRClient.transcribe_chunks")
        chunks = request.get("audio_data", [])
        if not chunks:
            raise HTTPException(400, "需要 audio_b64 或 audio_data 字段之一")
        try:
            texts = await self._asr_client_skeleton.transcribe_chunks(chunks)
        except Exception as e:
            raise HTTPException(500, f"旧 audio_data 路径失败(已 deprecated):{e}")
        return {
            "status": "ok",
            "texts": texts,
            "_deprecated": (
                "audio_data 格式已 deprecated,FastLane H-5 之后真跑降级链需传 audio_b64(WAV 字节 base64)。"
                "下次大版本 v1.0 移除此路径。"
            ),
        }

    async def _generate(self, request: dict):
        # 2026-07-03 M-9:schema 校验
        request = self._validate(GenerateRequest, request)
        prompt = request.get("prompt") or request.get("messages")
        if not prompt:
            raise HTTPException(400, "缺 prompt / messages")
        try:
            text = await self.llm_router.call_with_fallback("generate", prompt, **{
                "temperature": request.get("temperature", 0.7),
            })
        except RuntimeError as e:
            raise HTTPException(503, f"FastLane LLM 全链失败:{e}")
        return {"status": "ok", "text": text}

    async def _synthesize(self, request: dict):
        # 2026-07-03 M-9:schema 校验
        request = self._validate(SynthesizeRequest, request)
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
        # 2026-07-03 M-9:schema 校验
        request = self._validate(SendMessageRequest, request)
        # 2026-07-04 L-7 修法:IM 失败时返 502(下游服务失败),不是 200
        result = await self.im_client.send_request("/message", request)
        if result.get("status") != "sent":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"FastLane IM 发送失败:{result.get('error', 'unknown')}",
            )
        return result


# 导出 FastAPI app 对象
cloud_service = CloudAPIService({})
app = cloud_service.app
