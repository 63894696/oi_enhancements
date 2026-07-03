"""Profile 感知的 WebSocket ASR — 复用 oi_client.StreamASRSession"""
from __future__ import annotations

import sys
from pathlib import Path

_CLIENTS_DIR = Path(__file__).resolve().parents[1] / "voice_input" / "clients"
if _CLIENTS_DIR.is_dir() and str(_CLIENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENTS_DIR))

from oi_client import StreamASRSession  # noqa: E402


class ProfileStreamASRSession(StreamASRSession):
    """按 shell.yaml 中 profile 的 ASR 端口连接 WS"""

    def __init__(
        self,
        auth_token: str,
        asr_port: int,
        host: str = "127.0.0.1",
        wake_words=None,
    ):
        super().__init__(auth_token, wake_words=wake_words)
        self._uri = f"ws://{host}:{asr_port}/ws?token={auth_token}"
