# -*- coding: utf-8 -*-
# companion_asr_providers.py — ASR Provider 注册表 + 工厂(2026-09-16 M3.10)
#
# 目标:不绑死任何一个,装好即用(默认 bailian-paraformer-v2),
#       用户能切到 openai-whisper-streaming / 本地 sensevoice 等。
#
# 配置:data/settings.json 里 {active_provider, providers: {name: {api_key, endpoint, ...}}}
# 默认从环境变量 BAILIAN_API_KEY 抽(不落盘),用户自定义时落盘。
#
# 设计:每个 provider 是一个 AsrProviderProtocol 实现 + 注册表里一个 dict
#       (name / display / kind=stream|sync / fields 描述)。
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from companion_asr import BailianAsrSession

log = logging.getLogger("prisiragent-companion.asr.providers")


@dataclass
class ProviderField:
    key: str            # 在 settings 里叫什么(api_key/endpoint/sample_rate/...)
    label: str          # UI 提示
    secret: bool = False  # 是密钥(前端 mask)
    required: bool = False
    default: Any = ""
    placeholder: str = ""


@dataclass
class ProviderSpec:
    name: str           # 唯一 id,"bailian-paraformer-v2"
    display: str        # 中文显示名
    kind: str           # "stream"(走 ws/ PCM)+ "sync"(整段送)
    fields: list[ProviderField] = field(default_factory=list)
    # 工厂:接收 cfg dict(已 resolve 默认值),返 AsrProviderProtocol 实例
    factory: Optional[Any] = None
    # M3.29.9 — 分级 + 准备提示(让 UI 能把"需控制台/服务"和"填 key 即可"分开)
    tier: str = "advanced"           # "simple" 填 key 即可 / "advanced" 需先准备
    prep_hint: str = ""             # 选中后给用户的准备提示(空 = 不显示)


# ============================================================
# 注册表(目前只注册百炼,后续加 openai-whisper-streaming / 本地 sensevoice)
# ============================================================
PROVIDERS: dict[str, ProviderSpec] = {}


def _make_bailian_session(cfg: dict, callbacks: dict):
    """cfg: {api_key, model, sample_rate, language_hints, format}"""
    api_key = cfg.get("api_key") or os.environ.get("BAILIAN_API_KEY", "")
    if not api_key:
        raise RuntimeError("BAILIAN_API_KEY 未配置(env 或 settings)")
    # language_hints 容忍 list / CSV string(UI 可能传 "zh,en")
    raw_hints = cfg.get("language_hints")
    if isinstance(raw_hints, str):
        lang_list = [s.strip() for s in raw_hints.split(",") if s.strip()] or ["zh", "en"]
    elif isinstance(raw_hints, list):
        lang_list = raw_hints or ["zh", "en"]
    else:
        lang_list = ["zh", "en"]
    return BailianAsrSession(
        api_key=api_key,
        on_partial=callbacks.get("on_partial"),
        on_final=callbacks.get("on_final"),
        on_started=callbacks.get("on_started"),
        on_finished=callbacks.get("on_finished"),
        on_err=callbacks.get("on_err"),
        sample_rate=int(cfg.get("sample_rate") or 16000),
        language_hints=lang_list,
        model=cfg.get("model") or "paraformer-realtime-v2",
        audio_format=cfg.get("format") or "pcm",
    )


PROVIDERS["bailian-paraformer-v2"] = ProviderSpec(
    name="bailian-paraformer-v2",
    display="阿里云百炼 Paraformer-realtime-v2 · 中文 CER 3.40% (2025 SpeechColab)",
    kind="stream",
    tier="simple",
    prep_hint="",
    fields=[
        ProviderField("api_key", "API Key", secret=True, required=True,
                       placeholder="sk-ws-...",
                       default=os.environ.get("BAILIAN_API_KEY", "")),
        # 端点(wss://dashscope.aliyuncs.com/api-ws/v1/inference) + 模型 +
        # 格式(pcm/16k) + 语种均写死,用户填 key 即可用。
        # 高级用户想改可将来加"高级"折叠区
    ],
    factory=_make_bailian_session,
)

# 阿里 Qwen3-ASR-Flash(2026 新)— DashScope Qwen-Audio 系列旗舰 ASR,
# 30 种语种 + 22 种中文方言 + 抗噪/抗口音。
# 重要(M3.16):与 Paraformer-realtime-v2 协议不同 ——
#   真模型名: qwen-audio-3.0-asr-flash-streaming
#   端点: workspace-specific — wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference
#         需 env DASHSCOPE_WORKSPACE_ID
#   API: dashscope.audio.asr.Recognition(model, format, sample_rate, callback)
#         回调:on_open / on_event(partial+sentence_end)/ on_complete / on_error / on_close
#         方法:start() / send_audio_frame(bytes) / stop()
#   key: 用 DASHSCOPE_API_KEY(env)或 cfg api_key;若用百炼 sk-ws key 也兼容


class Qwen3AsrSession:
    """DashScope Qwen-Audio-3.0 流式 ASR(2026-09-16 现行)。

    通过 dashscope SDK 的 Recognition 类封包 WSS。
    SDK 是同步 API,但内部已管理 WSS 收发;我们在 asyncio 线程里跑。
    callbacks(全部可选):on_partial / on_final / on_started / on_finished / on_err
    """

    name = "qwen3-asr-flash"

    def __init__(self, cfg, callbacks):
        import dashscope
        api_key = cfg.get("api_key") or os.environ.get(
            "DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "Qwen3-ASR API Key 未配置(env DASHSCOPE_API_KEY 或 settings)")
        dashscope.api_key = api_key
        # workspace endpoint(若没设 env,给出清晰提示)
        workspace = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        if not workspace:
            raise RuntimeError(
                "Qwen3-ASR 走 workspace-specific 端点,需 env DASHSCOPE_WORKSPACE_ID "
                "(阿里云百炼控制台 → 模型工作室 → 工作空间 ID,形如 llm-xxxxxx)")
        dashscope.base_websocket_api_url = (
            f"wss://{workspace}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        )
        self.model = "qwen-audio-3.0-asr-flash-streaming"
        self.sample_rate = int(cfg.get("sample_rate") or 16000)
        self.on_partial = callbacks.get("on_partial")
        self.on_final = callbacks.get("on_final")
        self.on_started = callbacks.get("on_started")
        self.on_finished = callbacks.get("on_finished")
        self.on_err = callbacks.get("on_err")
        self._started = False
        self._closed = False
        self._recognition = None  # dashscope.Recognition
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._send_thread: Optional[threading.Thread] = None
        self._send_queue: list[bytes] = []
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._err_msg: Optional[str] = None

    async def start(self):
        # 在调用方 asyncio 线程里 set _loop(回调线程可 schedule)
        self._loop = asyncio.get_running_loop()
        try:
            from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"dashscope SDK 缺 audio.asr 模块: {e}")

        outer = self

        class _Cb(RecognitionCallback):
            def on_open(self_inner):
                outer._started = True
                if outer.on_started:
                    outer._loop.call_soon_threadsafe(
                        asyncio.create_task, outer.on_started())

            def on_event(self_inner, result):
                try:
                    sent = result.get_sentence()
                except Exception:
                    return
                if not sent or "text" not in sent:
                    return
                text = sent.get("text", "")
                is_end = RecognitionResult.is_sentence_end(sent)
                cb = outer.on_final if is_end else outer.on_partial
                if cb and text:
                    outer._loop.call_soon_threadsafe(
                        asyncio.create_task, cb(text))

            def on_complete(self_inner):
                if outer.on_finished:
                    outer._loop.call_soon_threadsafe(
                        asyncio.create_task, outer.on_finished())

            def on_error(self_inner, result):
                msg = getattr(result, "message", "asr error")
                outer._err_msg = msg
                if outer.on_err:
                    outer._loop.call_soon_threadsafe(
                        asyncio.create_task, outer.on_err(msg))

            def on_close(self_inner):
                pass

        # DashScope Recognition 是同步的,start() 立刻返回(后台起 ws 收发)
        self._recognition = Recognition(
            model=self.model,
            format="pcm",
            sample_rate=self.sample_rate,
            callback=_Cb(),
        )
        self._recognition.start()
        # 起一个发帧线程,把 asyncio 队列里累积的 PCM 喂给 SDK
        self._send_thread = threading.Thread(
            target=self._pump, name="qwen3-asr-pump", daemon=True)
        self._send_thread.start()
        log.info("[qwen3-asr] started model=%s rate=%d ws=%s",
                 self.model, self.sample_rate,
                 os.environ.get("DASHSCOPE_WORKSPACE_ID", "?"))

    def _pump(self):
        """SDK 内部 ws 是事件循环驱动的,我们这边只是同步 send_audio_frame。
        攒帧 → 100ms 一次 batch 送。"""
        while not self._stop_event.is_set():
            time.sleep(0.05)
            with self._send_lock:
                if not self._send_queue:
                    continue
                batch = b"".join(self._send_queue)
                self._send_queue.clear()
            try:
                if self._recognition is not None:
                    self._recognition.send_audio_frame(batch)
            except Exception as e:  # noqa: BLE001
                self._err_msg = f"send_audio_frame: {e}"
                if self.on_err and self._loop:
                    self._loop.call_soon_threadsafe(
                        asyncio.create_task, self.on_err(self._err_msg))
                break

    async def send_pcm(self, pcm_bytes: bytes):
        if self._closed:
            return
        if not self._started:
            # task-started 未到,先缓存;SDK 那边会自动处理
            pass
        with self._send_lock:
            self._send_queue.append(pcm_bytes)

    async def finish(self):
        if self._closed:
            return
        self._stop_event.set()
        # 让 pump 把最后帧发完
        if self._send_thread:
            self._send_thread.join(timeout=2)
        try:
            if self._recognition is not None:
                self._recognition.stop()
        except Exception as e:  # noqa: BLE001
            log.warning("[qwen3-asr] stop err: %s", e)
        await self.close()

    async def close(self):
        self._closed = True
        self._stop_event.set()
        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=1)
        try:
            if self._recognition is not None:
                # SDK 没有显式 close,start/stop 自管生命周期
                pass
        except Exception:  # noqa: BLE001
            pass


def _make_qwen3_asr(cfg, callbacks):
    return Qwen3AsrSession(cfg, callbacks)


PROVIDERS["qwen3-asr-flash"] = ProviderSpec(
    name="qwen3-asr-flash",
    display="阿里 Qwen3-ASR-Flash(Qwen-Audio 3.0) · 30语种+22方言 · 多语混合 (2026)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在阿里云百炼控制台 → 模型工作室 → 开通 Qwen3-ASR-Flash, "
               "创建应用并绑定 Workspace,然后 `setx DASHSCOPE_WORKSPACE_ID=你的workspace_id` "
               "(PowerShell) 或 `export DASHSCOPE_WORKSPACE_ID=...` (bash) 后重启语伴。"),
    fields=[
        ProviderField("api_key", "API Key", secret=True, required=True,
                       placeholder="DASHSCOPE_API_KEY 或百炼 sk-ws key",
                       default=os.environ.get(
                           "DASHSCOPE_API_KEY",
                           os.environ.get("BAILIAN_API_KEY", ""))),
        ProviderField("sample_rate", "采样率 Hz", default="16000",
                       placeholder="16000 / 8000"),
        # M3.16:workspace_id 通过 env DASHSCOPE_WORKSPACE_ID 配,
        # 不暴露 UI 字段(免误填)— 模型工作室控制台一次性设好
    ],
    factory=_make_qwen3_asr,
)


# ============================================================
# 演示:OpenAI Whisper 同步版(整段上传)— 演示如何扩展不同厂商
# 实现要点:工厂返回 OpenAIWhisperSession(整段 PCM 上传 → /v1/audio/transcriptions)
# ============================================================
class OpenAIWhisperSession:
    """最小实现:整段上传 PCM → OpenAI /v1/audio/transcriptions。
    演示用,M3.x 阶段不接流式 Whisper。展示 provider 如何任意扩展。
    """
    name = "openai-whisper-sync"
    api_key: str = ""
    endpoint: str = "https://api.openai.com/v1/audio/transcriptions"
    model: str = "whisper-1"
    language: str = "zh"

    def __init__(self, cfg, callbacks):
        self.api_key = cfg.get("api_key", "")
        self.endpoint = cfg.get("endpoint") or self.endpoint
        self.model = cfg.get("model") or self.model
        self.language = cfg.get("language") or self.language
        self.on_started = callbacks.get("on_started")
        self.on_partial = callbacks.get("on_partial")
        self.on_final = callbacks.get("on_final")
        self.on_finished = callbacks.get("on_finished")
        self.on_err = callbacks.get("on_err")
        self._started = False
        self._buf = bytearray()

    async def start(self):
        if not self.api_key:
            raise RuntimeError("OpenAI api_key 未配置")
        self._started = True
        if self.on_started:
            await self.on_started()

    async def send_pcm(self, pcm_bytes: bytes):
        self._buf.extend(pcm_bytes)

    async def finish(self):
        # 同步上传 — 一次性返 final
        try:
            import aiohttp
            from io import BytesIO
            data = aiohttp.FormData()
            data.add_field("file", BytesIO(bytes(self._buf)),
                           filename="audio.wav",
                           content_type="audio/wav")
            data.add_field("model", self.model)
            data.add_field("language", self.language)
            data.add_field("response_format", "json")
            async with aiohttp.ClientSession() as ses:
                async with ses.post(self.endpoint, data=data,
                                    headers={"Authorization": f"Bearer {self.api_key}"},
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        j = await r.json()
                        text = j.get("text", "")
                        if self.on_final:
                            await self.on_final(text)
                    else:
                        body = await r.text()
                        if self.on_err:
                            await self.on_err(f"HTTP {r.status}: {body[:200]}")
        except Exception as e:
            if self.on_err:
                await self.on_err(f"{type(e).__name__}: {e}")
        finally:
            if self.on_finished:
                await self.on_finished()

    async def close(self):
        # 不重置 _started:一次 started=True 已确认服务端起来,handler 据此 return ok。
        # 不要像 BailianAsrSession 旧 finally 那样回退 — 那是另一个 bug
        self._buf.clear()


PROVIDERS["openai-whisper-sync"] = ProviderSpec(
    name="openai-whisper-sync",
    display="OpenAI Whisper(整段上传) · 英文 WER ~2-3% (Whisper-large-v3)",
    kind="sync",
    tier="simple",
    prep_hint="",
    fields=[
        ProviderField("api_key", "API Key", secret=True, required=True,
                       placeholder="sk-..."),
        # 端点固定 — 不暴露给用户(用户填 key 即可)
        # 高级用户想改可将来加"高级"折叠区
        ProviderField("model", "Whisper 模型", default="whisper-1",
                       placeholder="whisper-1"),
        ProviderField("language", "主语言", default="zh",
                       placeholder="zh / en / ja"),
    ],
    factory=lambda cfg, cb: OpenAIWhisperSession(cfg, cb),
)


def list_providers() -> list[dict]:
    """给前端展示:每个 provider 的字段定义(secret 字段不返 key 明文)。"""
    out = []
    for name, spec in PROVIDERS.items():
        out.append({
            "name": spec.name,
            "display": spec.display,
            "kind": spec.kind,
            "tier": spec.tier,                   # M3.29.9:简单/高级
            "prep_hint": spec.prep_hint,         # M3.29.9:准备提示
            "fields": [
                {"key": f.key, "label": f.label, "secret": f.secret,
                 "required": f.required, "default": f.default,
                 "placeholder": f.placeholder}
                for f in spec.fields
            ],
        })
    return out


def create_session(provider_name: str, cfg: dict, callbacks: dict):
    """工厂:用 settings 里的 cfg + 调用方回调,造一个 ASR 会话。"""
    spec = PROVIDERS.get(provider_name)
    if spec is None or spec.factory is None:
        raise RuntimeError(f"未注册的 ASR provider: {provider_name}")
    return spec.factory(cfg, callbacks)


# ============================================================
# 本地 provider — funasr(实时流,WebSocket)
# ============================================================
class LocalFunAsrSession:
    """本地 FunASR(阿里达摩院开源流式 ASR)WebSocket。
    默认 ws://127.0.0.1:10095/ — 启动:
        pip install funasr-onnx funasr-websocket
        python -m funasr_websocket.server --port 10095 --model paraformer-zh
    """
    name = "local-funasr"

    def __init__(self, cfg, callbacks):
        self.endpoint = cfg.get("endpoint") or "ws://127.0.0.1:10095/"
        self.mode = cfg.get("mode") or "2pass"  # 2pass=边识别边出句
        self._ws = None
        self._reader_task = None
        self._started = False
        self._buf = bytearray()
        self.on_partial = callbacks.get("on_partial")
        self.on_final = callbacks.get("on_final")
        self.on_started = callbacks.get("on_started")
        self.on_finished = callbacks.get("on_finished")
        self.on_err = callbacks.get("on_err")

    async def start(self):
        import websockets
        try:
            self._ws = await websockets.connect(self.endpoint, max_size=8 * 1024 * 1024)
        except Exception as e:
            raise RuntimeError(f"FunASR 连不上 {self.endpoint}: {type(e).__name__}: {e}")
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def send_pcm(self, pcm_bytes: bytes):
        if not self._started or self._ws is None:
            self._buf.extend(pcm_bytes)
            return
        await self._ws.send(pcm_bytes)

    async def finish(self):
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"end": True}))
        except Exception:
            pass
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=5)
            except asyncio.TimeoutError:
                self._reader_task.cancel()
        await self.close()

    async def close(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _reader_loop(self):
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("mode") or msg.get("type") or ""
                text = msg.get("text", "")
                if t in ("partial", "2pass-online", "online"):
                    if self.on_partial and text:
                        await self.on_partial(text)
                elif t in ("final", "2pass-offline", "offline"):
                    if self.on_final and text:
                        await self.on_final(text)
                elif t == "ready":
                    self._started = True
                    # flush buffer
                    if self._buf:
                        await self._ws.send(bytes(self._buf))
                        self._buf.clear()
                    if self.on_started:
                        await self.on_started()
                elif t == "finished":
                    if self.on_finished:
                        await self.on_finished()
                    return
        except Exception as e:
            if self.on_err:
                await self.on_err(f"{type(e).__name__}: {e}")


def _make_local_funasr(cfg, callbacks):
    return LocalFunAsrSession(cfg, callbacks)


PROVIDERS["local-funasr"] = ProviderSpec(
    name="local-funasr",
    display="本地(隐私优先) · sherpa-onnx zipformer-zh-14M 默认 + 自选 FunASR/SenseVoice",
    kind="stream",
    tier="advanced",
    prep_hint=("需先在系统中启动本地 ASR 服务(WebSocket)。推荐两种:\n"
               "  • sherpa-onnx(轻量): pip install sherpa-onnx;下载 zipformer-zh-14M 模型(54MB int8);\n"
               "      python -m sherpa_onnx.runtime.server --port 10096 --model <模型目录>\n"
               "  • FunASR(高准度): docker run -p 10095:10095 funasr/funasr:latest\n"
               "默认端点 ws://127.0.0.1:10096/(可在下方字段改)。"),
    fields=[
        ProviderField("endpoint", "WebSocket 端点",
                       default="ws://127.0.0.1:10096/",
                       placeholder="ws://127.0.0.1:10096/ (sherpa-onnx 默认 10096 / "
                                   "funasr 默认 10095 / SenseVoice 10097)"),
        ProviderField("mode", "识别模式", default="2pass",
                       placeholder="2pass / online / offline"),
        # 用户指定:「让用户能通过选这一项自定义使用自己的本地模型,不用写其它厂商和模型」
        # 默认 sherpa-onnx zipformer-zh-14M(54MB,推荐入门);FunASR 留给 GPU 用户
        ProviderField("model", "本地模型", default="sherpa-onnx-zipformer-zh-14M",
                       placeholder="sherpa-onnx-zipformer-zh-14M / paraformer-zh / "
                                   "sensevoice-small / sherpa-onnx-paraformer-zh / llama-funasr-q4"),
        ProviderField("device", "推理设备", default="cpu",
                       placeholder="cpu / cuda:0"),
    ],
    factory=_make_local_funasr,
)

# 本地模型说明字段(model_choices)供前端下拉,避免用户乱写
PROVIDERS["local-funasr"].model_choices = [
    {"id": "sherpa-onnx-zipformer-zh-14M",
     "name": "sherpa-onnx zipformer-zh-14M (54MB int8) · ★推荐",
     "note": "中文 CER 7-8% · CPU 实时因子 0.1 · 启动 0.78s · 无 torch 依赖"},
    {"id": "paraformer-zh", "name": "FunASR-Paraformer-Large (220M)",
     "note": "中文 CER 10.18% · CPU 可跑 · 模型 ~220MB · ⚠ 需独立子进程(本机无模型)"},
    {"id": "sensevoice-small", "name": "FunASR-SenseVoice-Small (234M)",
     "note": "中文 CER 7.81% · 50+语种 · 模型 ~230MB · 多语首选 · ⚠ 需独立子进程"},
    {"id": "sherpa-onnx-paraformer-zh", "name": "sherpa-onnx Paraformer (220M)",
     "note": "跨平台 ONNX · 嵌入式/移动端首选 · 模型 ~220MB"},
    {"id": "llama-funasr-q4", "name": "llama-funasr Q4 GGUF (~400MB)",
     "note": "中文 CER 比 whisper.cpp 低 3 倍 · 需 llama.cpp"},
    {"id": "funasr-nano",   "name": "FunASR-Nano (0.8B,2026 新)",
     "note": "中文 CER 8.06% · 需 ~1.6GB 显存(GPU 旗舰)"},
]


# ============================================================
# 主流云厂商 ASR — spec 列出,factory 给「未实现」友好提示
# 用户看到下拉项就知道"以后会支持",agent 可基于 spec 实现
# ============================================================
def _not_implemented_factory(provider_name: str):
    """占位工厂 — 暂未实现,启动时给清晰提示。"""
    def factory(cfg, callbacks):
        async def on_err(msg):
            if callbacks.get("on_err"):
                await callbacks["on_err"](msg)
        async def start():
            await on_err(
                f"{provider_name} 暂未实现,欢迎贡献代码 "
                f"(companion_asr_providers.py 实现 Protocol 后注册)"
            )
            raise RuntimeError(f"{provider_name} 暂未实现")
        s = _StubSession(start)
        return s
    return factory


class _StubSession:
    """骨架 session — start() 即报错,fail-fast 让用户知道「下拉里这项还跑不起来」。"""
    def __init__(self, start_fn):
        self._start_fn = start_fn
        self._started = False
    async def start(self):
        await self._start_fn()
    async def send_pcm(self, pcm_bytes): pass
    async def finish(self): pass
    async def close(self): pass


# --- Google Cloud Speech-to-Text v2 (streaming) ---
PROVIDERS["gcp-speech-v2"] = ProviderSpec(
    name="gcp-speech-v2",
    display="Google Cloud Speech-to-Text v2 · 英文 WER ~3-4%",
    kind="stream",
    tier="advanced",
    prep_hint=("需在 Google Cloud Console 开 Speech-to-Text API + 创建 Service Account "
               "并下载 JSON credentials 文件,把文件内容粘贴到上方 Key 字段(或填 OAuth access_token)。"),
    fields=[
        ProviderField("api_key", "API Key / OAuth Token", secret=True, required=True,
                       placeholder="ya29.... 或 service account JSON path"),
        ProviderField("language", "主语言 BCP-47", default="zh-CN",
                       placeholder="zh-CN / en-US / ja-JP"),
        ProviderField("region", "区域", default="asia-east1",
                       placeholder="asia-east1 / us-central1"),
        ProviderField("model", "模型", default="latest_long",
                       placeholder="latest_long / latest_short / telephony"),
    ],
    factory=_not_implemented_factory("gcp-speech-v2"),
)

# --- Azure Speech-to-Text (continuous recognition) ---
PROVIDERS["azure-speech"] = ProviderSpec(
    name="azure-speech",
    display="Azure AI Speech(连续识别) · 中文 CER 2.99% / 英文 WER ~3%",
    kind="stream",
    tier="advanced",
    prep_hint=("需在 Azure Portal 创建 Speech resource(免费层可用),从「Keys and Endpoint」"
               "页拿 Key + Region(endpoint 字段已默认 eastasia,Region 不匹配请改)。"),
    fields=[
        ProviderField("api_key", "Speech Key", secret=True, required=True,
                       placeholder="<region>.api.cognitive.microsoft.com key"),
        ProviderField("endpoint", "Region Endpoint",
                       default="wss://eastasia.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1",
                       placeholder="wss://<region>.stt.speech.microsoft.com/..."),
        ProviderField("language", "主语言 BCP-47", default="zh-CN",
                       placeholder="zh-CN / en-US"),
    ],
    factory=_not_implemented_factory("azure-speech"),
)

# --- AWS Transcribe Streaming ---
PROVIDERS["aws-transcribe-streaming"] = ProviderSpec(
    name="aws-transcribe-streaming",
    display="AWS Transcribe Streaming · 英文 WER ~4-5%",
    kind="stream",
    tier="advanced",
    prep_hint=("需在 AWS Console 创建 IAM User + Access Key(开 transcribe:StartStreamTranscribe 权限)。"
               "注:AWS Transcribe Streaming 走 Kinesis Video Streams 推流通道,本机暂未实装 KVS producer,"
               "factory 为占位,实际派发会失败 — 仅做技术预研参考。"),
    fields=[
        ProviderField("api_key", "Access Key ID", secret=True, required=True),
        ProviderField("api_secret", "Secret Access Key", secret=True, required=True),
        ProviderField("region", "AWS Region", default="ap-northeast-1",
                       placeholder="ap-northeast-1 / us-east-1"),
        ProviderField("language", "主语言 BCP-47", default="zh-CN",
                       placeholder="zh-CN / en-US / ja-JP"),
    ],
    factory=_not_implemented_factory("aws-transcribe-streaming"),
)


# ============================================================
# 国内主流 ASR(2026-09-16 加)— 骨架 factory 占位
# 鉴权模型各异(app_id+api_key+secret / app_id+api_key+access_token / access_key 等)
# 端点常含 region,字段里 region 由用户填
# ============================================================

# --- 阿里云智能语音 NLS(传统 SDK,跟百炼不同)---
PROVIDERS["aliyun-nls"] = ProviderSpec(
    name="aliyun-nls",
    display="阿里云智能语音 NLS(传统 SDK)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在阿里云控制台开通智能语音 NLS 服务,创建项目拿 AppKey + 在 RAM 控制台"
               "创建 AccessKey(AK/SK)。百炼用户不用这个,走 paraformer-realtime-v2 即可。"),
    fields=[
        ProviderField("app_key", "AppKey", required=True,
                       placeholder="从阿里云控制台拿"),
        ProviderField("api_key", "AccessKey ID", secret=True, required=True,
                       placeholder="LTAI...."),
        ProviderField("api_secret", "AccessKey Secret", secret=True, required=True),
        ProviderField("region", "区域", default="cn-shanghai",
                       placeholder="cn-shanghai / cn-beijing"),
        ProviderField("model", "模型", default="paraformer-v2",
                       placeholder="paraformer-v2 / paraformer-8k-v2"),
    ],
    factory=_not_implemented_factory("aliyun-nls"),
)

# --- 腾讯云一句话/流式 ASR ---
PROVIDERS["tencent-asr"] = ProviderSpec(
    name="tencent-asr",
    display="腾讯云 ASR(实时识别) · 中文 CER 4.64% (2025 SpeechColab)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在腾讯云控制台 → 语音识别 开服务、创建应用拿 AppID;在 CAM 控制台"
               "创建 API 密钥拿 SecretId/SecretKey(需开 asr 权限)。"),
    fields=[
        ProviderField("app_id", "AppID", required=True,
                       placeholder="腾讯云控制台 → 语音识别"),
        ProviderField("api_key", "SecretId", secret=True, required=True,
                       placeholder="AKID...."),
        ProviderField("api_secret", "SecretKey", secret=True, required=True),
        ProviderField("region", "区域", default="ap-shanghai",
                       placeholder="ap-shanghai / ap-guangzhou / ap-beijing"),
        ProviderField("engine_model_type", "引擎模型",
                       default="16k_zh",
                       placeholder="16k_zh / 16k_zh-PY / 16k_en"),
    ],
    factory=_not_implemented_factory("tencent-asr"),
)

# --- 腾讯混元 Hy ASR 3.0(2026 新)— 旗舰 CER 2.61%,粤语准度媲美普通话 ---
# M3.18:鉴权走 TC3-HMAC-SHA256,在 URL query 加 secretid/timestamp/expired/nonce 签名
#       engine_model_type 真值是 Hy-ASR-3.0-preview(不是 hy-asr-v3)
#       每个连接需唯一 voice_id;仅 16k PCM;首版 ≤60s
PROVIDERS["tencent-hy-asr-v3"] = ProviderSpec(
    name="tencent-hy-asr-v3",
    display="腾讯混元 Hy ASR 3.0 · 中文 CER 2.61% / 粤语 ≈普通话 (2026)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在腾讯云 → 混元大模型 → ASR 申请白名单 + 创建应用拿 AppID。"
               "鉴权走 TC3-HMAC-SHA256,SecretId/SecretKey 同 CAM 控制台。"),
    fields=[
        ProviderField("app_id", "AppID", required=True,
                       placeholder="腾讯云 → 混元大模型 → ASR"),
        ProviderField("api_key", "SecretId", secret=True, required=True,
                       placeholder="AKID...."),
        ProviderField("api_secret", "SecretKey", secret=True, required=True),
        ProviderField("region", "区域", default="ap-shanghai",
                       placeholder="ap-shanghai / ap-guangzhou / ap-beijing"),
        ProviderField("model", "引擎模型 engine_model_type",
                       default="Hy-ASR-3.0-preview",
                       placeholder="Hy-ASR-3.0-preview / 16k_zh"),
    ],
    factory=_not_implemented_factory("tencent-hy-asr-v3"),
)

# --- 讯飞开放平台(流式听写 WebAPI)---
# M3.18:鉴权走 URL query 签名 ——
#   用 APIKey + APISecret 对"host date request-line"做 HMAC-SHA256 → Base64
#   authorization_origin = 'api_key="...",algorithm="hmac-sha256",
#     headers="host date request-line",signature="..."'
#   authorization = Base64(authorization_origin)
#   URL: wss://iat-api.xfyun.cn/v2/iat?authorization=...&host=...&date=...
# 音频 base64 上行,建议每 40ms 一帧,单次 ≤60s
PROVIDERS["xunfei-streaming"] = ProviderSpec(
    name="xunfei-streaming",
    display="科大讯飞 流式听写(开放平台) · 中文 CER 4.80% · 87种方言",
    kind="stream",
    tier="advanced",
    prep_hint=("需在讯飞开放平台完成实名认证 → 创建应用 → 开通「流式听写(WebAPI)」,"
               "拿 APPID + APIKey + APISecret。鉴权用 HMAC-SHA256 拼到 URL query。"),
    fields=[
        ProviderField("app_id", "APPID", required=True,
                       placeholder="讯飞控制台 → 我的应用"),
        ProviderField("api_key", "APIKey(32位)", secret=True, required=True),
        ProviderField("api_secret", "APISecret(32位,用于 HMAC-SHA256 签名)",
                       secret=True, required=True),
        ProviderField("language", "语种 domain", default="iat",
                       placeholder="iat=中文 iat_en=英文"),
        ProviderField("accent", "口音 accent", default="mandarin",
                       placeholder="mandarin / cantonese"),
        ProviderField("endpoint", "WebSocket 端点",
                       default="wss://iat-api.xfyun.cn/v2/iat",
                       placeholder="wss://iat-api.xfyun.cn/v2/iat"),
    ],
    factory=_not_implemented_factory("xunfei-streaming"),
)

# --- 百度智能云短语音/实时 ---
# M3.18:鉴权两步 ——
#   1) api_key + api_secret → POST /oauth/2.0/token 换 access_token(30 天过期)
#   2) WSS 第一帧 JSON 带 dev_pid + token + cuid + format + rate + channel
# cuid 通常用 mac/设备 id/用户自定义;让用户填一个稳定字符串
PROVIDERS["baidu-asr-streaming"] = ProviderSpec(
    name="baidu-asr-streaming",
    display="百度智能云 实时语音识别 · 中文 CER 10.10%",
    kind="stream",
    tier="advanced",
    prep_hint=("需在百度智能云 → 语音技术 开通「短语音/实时」+ 创建应用拿 API Key / Secret Key。"
               "鉴权走两步:先用 AK/SK 换 access_token(30 天过期),再带 cuid 上行 WSS。"
               "cuid 字段可留默认,任意稳定字符串即可。"),
    fields=[
        ProviderField("app_id", "AppID", required=True,
                       placeholder="百度云 → 语音技术"),
        ProviderField("api_key", "API Key", secret=True, required=True),
        ProviderField("api_secret", "Secret Key", secret=True, required=True),
        ProviderField("dev_pid", "模型 ID", default="1537",
                       placeholder="1537=普通话 1737=英语 1637=粤语 1837=四川话"),
        ProviderField("cuid", "设备唯一 ID (cuid)", default="prisiragent-companion",
                       placeholder="任意稳定字符串,mac/设备 sn 都行"),
        ProviderField("endpoint", "服务 URL",
                       default="wss://vop.baidu.com/realtime_asr",
                       placeholder="wss://vop.baidu.com/realtime_asr"),
    ],
    factory=_not_implemented_factory("baidu-asr-streaming"),
)

# --- 字节跳动火山引擎 ASR(传统流式,v2 端点)---
# M3.18:鉴权走 WS header ——
#   旧控制台: Bearer;{TOKEN} + AppID + CLUSTER
#   新控制台: X-Api-Key + 可选 X-Api-Resource-Id
# 端点分 v2 通用流式(本条目)与 v3 大模型(doubao-seed-asr 条目)
PROVIDERS["volcengine-asr"] = ProviderSpec(
    name="volcengine-asr",
    display="字节火山引擎 流式语音识别(v2 传统流式)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在火山引擎 → 语音技术 开通「流式语音识别(传统版 v2)」+ 创建应用拿 AppID + Token。"
               "鉴权走 WS header(旧版 Bearer;{TOKEN} 或新版 X-Api-Key),cluster 字段按控制台给的填。"),
    fields=[
        ProviderField("app_id", "AppID", required=True,
                       placeholder="火山控制台 → 语音技术"),
        ProviderField("api_key", "Token(旧版 Bearer;{TOKEN} 或新版 X-Api-Key)",
                       secret=True, required=True,
                       placeholder="Token / API Key"),
        # 旧版 cluster 是必填;新版可选填 resource_id
        ProviderField("cluster", "Cluster(旧版)/Resource-Id(新版)",
                       default="volcengine_streaming_common",
                       placeholder="volcengine_streaming_common / "
                                   "volc.bigasr.sauc.duration"),
        ProviderField("endpoint", "WebSocket 端点",
                       default="wss://openspeech.bytedance.com/api/v2/asr",
                       placeholder="wss://openspeech.bytedance.com/api/v2/asr"),
    ],
    factory=_not_implemented_factory("volcengine-asr"),
)

# --- 字节豆包 Seed-ASR(2026 新)— LLM 架构推理级 ASR ---
# M3.18:真模型名 doubao-seed-asr;新版方舟走 X-Api-Key header + bigmodel_async 集群
#       (旧控制台走 X-Api-App-Key + X-Api-Access-Key + X-Api-Resource-Id 三头)
#       端点也分 v2 通用 vs v3 大模型双向流式
PROVIDERS["doubao-seed-asr"] = ProviderSpec(
    name="doubao-seed-asr",
    display="字节豆包 Seed-ASR · LLM 架构推理级 · 中文 CER ~2.5% (2026)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在火山引擎 → 方舟(Ark)控制台开通 Seed-ASR + 创建 API Key。"
               "新版方舟走 X-Api-Key header + bigmodel_async 集群;旧控制台三头鉴权已合并,留作可选。"),
    fields=[
        ProviderField("api_key", "API Key (X-Api-Key)", secret=True, required=True,
                       placeholder="新版方舟控制台拿 → 直接当 Bearer 用"),
        # M3.18:app_id 在新版方舟体系已合并到 api_key 概念;留作可选兼容旧版
        ProviderField("app_id", "AppID(仅旧版用)", required=False,
                       placeholder="旧控制台 → 语音技术"),
        ProviderField("cluster", "推理集群", default="bigmodel_async",
                       placeholder="bigmodel_async / doubao-seed-asr"),
        ProviderField("endpoint", "WebSocket 端点",
                       default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
                       placeholder="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"),
        ProviderField("model", "模型名", default="doubao-seed-asr",
                       placeholder="doubao-seed-asr"),
    ],
    factory=_not_implemented_factory("doubao-seed-asr"),
)

# --- 华为云语音(SIS)---
PROVIDERS["huawei-speech"] = ProviderSpec(
    name="huawei-speech",
    display="华为云语音 SIS(一句话/连续)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在华为云控制台 → 语音交互服务 SIS 实名认证 + 创建项目拿 Access Key(AK/SK)。"
               "endpoint 字段已默认 cn-north-4,其他区域请按格式改。"),
    fields=[
        ProviderField("endpoint", "终端节点",
                       default="https://sis-ext.cn-north-4.myhuaweicloud.com",
                       placeholder="https://sis-ext.<region>.myhuaweicloud.com"),
        ProviderField("api_key", "AK", secret=True, required=True,
                       placeholder="Access Key ID"),
        ProviderField("api_secret", "SK", secret=True, required=True,
                       placeholder="Secret Access Key"),
        ProviderField("region", "区域", default="cn-north-4",
                       placeholder="cn-north-4 / cn-east-3"),
        ProviderField("property", "属性", default="chinese_8k_common",
                       placeholder="chinese_8k_common / english_8k_common"),
    ],
    factory=_not_implemented_factory("huawei-speech"),
)

# --- 京东云语音 ---
PROVIDERS["jd-speech"] = ProviderSpec(
    name="jd-speech",
    display="京东云 语音识别(ASR)",
    kind="stream",
    tier="advanced",
    prep_hint=("需在京东云控制台 → 语音识别 开通服务 + 创建 AccessKey/SecretKey 对(开 ASR 权限)。"),
    fields=[
        ProviderField("api_key", "Access Key", secret=True, required=True),
        ProviderField("api_secret", "Secret Key", secret=True, required=True),
        ProviderField("region", "区域", default="cn-north-1",
                       placeholder="cn-north-1 / cn-east-1"),
        ProviderField("language", "语种", default="zh",
                       placeholder="zh / en"),
    ],
    factory=_not_implemented_factory("jd-speech"),
)


# ============================================================
# settings.json 读写(data_dir/settings.json)
# ============================================================
DEFAULT_SETTINGS = {
    "active_provider": "bailian-paraformer-v2",
    # M3.17.3 — 兜底链(主 → 备)
    "fallback_chain": [],
    # M3.17.3 — 隐私开关:本地 provider 故障时是否允许自动切云端
    # 默认 False(尊重隐私,只弹窗让用户手动切)
    "allow_cloud_fallback": False,
    # M3.27 — 派发到 PrisirAI 开关(默认开,关闭后 /api/dispatch 返 403)
    "enable_dispatch": True,
    # M3.27.1 — 派发时附带知识库命中(snippet 500 字,默认开)
    "dispatch_include_knowledge": True,
    # M3.27.2 — 启动时若本地 ASR 可达 → 强制用它(降云端 key 依赖)
    # 用户可在 settings 里关掉 → 尊重用户偏好
    "asr_auto_local_first": True,
    "providers": {
        # 不在文件里存 api_key 明文 → 默认值走 env(启动时 resolve)
        # 用户在 UI 填了 key 后才写进文件
        "bailian-paraformer-v2": {
            # "api_key": "...",  # 用户填了才存
            "sample_rate": 16000,
            "language_hints": ["zh", "en"],
        },
    },
}


def _settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_settings(data_dir: Path) -> dict:
    p = _settings_path(data_dir)
    if not p.is_file():
        return json.loads(json.dumps(DEFAULT_SETTINGS))  # 深 copy
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("settings.json 解析失败,回退默认")
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    # 补齐缺失字段
    if "active_provider" not in s:
        s["active_provider"] = DEFAULT_SETTINGS["active_provider"]
    if "providers" not in s:
        s["providers"] = {}
    for name, default_cfg in DEFAULT_SETTINGS["providers"].items():
        s["providers"].setdefault(name, default_cfg)
    # M3.27 — 旧 settings.json 缺 enable_dispatch 时补 True
    s.setdefault("enable_dispatch", DEFAULT_SETTINGS["enable_dispatch"])
    # M3.27.1 — 旧 settings.json 缺 dispatch_include_knowledge 时补 True
    s.setdefault("dispatch_include_knowledge",
                 DEFAULT_SETTINGS["dispatch_include_knowledge"])
    # M3.27.2 — 旧 settings.json 缺 asr_auto_local_first 时补 True
    s.setdefault("asr_auto_local_first",
                 DEFAULT_SETTINGS["asr_auto_local_first"])
    return s


def save_settings(data_dir: Path, settings: dict) -> None:
    p = _settings_path(data_dir)
    p.write_text(json.dumps(settings, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def resolve_provider_cfg(data_dir: Path, provider_name: Optional[str] = None) -> tuple[str, dict]:
    """取 (provider_name, cfg)。env 注入默认 api_key(若 settings 没存)。
    返回的 cfg 可直接喂给 factory。
    """
    s = load_settings(data_dir)
    name = provider_name or s.get("active_provider", "bailian-paraformer-v2")
    cfg = dict(s.get("providers", {}).get(name, {}))
    spec = PROVIDERS.get(name)
    if spec is None:
        return name, cfg
    # 把 spec fields 的 default 补齐(env 优先)
    for f in spec.fields:
        if f.key not in cfg or cfg[f.key] == "":
            if f.key == "api_key":
                cfg[f.key] = os.environ.get("BAILIAN_API_KEY", "")
            else:
                cfg[f.key] = f.default
    return name, cfg


# ============================================================
# ASR 兜底链(M3.17.3)
# ============================================================
# 设计(参 docs/M3.17-paraformer-zh-design.md 4c):
#   - 默认策略 2(尊重隐私):用户主选 local-funasr,故障弹窗让用户手动切云端
#   - 允许 ASR 联网兑底(allow_cloud_fallback=true)→ 策略 1:本地故障 5s 无响应
#     自动跳云端 bailian-paraformer-v2
#   - fallback_chain 是按顺序的 provider name 列表,从 settings["fallback_chain"]
#     读,默认 [active_provider, bailian-paraformer-v2]
# ============================================================
DEFAULT_FALLBACK_TARGETS = ["bailian-paraformer-v2"]


def resolve_fallback_chain(data_dir: Path) -> list[str]:
    """返 [primary, secondary, ...] provider name 列表。

    - settings["fallback_chain"] 有 → 用之(去重,保留 active_provider 在第一)
    - 否则 → [active_provider, bailian-paraformer-v2](云端兜底)
    - 若 allow_cloud_fallback=False 且 primary 不是 cloud → 返 [primary](不兑底)
    """
    s = load_settings(data_dir)
    primary = s.get("active_provider", "bailian-paraformer-v2")
    allow_cloud = bool(s.get("allow_cloud_fallback", False))
    chain_raw = s.get("fallback_chain")
    if chain_raw and isinstance(chain_raw, list):
        # 用 user 配置
        seen = {primary}
        out = [primary]
        for n in chain_raw:
            if n not in seen and n in PROVIDERS:
                seen.add(n)
                out.append(n)
        return out
    # 默认:primary 后挂 bailian-paraformer-v2(若 allow_cloud or primary 已是云端)
    primary_spec = PROVIDERS.get(primary)
    is_local_primary = bool(primary_spec and primary.startswith("local-"))
    if not allow_cloud and is_local_primary:
        # 用户隐私优先 → 不兑底云端
        return [primary]
    # dedup:若 primary 已在 DEFAULT_FALLBACK_TARGETS 里,别重复加
    extras = [t for t in DEFAULT_FALLBACK_TARGETS if t != primary and t in PROVIDERS]
    return [primary] + extras


class AsrStartError(Exception):
    """provider start() 失败的具体错误(含 provider name + 原因)。"""

    def __init__(self, provider_name: str, phase: str, cause: BaseException):
        super().__init__(f"{provider_name} [{phase}]: {type(cause).__name__}: {cause}")
        self.provider_name = provider_name
        self.phase = phase
        self.cause = cause


async def try_start_provider(provider_name: str, cfg: dict,
                              callbacks: dict, *,
                              started_timeout: float = 8.0) -> object:
    """包装 create_session + start() + 等 task-started。
    返 session 句柄(已 _started=True)。
    抛 AsrStartError(失败原因)。
    """
    try:
        asr = create_session(provider_name, cfg, callbacks)
    except Exception as e:  # noqa: BLE001
        raise AsrStartError(provider_name, "create", e) from e
    try:
        await asr.start()
    except Exception as e:  # noqa: BLE001
        raise AsrStartError(provider_name, "connect", e) from e
    # 等 task-started / ready 上限 N 秒
    loop_deadline = started_timeout
    step = 0.05
    waited = 0.0
    while waited < loop_deadline:
        if getattr(asr, "_started", False):
            return asr
        await asyncio.sleep(step)
        waited += step
    # 超时关掉,别留半截 ws
    try:
        await asr.close()
    except Exception:  # noqa: BLE001
        pass
    raise AsrStartError(provider_name, "timeout",
                        RuntimeError(f"task-started 超时 {started_timeout}s"))


def public_settings(data_dir: Path) -> dict:
    """给前端 GET:展示当前设置,但 secret 字段(mask)。"""
    s = load_settings(data_dir)
    active = s.get("active_provider", "bailian-paraformer-v2")
    out_providers = {}
    for name, cfg in s.get("providers", {}).items():
        spec = PROVIDERS.get(name)
        if spec is None:
            out_providers[name] = cfg
            continue
        masked = {}
        for f in spec.fields:
            v = cfg.get(f.key, f.default)
            if f.secret and v:
                # mask 中间,留首尾 4 字
                masked[f.key] = v[:4] + "…" + v[-4:] if len(v) > 12 else "***"
            else:
                masked[f.key] = v
        out_providers[name] = masked
    return {
        "active_provider": active,
        "providers": out_providers,
        "fallback_chain": resolve_fallback_chain(data_dir),
        "allow_cloud_fallback": bool(s.get("allow_cloud_fallback", False)),
    }

# ============================================================
