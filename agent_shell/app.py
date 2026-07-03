"""Agent Shell 主应用 — 组装轮询 / 热键 / UI / 流式 ASR"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .client import OrchestratorClient
from .config import (
    get_active_profile,
    get_orb_position,
    load_config,
    save_orb_position,
    set_active_profile,
)
from .display import format_status_line, state_color
from .floating_orb import FloatingOrb
from .hooks import AgentStateHooks
from .hotkeys import HotkeyManager
from .oi_pipeline import VoicePipeline
from .poller import StatePoller
from .ptt import PushToTalkListener
from .status_bar import StatusBar
from .stream_ptt import PTTStreamController
from .tray import TrayController

log = logging.getLogger("agent_shell")


class AgentShellApp:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or load_config()
        self.profile = get_active_profile(self.cfg)
        self.client = self._make_client(self.profile)
        ui = self.cfg.get("ui", {})
        self._poll_ms = int(ui.get("poll_interval_ms", 500))
        self._use_orb = bool(ui.get("floating_orb", True))
        self._use_bar = bool(ui.get("status_bar", False))
        self._use_tray = bool(ui.get("tray", True))
        self._bar_height = int(ui.get("status_bar_height", 10))
        self._orb_size = int(ui.get("orb", {}).get("size", 56))
        self._stream_asr = bool(ui.get("stream_asr", True))
        pl = self.cfg.get("pipeline", {})
        self._auto_route = bool(pl.get("auto_route", True))
        self._auto_respond = bool(pl.get("auto_respond", True))

        self._poller: Optional[StatePoller] = None
        self._pipeline: Optional[VoicePipeline] = None
        self._hotkeys = HotkeyManager()
        self._ptt: Optional[PushToTalkListener] = None
        self._ptt_stream: Optional[PTTStreamController] = None
        self._tray: Optional[TrayController] = None
        self._orb: Optional[FloatingOrb] = None
        self._bar: Optional[StatusBar] = None
        self._ui_thread: Optional[threading.Thread] = None
        self._ui_ready = threading.Event()
        self._stop_event = threading.Event()
        self._ui_lock = threading.Lock()

    @staticmethod
    def _make_client(profile: dict) -> OrchestratorClient:
        ports = profile.get("ports", {})
        return OrchestratorClient(
            orch_port=int(ports.get("orch", 8730)),
            home=str(profile.get("home", Path.home() / ".voice_input")),
        )

    def _profile_menu(self):
        items = []
        for name, prof in self.cfg.get("profiles", {}).items():
            mark = "✓ " if name == self.cfg.get("active_profile") else "   "
            label = f"{mark}{prof.get('label', name)}"

            def _make_switch(n=name):
                return lambda: self.switch_profile(n)

            items.append((label, _make_switch()))
        return items

    def _make_pipeline(self) -> VoicePipeline:
        ports = self.profile.get("ports", {})
        hooks = AgentStateHooks(
            orch_port=int(ports.get("orch", 8730)),
            home=str(self.profile.get("home", Path.home() / ".voice_input")),
        )
        hooks.client = self.client
        return VoicePipeline(
            profile_name=self.profile.get("name", "base"),
            client=self.client,
            hooks=hooks,
            auto_route=self._auto_route,
            auto_respond=self._auto_respond,
            on_result=lambda _r: self._poller.refresh_once() if self._poller else None,
        )

    def _on_orb_moved(self, x: int, y: int) -> None:
        save_orb_position(x, y, self.cfg)
        orb_cfg = self.cfg.setdefault("ui", {}).setdefault("orb", {})
        orb_cfg["x"] = x
        orb_cfg["y"] = y

    def switch_profile(self, name: str) -> None:
        log.info("切换 profile → %s", name)
        if self._ptt_stream is not None:
            self._ptt_stream.stop()
            self._ptt_stream = None
        self.cfg = set_active_profile(name)
        self.profile = get_active_profile(self.cfg)
        self.client = self._make_client(self.profile)
        if self._poller is not None:
            self._poller.client = self.client
            self._poller.profile_name = self.profile.get("name", "base")
        self._pipeline = self._make_pipeline()
        self._setup_hotkeys()
        self._setup_ptt()
        self._flash_local("idle", f"已切换 → {self.profile.get('label', name)}")
        if self._poller is not None:
            self._poller.refresh_once()
        if self._tray is not None:
            self._tray.profile_actions = self._profile_menu()
            self._tray.refresh_menu()

    def _flash_local(self, state: str, detail: str = "", **extra: Any) -> None:
        """热键按下后立即刷新 UI,不等待轮询"""
        snap: Dict[str, Any] = {
            "state": state,
            "profile": self.profile.get("name", "base"),
            "detail": detail,
            "can_wake": state not in ("speaking", "blocked"),
            "can_interrupt": state in ("thinking", "speaking"),
            "mute_gate_active": state == "speaking",
        }
        snap.update(extra)
        self._on_state(snap)

    def _on_state(self, snap: dict) -> None:
        pname = self.profile.get("name", "base")
        text = format_status_line(pname, snap)
        color = state_color(snap)
        if self._tray is not None:
            self._tray.update_snapshot(snap, color)
        with self._ui_lock:
            if self._orb is not None:
                try:
                    self._orb.update(pname, snap)
                except Exception:
                    pass
            if self._bar is not None:
                try:
                    self._bar.update(text, color)
                except Exception:
                    pass

    def _ensure_ptt_stream(self) -> None:
        if self._ptt_stream is not None:
            return
        ports = self.profile.get("ports", {})
        asr_port = int(ports.get("asr", 8731))
        token = self.client._token
        if not token:
            log.warning("无 auth_token,跳过 WS 流式 ASR")
            return

        def _on_partial(text: str) -> None:
            self._flash_local("listening", text[:40])

        def _on_final(text: str) -> None:
            detail = f"「{text[:36]}」" if text else "无识别结果"
            self._flash_local("thinking", detail)
            self.client.set_agent_state("thinking", detail=detail)
            if self._poller:
                self._poller.refresh_once()
            if text.strip() and self._pipeline:
                self._pipeline.handle_final(text)
            else:
                self.client.set_agent_state("idle", detail=detail)
                self._flash_local("idle", detail)
                if self._poller:
                    self._poller.refresh_once()

        def _on_error(msg: str) -> None:
            log.warning("PTT ASR: %s", msg)
            self._flash_local("idle", msg[:40])
            if self._poller:
                self._poller.refresh_once()

        self._ptt_stream = PTTStreamController(
            asr_port=asr_port,
            auth_token=token,
            on_partial=_on_partial,
            on_final=_on_final,
            on_error=_on_error,
        )

    def _ptt_start(self) -> None:
        snap = self.client.get_agent_state()
        if not snap.get("can_wake", True) and snap.get("state") != "offline":
            self._flash_local(snap.get("state", "idle"), snap.get("detail") or "不可唤醒")
            return
        self._flash_local("listening", "PTT 按住")
        self.client.set_agent_state("listening", detail="PTT 按住")
        if self._stream_asr:
            self._ensure_ptt_stream()
            if self._ptt_stream is not None:
                self._ptt_stream.begin()

    def _ptt_end(self) -> None:
        if self._ptt_stream is not None and self._ptt_stream.active:
            self._flash_local("thinking", "识别中…")
            self.client.set_agent_state("thinking", detail="识别中…")
            self._ptt_stream.end()
        else:
            self.client.set_agent_state("idle", detail="")
            self._flash_local("idle", "")
        if self._poller:
            self._poller.refresh_once()

    def _interrupt(self) -> None:
        if self._ptt_stream is not None:
            self._ptt_stream.stop()
        self._flash_local("idle", "用户打断")
        self.client.set_agent_state("idle", detail="用户打断")
        if self._poller:
            self._poller.refresh_once()

    def _cycle_presence(self) -> None:
        mode = self.client.cycle_presence()
        if mode:
            self._flash_local("idle", f"presence → {mode}")
            log.info("presence → %s", mode)
        else:
            self._flash_local("offline", "presence 切换失败")
        if self._poller:
            self._poller.refresh_once()

    def _setup_hotkeys(self) -> None:
        hk = self.profile.get("hotkeys", {})
        self._hotkeys.stop()
        self._hotkeys = HotkeyManager()
        if hk.get("presence_cycle"):
            self._hotkeys.register(hk["presence_cycle"], self._cycle_presence)
        if hk.get("interrupt"):
            self._hotkeys.register(hk["interrupt"], self._interrupt)
        self._hotkeys.start()

    def _setup_ptt(self) -> None:
        if self._ptt is not None:
            self._ptt.stop()
        spec = self.profile.get("hotkeys", {}).get("push_to_talk", "ctrl+shift+space")
        self._ptt = PushToTalkListener(spec, self._ptt_start, self._ptt_end)
        self._ptt.start()

    def _run_ui(self) -> None:
        """单线程跑 tk mainloop(浮动球优先; 顶栏仅作可选叠加)"""
        try:
            if self._use_orb:
                self._orb = FloatingOrb(
                    size=self._orb_size,
                    position=get_orb_position(self.cfg),
                    profile_name=self.profile.get("name", "base"),
                    on_double_click=self._cycle_presence,
                    on_right_click=self._interrupt,
                    on_moved=self._on_orb_moved,
                )
                self._orb.create()
                self._ui_ready.set()
                self._orb.run()
            elif self._use_bar:
                self._bar = StatusBar(height=self._bar_height)
                self._bar.create()
                self._ui_ready.set()
                self._bar.run()
        except Exception as e:
            log.error("UI 线程异常: %s", e)
            self._ui_ready.set()

    def start(self) -> None:
        logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

        self._setup_hotkeys()
        self._setup_ptt()

        if self._use_tray:
            self._tray = TrayController(
                on_quit=self.shutdown,
                profile_actions=self._profile_menu(),
            )
            self._tray.start()

        if self._use_orb or self._use_bar:
            if self._use_orb and self._use_bar:
                log.warning("floating_orb 与 status_bar 同时启用时仅显示浮动球")
            self._ui_thread = threading.Thread(target=self._run_ui, name="agent-shell-ui", daemon=True)
            self._ui_thread.start()
            self._ui_ready.wait(timeout=5.0)

        self._poller = StatePoller(
            self.client,
            self._poll_ms,
            on_update=self._on_state,
            profile_name=self.profile.get("name", "base"),
        )
        self._pipeline = self._make_pipeline()
        self._poller.start()
        self._poller.refresh_once()

        if self._ui_thread is not None:
            try:
                while self._ui_thread.is_alive() and not self._stop_event.is_set():
                    self._stop_event.wait(0.5)
            except KeyboardInterrupt:
                self.shutdown()
        else:
            log.info("无桌面 UI,Ctrl+C 退出")
            try:
                while not self._stop_event.is_set():
                    self._stop_event.wait(1.0)
            except KeyboardInterrupt:
                self.shutdown()

    def shutdown(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        if self._ptt_stream is not None:
            self._ptt_stream.stop()
        if self._poller:
            self._poller.stop()
        if self._ptt:
            self._ptt.stop()
        self._hotkeys.stop()
        if self._tray:
            self._tray.stop()
        if self._orb:
            self._orb.destroy()
        if self._bar:
            self._bar.destroy()
        log.info("Agent Shell 已退出")
