"""
prisir_work/edge_tts_client.py — Edge TTS 免费 TTS 客户端(Phase 12 OM-P4, 2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

## 定位
Microsoft Edge 公开 TTS 服务,通过 edge-tts 库(已装 7.2.8)调用。
- **零依赖 / 免 key / 免费** — Microsoft 公开 WebSocket,不走 OAuth
- **中文质量好** — zh-CN-XiaoxiaoNeural / zh-CN-YunxiNeural / zh-CN-YunjianNeural 等 7 个中文 voice
- **真集成** — 不 mock,真跑 WebSocket 拿 mp3

## 关键 API
  - synthesize(text, output_path, voice='zh-CN-XiaoxiaoNeural', rate='+0%') → Result
  - list_chinese_voices() → list[str]  (中文 voice 全集)
  - is_available() → bool  (无 key 探测,直接判库是否可导入)

## 与 video_creator 集成
TtsCreator 默认走 edge_tts(无 key 时),有 key 时走 cosyvoice2/elevenlabs。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "TTSResult",
    "synthesize",
    "synthesize_sync",
    "list_chinese_voices",
    "is_available",
    "DEFAULT_VOICE",
]

log = logging.getLogger("prisir_work.edge_tts_client")


# 默认中文 voice(女声,Xiaoxiao 是微软最热门中文 TTS)
DEFAULT_VOICE: str = "zh-CN-XiaoxiaoNeural"


# 中文 voice 全集(2026-09-28 edge-tts 7.2.8)
CHINESE_VOICES: list[str] = [
    "zh-CN-XiaoxiaoNeural",   # 女声 · 温柔
    "zh-CN-YunxiNeural",      # 男声 · 青年
    "zh-CN-YunjianNeural",    # 男声 · 浑厚
    "zh-CN-XiaoyiNeural",     # 女声 · 活泼
    "zh-CN-YunyangNeural",    # 男声 · 新闻播报
    "zh-CN-XiaomengNeural",   # 女声 · 儿童
    "zh-CN-XiaomoNeural",     # 女声 · 情感
    "zh-HK-HiuMaanNeural",    # 粤语 · 女
    "zh-TW-HsiaoChenNeural",  # 台湾 · 女
]


@dataclass
class TTSResult:
    """合成结果。"""
    ok: bool
    output_path: str = ""
    size_bytes: int = 0
    voice: str = ""
    text_length: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output_path": self.output_path,
            "size_bytes": self.size_bytes,
            "voice": self.voice,
            "text_length": self.text_length,
            "error": self.error,
        }


def is_available() -> bool:
    """检测 edge-tts 库是否可导入。"""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def list_chinese_voices() -> list[str]:
    """返所有中文 voice 名。"""
    return list(CHINESE_VOICES)


async def synthesize(text: str, output_path: str,
                     voice: str = DEFAULT_VOICE,
                     rate: str = "+0%",
                     pitch: str = "+0Hz") -> TTSResult:
    """异步合成 TTS → mp3 文件。

    Args:
        text: 中文/英文文本(中文推荐)
        output_path: 输出 .mp3 文件路径
        voice: 中文 voice 名(默认 zh-CN-XiaoxiaoNeural)
        rate: 语速(±N%,默认 +0%)
        pitch: 音调(±NHz,默认 +0Hz)
    """
    try:
        import edge_tts
    except ImportError as e:
        return TTSResult(ok=False, error=f"edge-tts 未装: {e}")

    if not text or not text.strip():
        return TTSResult(ok=False, error="text 为空")

    # 父目录不存在则创建
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    try:
        comm = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        await comm.save(output_path)
        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if size == 0:
            return TTSResult(ok=False, error="生成文件 0 字节", voice=voice)
        return TTSResult(
            ok=True,
            output_path=output_path,
            size_bytes=size,
            voice=voice,
            text_length=len(text),
        )
    except Exception as e:
        return TTSResult(ok=False, error=f"{type(e).__name__}: {e}",
                         voice=voice, text_length=len(text))


def synthesize_sync(text: str, output_path: str,
                    voice: str = DEFAULT_VOICE,
                    rate: str = "+0%",
                    pitch: str = "+0Hz") -> TTSResult:
    """同步版本(内部跑 asyncio.run)。

    用法:在非异步代码里直接调 synthesize_sync("你好", "/tmp/test.mp3")。
    """
    try:
        return asyncio.run(
            synthesize(text, output_path, voice=voice, rate=rate, pitch=pitch)
        )
    except RuntimeError as e:
        # 已在 event loop 内 → 返失败,让调用方走异步
        return TTSResult(ok=False, error=f"event loop 内请用 synthesize(): {e}")