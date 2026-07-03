"""PTT final → DynamicRouter / inject / speak 管线(S3)"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from .client import OrchestratorClient
from .hooks import AgentStateHooks

log = logging.getLogger("agent_shell.oi_pipeline")

_OI_ROOT = Path(__file__).resolve().parents[1]
if str(_OI_ROOT) not in sys.path:
    sys.path.insert(0, str(_OI_ROOT))


class VoicePipeline:
    """语音识别结果进入 OI 增强层。"""

    def __init__(
        self,
        profile_name: str,
        client: OrchestratorClient,
        hooks: Optional[AgentStateHooks] = None,
        auto_route: bool = True,
        auto_respond: bool = True,
        on_result: Optional[Callable[[str], None]] = None,
    ):
        self.profile_name = profile_name
        self.client = client
        self.hooks = hooks or AgentStateHooks(
            orch_port=int(client.base_url.rsplit(":", 1)[-1]),
        )
        self.auto_route = auto_route
        self.auto_respond = auto_respond
        self.on_result = on_result
        self._busy = threading.Lock()

    def handle_final(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self._busy.acquire(blocking=False):
            log.info("管线忙,跳过: %s", text[:20])
            return
        threading.Thread(
            target=self._run,
            args=(text,),
            name="agent-voice-pipeline",
            daemon=True,
        ).start()

    def _run(self, text: str) -> None:
        try:
            self.hooks.thinking(f"处理: {text[:28]}")
            result = self._route(text)
            if self.auto_respond:
                self._respond(result)
            self.hooks.idle(result[:48] if result else "完成")
            if self.on_result:
                self.on_result(result)
        except Exception as e:
            log.exception("VoicePipeline 失败")
            self.hooks.idle(f"管线错误: {e}"[:48])
        finally:
            self._busy.release()

    def _route(self, text: str) -> str:
        if not self.auto_route:
            return text
        try:
            from dynamic_router.router import DynamicRouter

            router = DynamicRouter(hub_name="oi_hub")
            return asyncio.run(router.route(text))
        except Exception as e:
            log.warning("DynamicRouter 不可用,回退原文: %s", e)
            return text

    def _respond(self, text: str) -> None:
        if not text.strip():
            return
        presence = self.client.get_agent_state().get("presence", "voice")
        if presence in ("text", "auto"):
            self.client.inject_text(text)
        else:
            self.client.speak(text)
