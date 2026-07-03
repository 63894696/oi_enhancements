"""Agent 状态 → UI 文案 / 颜色"""
from __future__ import annotations

STATE_COLORS = {
    "offline": "#666666",
    "idle": "#22c55e",
    "listening": "#3b82f6",
    "thinking": "#f59e0b",
    "speaking": "#a855f7",
    "busy": "#06b6d4",
    "blocked": "#ef4444",
    "degraded": "#eab308",
}

STATE_LABELS = {
    "offline": "离线",
    "idle": "就绪",
    "listening": "聆听中",
    "thinking": "思考中",
    "speaking": "播报中",
    "busy": "处理中",
    "blocked": "不可用",
    "degraded": "已降级",
}

PROFILE_BADGE = {
    "base": "BASE",
    "fastlane": "FAST",
    "ghostline": "GHOST",
    "neutral": "BASE",
    "cloud_first": "FAST",
    "privacy_first": "GHOST",
}


def format_status_line(profile_name: str, snap: dict) -> str:
    state = snap.get("state", "offline")
    label = STATE_LABELS.get(state, state)
    badge = PROFILE_BADGE.get(snap.get("profile", profile_name), profile_name.upper()[:5])
    detail = (snap.get("detail") or "").strip()
    flags = []
    if snap.get("mute_gate_active"):
        flags.append("静音")
    if snap.get("can_interrupt"):
        flags.append("可打断")
    if not snap.get("can_wake", True) and state != "offline":
        flags.append("勿唤醒")
    if snap.get("fastlane_degraded_from"):
        flags.append(f"降级←{snap['fastlane_degraded_from']}")
    if snap.get("ghostline_blocked_reason"):
        flags.append("阻断")
    extra = " · ".join(flags)
    line = f"[{badge}] {label}"
    if detail:
        line += f" — {detail[:40]}"
    if extra:
        line += f" ({extra})"
    return line


def state_color(snap: dict) -> str:
    return STATE_COLORS.get(snap.get("state", "offline"), "#666666")
