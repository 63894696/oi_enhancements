"""PTT → WebSocket 流式 ASR(S2)"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from .asr_stream import ProfileStreamASRSession

log = logging.getLogger("agent_shell.stream_ptt")

SAMPLE_RATE = 16000


class PTTStreamController:
    """按住 PTT 时开 mic + WS; 松开时 stop 并等 final"""

    def __init__(
        self,
        asr_port: int,
        auth_token: str,
        host: str = "127.0.0.1",
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.asr_port = asr_port
        self.auth_token = auth_token
        self.host = host
        self.on_partial = on_partial or (lambda _t: None)
        self.on_final = on_final or (lambda _t: None)
        self.on_error = on_error or (lambda _m: None)

        self._session: Optional[ProfileStreamASRSession] = None
        self._mic_stream = None
        self._reader: Optional[threading.Thread] = None
        self._active = False
        self._ending = False
        self._final_sent = False
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._active

    def begin(self) -> None:
        with self._lock:
            if self._active:
                return
            self._ending = False
            self._final_sent = False
            self._session = ProfileStreamASRSession(self.auth_token, self.asr_port, host=self.host)
            try:
                self._session.start(timeout=8.0)
            except Exception as e:
                self._session = None
                self.on_error(f"WS 连接失败: {e}")
                return

            try:
                import sounddevice as sd
            except ImportError:
                self._cleanup_session()
                self.on_error("sounddevice 未安装,无法采集 mic")
                return

            session = self._session

            def _callback(indata, _frames, _time, _status):
                if session and self._active and not self._ending:
                    session.push_audio(bytes(indata))

            try:
                self._mic_stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=512,
                    callback=_callback,
                )
                self._mic_stream.start()
            except Exception as e:
                self._cleanup_session()
                self.on_error(f"mic 打开失败: {e}")
                return

            self._active = True
            self._reader = threading.Thread(target=self._read_loop, name="ptt-asr-reader", daemon=True)
            self._reader.start()
            log.info("PTT 流式 ASR 已开始 (port=%s)", self.asr_port)

    def end(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._ending = True
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    pass
                self._mic_stream = None
            if self._session is not None:
                try:
                    self._session.stop()
                except Exception:
                    pass
            log.info("PTT 松开,等待 final…")

    def _read_loop(self) -> None:
        session = self._session
        if session is None:
            return
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                ev = session._event_queue.get(timeout=0.3)
            except queue.Empty:
                if self._ending and self._final_sent:
                    break
                if self._ending and time.time() > deadline - 5:
                    break
                continue
            if ev is None:
                break
            et = ev.get("type")
            if et == "partial":
                text = (ev.get("text") or "").strip()
                if text:
                    self.on_partial(text)
            elif et == "final":
                text = (ev.get("text") or "").strip()
                self._final_sent = True
                self.on_final(text)
                break
            elif et == "error":
                self.on_error(ev.get("message") or "ASR error")
                break
        self._cleanup_session()

    def _cleanup_session(self) -> None:
        with self._lock:
            self._active = False
            self._ending = False
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    pass
                self._mic_stream = None

    def stop(self) -> None:
        self.end()
        self._cleanup_session()
