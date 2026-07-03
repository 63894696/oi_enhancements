"""后台轮询 orchestrator /agent_state"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from .client import OrchestratorClient
from .health_snap import merge_health_into_snap


class StatePoller:
    def __init__(
        self,
        client: OrchestratorClient,
        interval_ms: int = 500,
        on_update: Optional[Callable[[dict], None]] = None,
        profile_name: str = "base",
    ):
        self.client = client
        self.profile_name = profile_name
        self.interval_s = max(0.1, interval_ms / 1000.0)
        self.on_update = on_update
        self._snap: dict = {"state": "offline", "detail": "启动中"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="agent-shell-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh_once(self) -> dict:
        health = self.client.health()
        if health.get("status") not in ("ok", "muted"):
            snap = {
                "state": "offline",
                "detail": health.get("error") or f"health={health.get('status')}",
                "can_wake": False,
                "can_interrupt": False,
                "mute_gate_active": False,
                "profile": self.profile_name,
            }
        else:
            snap = self.client.get_agent_state()
            snap = merge_health_into_snap(self.profile_name, health, snap)
        with self._lock:
            self._snap = snap
        if self.on_update:
            self.on_update(snap)
        return snap

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self.interval_s)
