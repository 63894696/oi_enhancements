"""根据 /health + profile 合成 blocked/degraded 等 UI 状态(S3)"""
from __future__ import annotations

from typing import Any, Dict


def _sub_ok(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    st = block.get("status", "")
    return st in ("ok", "muted")


def merge_health_into_snap(profile_name: str, health: dict, snap: dict) -> dict:
    """在 orchestrator /agent_state 之上叠加 daemon 健康语义。

    - ghostline: ASR/TTS 任一不可用 → blocked(G-5 fail-closed 可视化)
    - fastlane: 子 daemon 异常 → degraded(云链路/local 兜底失败)
    活跃会话(listening/thinking/speaking)不强制覆盖,避免打断 PTT 反馈。
    """
    out = dict(snap)
    active = out.get("state") in ("listening", "thinking", "speaking", "busy")

    if health.get("status") not in ("ok", "muted"):
        out.setdefault("state", "offline")
        out["detail"] = health.get("error") or f"health={health.get('status')}"
        out["can_wake"] = False
        return out

    asr_h = health.get("asr_health") or health.get("asr") or {}
    tts_h = health.get("tts_health") or health.get("tts") or {}
    asr_ok = _sub_ok(asr_h)
    tts_ok = _sub_ok(tts_h)

    if profile_name == "ghostline" and (not asr_ok or not tts_ok):
        reason = []
        if not asr_ok:
            reason.append(f"ASR={asr_h.get('status', '?')}")
        if not tts_ok:
            reason.append(f"TTS={tts_h.get('status', '?')}")
        msg = "GhostLine 阻断: " + ", ".join(reason)
        if not active:
            out["state"] = "blocked"
            out["ghostline_blocked_reason"] = msg
            out["detail"] = msg
            out["can_wake"] = False
        else:
            out.setdefault("ghostline_blocked_reason", msg)

    elif profile_name == "fastlane" and (not asr_ok or not tts_ok):
        msg = "FastLane 降级: 本地 daemon 异常"
        if not active and out.get("state") in ("idle", "offline", "degraded"):
            out["state"] = "degraded"
            out["fastlane_degraded_from"] = out.get("fastlane_degraded_from") or "cloud"
            out["detail"] = msg if not out.get("detail") else out["detail"]
        out.setdefault("fastlane_degraded_from", "local_daemon")

    return out
