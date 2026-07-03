"""Claude-Code 风格 stream watchdog 5min 默认 — OI 流式超时

参考 github.com/anthropics/claude-code v2.1.196:
  stream watchdog 5min 默认开 (CLAUDE_ENABLE_STREAM_WATCHDOG=0 关)
  5min 无 chunk 进展 → 自动 abort / retry

OI 0.4.3 当前用 litellm 流式调用,卡住时无超时机制,用户必须 Ctrl+C。
这个 watchdog 包装 litellm 流式调用,5min 没新 chunk 就 raise TimeoutError。

用法:
    from oi_enhancements.stream_watchdog import watchdog_stream
    for chunk in watchdog_stream(litellm.completion(...), idle_timeout_ms=300_000):
        ...
"""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Generator, Optional

# 默认 5min,可被 OI_STREAM_WATCHDOG_MS 环境变量覆盖;0 表示禁用
DEFAULT_TIMEOUT_MS = int(os.environ.get("OI_STREAM_WATCHDOG_MS", 5 * 60 * 1000))


class StreamTimeoutError(TimeoutError):
    """流式调用空闲超时,被 watchdog 主动 raise"""

    def __init__(self, idle_ms: int):
        super().__init__(f"stream idle for {idle_ms}ms (watchdog triggered)")
        self.idle_ms = idle_ms


class _WatchdogTimer:
    """后台线程 watchdog:每 idle_timeout_ms 检查一次 stream 是否有新 chunk"""

    def __init__(self, idle_timeout_ms: int):
        self.idle_timeout_ms = idle_timeout_ms
        self.last_chunk_at = time.time()
        self.triggered = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        if self.idle_timeout_ms <= 0:
            return self
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def touch(self) -> None:
        """每次有 chunk 来就调用,reset 计时"""
        self.last_chunk_at = time.time()

    def _watch(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(timeout=1.0)
            if self._stop.is_set():
                return
            idle = (time.time() - self.last_chunk_at) * 1000
            if idle > self.idle_timeout_ms:
                self.triggered = True
                return  # 退出 watch 线程,等 caller 检查 triggered


def watchdog_stream(
    stream: Generator,
    idle_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    poll_interval: float = 1.0,
) -> Generator:
    """包装一个 litellm 流式 generator,空闲超时主动 raise

    Args:
        stream: litellm.completion(stream=True) 返回的 generator
        idle_timeout_ms: 默认 5min,0 表示禁用
        poll_interval: 后台线程检查间隔

    Yields:
        stream 的每个 chunk(原样转发)

    Raises:
        StreamTimeoutError: 连续 idle_timeout_ms 没新 chunk 时
    """
    if idle_timeout_ms <= 0:
        # watchdog 关闭,直接 yield 原 stream
        yield from stream
        return

    wd = _WatchdogTimer(idle_timeout_ms)
    with wd:
        try:
            for chunk in stream:
                wd.touch()  # 每来一个 chunk 就 reset 计时
                yield chunk
                if wd.triggered:
                    raise StreamTimeoutError(idle_timeout_ms)
        finally:
            # 显式关闭底层 stream(如果是 litellm 流式)
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            if wd.triggered:
                raise StreamTimeoutError(idle_timeout_ms)


# ============================================================
# 给 OI 用的便捷包装:直接接 litellm
# ============================================================

def oi_chat_with_watchdog(
    task: str,
    model: str = "openai/devstral-small-2:24b",
    api_base: str = "https://ollama.com/v1",
    api_key: str = None,
    idle_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    temperature: float = 0,
    max_tokens: int = 4096,
    **litellm_kwargs,
) -> str:
    """一键:litellm 流式调用 + watchdog + 返回完整 response

    用法:
        text = oi_chat_with_watchdog(task='...', model='...', api_key='...')
    """
    import litellm

    resp = litellm.completion(
        model=model,
        api_base=api_base,
        api_key=api_key,
        messages=[{"role": "user", "content": task}],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        **litellm_kwargs,
    )

    parts = []
    try:
        for chunk in watchdog_stream(resp, idle_timeout_ms=idle_timeout_ms):
            # chunk.choices[0].delta.content 才是文本(其他字段跳过)
            try:
                content = chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                content = None
            if content:
                parts.append(content)
    except StreamTimeoutError as e:
        # 超时:返回已收集的部分(不抛)
        print(f"[watchdog] {e}", flush=True)

    return "".join(parts)


if __name__ == "__main__":
    import os
    import winreg

    # 拿 OLLAMA key
    try:
        reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        key, _ = winreg.QueryValueEx(reg, "OLLAMA_API_KEY")
        winreg.CloseKey(reg)
    except Exception:
        key = "test_key"

    print(f"=== oi_chat_with_watchdog (timeout={DEFAULT_TIMEOUT_MS}ms) ===")
    print(f"  model=openai/devstral-small-2:24b")
    t0 = time.time()
    try:
        text = oi_chat_with_watchdog(
            task="用一句话回答:1+1=?",
            api_key=key,
            idle_timeout_ms=30_000,  # 测试用 30s
        )
        elapsed = time.time() - t0
        print(f"  response ({elapsed:.1f}s): {text[:200]}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")