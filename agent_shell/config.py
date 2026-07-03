"""Agent Shell 配置 — ~/.oi_agent/shell.yaml"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

OI_AGENT_HOME = Path.home() / ".oi_agent"
SHELL_CONFIG_PATH = OI_AGENT_HOME / "shell.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "active_profile": "base",
    "profiles": {
        "base": {
            "label": "基础版 (本地参考)",
            "philosophy": "neutral",
            "ports": {"orch": 8730, "asr": 8731, "tts": 8732},
            "home": str(Path.home() / ".voice_input"),
            "hotkeys": {
                "push_to_talk": "ctrl+shift+space",
                "presence_cycle": "ctrl+shift+p",
                "interrupt": "escape",
            },
        },
        "fastlane": {
            "label": "FastLane (云优先)",
            "philosophy": "cloud_first",
            "ports": {"orch": 8750, "asr": 8751, "tts": 8752},
            "home": str(Path.home() / ".voice_input_fastlane"),
            "hotkeys": {
                "push_to_talk": "ctrl+shift+space",
                "presence_cycle": "ctrl+shift+p",
                "interrupt": "escape",
            },
        },
        "ghostline": {
            "label": "GhostLine (零外发)",
            "philosophy": "privacy_first",
            "ports": {"orch": 8740, "asr": 8741, "tts": 8742},
            "home": str(Path.home() / ".voice_input_ghostline"),
            "hotkeys": {
                "push_to_talk": "ctrl+alt+space",
                "presence_cycle": "ctrl+alt+p",
                "interrupt": "escape",
            },
        },
    },
    "ui": {
        "floating_orb": True,
        "status_bar": False,
        "tray": True,
        "poll_interval_ms": 500,
        "status_bar_height": 10,
        "stream_asr": True,
        "orb": {
            "size": 56,
            "x": None,
            "y": None,
        },
    },
    "pipeline": {
        "auto_route": True,
        "auto_respond": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_config() -> Path:
    """首启写入默认 shell.yaml"""
    OI_AGENT_HOME.mkdir(parents=True, exist_ok=True)
    if not SHELL_CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    return SHELL_CONFIG_PATH


def load_config() -> Dict[str, Any]:
    ensure_config()
    if yaml is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    raw = yaml.safe_load(SHELL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: Dict[str, Any]) -> None:
    OI_AGENT_HOME.mkdir(parents=True, exist_ok=True)
    if yaml is None:
        raise RuntimeError("PyYAML 未安装,无法保存 shell.yaml")
    SHELL_CONFIG_PATH.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def get_active_profile(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    name = cfg.get("active_profile", "base")
    profiles = cfg.get("profiles", {})
    if name not in profiles:
        raise KeyError(f"active_profile '{name}' 不在 profiles 里")
    prof = copy.deepcopy(profiles[name])
    prof["name"] = name
    return prof


def set_active_profile(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    if name not in cfg.get("profiles", {}):
        raise KeyError(f"unknown profile: {name}")
    cfg["active_profile"] = name
    save_config(cfg)
    return get_active_profile(cfg)


def save_orb_position(x: int, y: int, cfg: Optional[Dict[str, Any]] = None) -> None:
    """拖动浮动球后持久化位置"""
    cfg = cfg or load_config()
    ui = cfg.setdefault("ui", {})
    orb = ui.setdefault("orb", {})
    orb["x"] = int(x)
    orb["y"] = int(y)
    save_config(cfg)


def get_orb_position(cfg: Optional[Dict[str, Any]] = None) -> Optional[tuple[int, int]]:
    cfg = cfg or load_config()
    orb = cfg.get("ui", {}).get("orb", {})
    x, y = orb.get("x"), orb.get("y")
    if x is None or y is None:
        return None
    return int(x), int(y)
