"""OI 侧 agent_state 钩子 — POST /agent_state thinking/speaking 等"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client import OrchestratorClient


class AgentStateHooks:
    """供 OI / perception / router 进程调用,驱动 Agent Shell UI"""

    def __init__(
        self,
        orch_port: int = 8730,
        home: Optional[str] = None,
        host: str = "127.0.0.1",
    ):
        from pathlib import Path

        self.client = OrchestratorClient(
            orch_port=orch_port,
            home=home or str(Path.home() / ".voice_input"),
            host=host,
        )

    def set(self, state: str, detail: str = "", **kwargs: Any) -> Dict[str, Any]:
        return self.client.set_agent_state(state, detail=detail, **kwargs)

    def thinking(self, detail: str = "OI 推理中") -> Dict[str, Any]:
        return self.set("thinking", detail, can_interrupt=True)

    def speaking(self, detail: str = "TTS 播报") -> Dict[str, Any]:
        return self.set("speaking", detail, can_wake=False, can_interrupt=True)

    def busy(self, detail: str = "") -> Dict[str, Any]:
        return self.set("busy", detail)

    def idle(self, detail: str = "") -> Dict[str, Any]:
        return self.set("idle", detail, can_wake=True, can_interrupt=False)

    def degraded(self, from_profile: str, detail: str = "") -> Dict[str, Any]:
        return self.set(
            "degraded",
            detail or f"已从 {from_profile} 降级",
            fastlane_degraded_from=from_profile,
        )

    def blocked(self, reason: str, detail: str = "") -> Dict[str, Any]:
        return self.set(
            "blocked",
            detail or reason,
            can_wake=False,
            ghostline_blocked_reason=reason,
        )

    def snapshot(self) -> Dict[str, Any]:
        return self.client.get_agent_state()
