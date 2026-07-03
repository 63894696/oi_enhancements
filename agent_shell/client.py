"""Orchestrator HTTP 客户端 — 轮询 /agent_state、切 presence"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import httpx


class OrchestratorClient:
    def __init__(self, orch_port: int, home: str, host: str = "127.0.0.1", timeout: float = 3.0):
        self.base_url = f"http://{host}:{orch_port}"
        self.timeout = timeout
        self._token = self._read_token(Path(home) / "auth_token")

    @staticmethod
    def _read_token(path: Path) -> str:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def _auth_headers(self) -> Dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    def health(self) -> Dict[str, Any]:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            return r.json() if r.status_code == 200 else {"status": "down", "http_code": r.status_code}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    def get_agent_state(self) -> Dict[str, Any]:
        try:
            r = httpx.get(f"{self.base_url}/agent_state", timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {
            "state": "offline",
            "presence": "voice",
            "profile": "unknown",
            "can_wake": False,
            "can_interrupt": False,
            "mute_gate_active": False,
            "detail": "orchestrator 不可达",
        }

    def set_agent_state(
        self,
        state: str,
        detail: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"state": state, "detail": detail}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        try:
            r = httpx.post(
                f"{self.base_url}/agent_state",
                headers=self._auth_headers(),
                json=body,
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return self.get_agent_state()

    def cycle_presence(self) -> Optional[str]:
        try:
            r = httpx.get(f"{self.base_url}/presence", timeout=self.timeout)
            if r.status_code != 200:
                return None
            cur = r.json().get("mode", "voice")
            order = ["voice", "text", "auto"]
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "voice"
            r2 = httpx.post(
                f"{self.base_url}/presence",
                headers=self._auth_headers(),
                json={"mode": nxt},
                timeout=self.timeout,
            )
            if r2.status_code == 200:
                return r2.json().get("mode")
        except Exception:
            pass
        return None

    def inject_text(self, text: str) -> Dict[str, Any]:
        try:
            r = httpx.post(
                f"{self.base_url}/inject",
                headers=self._auth_headers(),
                json={"text": text},
                timeout=self.timeout,
            )
            return r.json() if r.status_code == 200 else {"status": "error", "http_code": r.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def speak(self, text: str, interrupt: bool = True) -> Dict[str, Any]:
        try:
            r = httpx.post(
                f"{self.base_url}/speak",
                headers=self._auth_headers(),
                json={"text": text, "interrupt": interrupt},
                timeout=max(self.timeout, 30.0),
            )
            return r.json() if r.status_code == 200 else {"status": "error", "http_code": r.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}
