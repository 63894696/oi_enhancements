"""Agent Shell 单元测试(S1,无 GUI)"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_shell.config import DEFAULT_CONFIG, _deep_merge, get_active_profile, load_config
from agent_shell.display import format_status_line, state_color, STATE_COLORS
from agent_shell.hotkeys import hotkey_to_pynput
from agent_shell.ptt import _parse_modifiers


def test_default_config_has_three_profiles():
    names = set(DEFAULT_CONFIG["profiles"].keys())
    assert names == {"base", "fastlane", "ghostline"}


def test_get_active_profile_base():
    cfg = _deep_merge(DEFAULT_CONFIG, {"active_profile": "ghostline"})
    p = get_active_profile(cfg)
    assert p["name"] == "ghostline"
    assert p["ports"]["orch"] == 8740


def test_hotkey_to_pynput():
    assert hotkey_to_pynput("ctrl+shift+space") == "<ctrl>+<shift>+<space>"
    assert hotkey_to_pynput("escape") == "<esc>"


def test_parse_ptt_modifiers():
    mods, main = _parse_modifiers("ctrl+alt+space")
    assert mods == {"ctrl", "alt"}
    assert main == "space"


def test_format_status_line_listening():
    snap = {
        "state": "listening",
        "profile": "ghostline",
        "detail": "PTT 按住",
        "can_wake": False,
        "can_interrupt": False,
        "mute_gate_active": False,
    }
    line = format_status_line("ghostline", snap)
    assert "GHOST" in line or "ghost" in line.lower()
    assert "聆听" in line


def test_state_color_known():
    assert state_color({"state": "blocked"}) == STATE_COLORS["blocked"]


def test_load_config_file_exists():
    cfg = load_config()
    assert "active_profile" in cfg
    assert "profiles" in cfg


def test_tray_menu_action_does_not_shadow_callback():
    from agent_shell.tray import TrayController

    called = []

    def cb():
        called.append(1)

    handler = TrayController._menu_action(cb)
    handler(None, object())  # pystray 传入 (icon, item)
    assert called == [1]


def test_agent_hooks_thinking(monkeypatch):
    from agent_shell.hooks import AgentStateHooks

    posts = []

    class FakeClient:
        def set_agent_state(self, state, detail="", **kwargs):
            posts.append((state, detail))
            return {"state": state}

    hooks = AgentStateHooks()
    hooks.client = FakeClient()
    hooks.thinking("test")
    assert posts == [("thinking", "test")]


def test_default_config_floating_orb():
    assert DEFAULT_CONFIG["ui"]["floating_orb"] is True
    assert DEFAULT_CONFIG["ui"]["status_bar"] is False


def test_state_glyph_listening():
    from agent_shell.floating_orb import _state_glyph

    assert _state_glyph("listening") == "听"
    assert _state_glyph("thinking") == "思"


def test_health_snap_ghostline_blocked():
    from agent_shell.health_snap import merge_health_into_snap

    snap = merge_health_into_snap(
        "ghostline",
        {"status": "ok", "asr_health": {"status": "down"}, "tts_health": {"status": "ok"}},
        {"state": "idle", "detail": ""},
    )
    assert snap["state"] == "blocked"
    assert "GhostLine" in snap.get("ghostline_blocked_reason", "")


def test_health_snap_preserves_active_listening():
    from agent_shell.health_snap import merge_health_into_snap

    snap = merge_health_into_snap(
        "ghostline",
        {"status": "ok", "asr_health": {"status": "down"}, "tts_health": {"status": "ok"}},
        {"state": "listening", "detail": "PTT"},
    )
    assert snap["state"] == "listening"


def test_voice_pipeline_route_only():
    from agent_shell.oi_pipeline import VoicePipeline

    class FakeClient:
        def get_agent_state(self):
            return {"presence": "text"}

        def inject_text(self, text):
            return {"status": "ok"}

        def speak(self, text, interrupt=True):
            return {"status": "ok"}

    class FakeHooks:
        def thinking(self, detail=""):
            pass

        def idle(self, detail=""):
            pass

    p = VoicePipeline("base", FakeClient(), hooks=FakeHooks(), auto_route=False, auto_respond=False)
    assert p._busy.acquire(blocking=False)
    p._run("测试指令")

