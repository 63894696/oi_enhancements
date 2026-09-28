#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# perf_alert.py — M3.69 perf_alert 核心逻辑(2026-09-24)
#
# 目的:
#   - 前台常驻 → 每 N 秒采一次性能(perf_collector)→ 用 perf_conf_v3
#     评 5 类风险 → critical/high 触发告警
#
# 设计:
#   - **零侵入**:不修改 companion/ 下任何代码
#   - **离线可用**:仅本地 LoRA 推理,无外部 API
#   - **复用 perf_collector / classify_perf**:不重写
#   - 不依赖 perf_guard.py(它有 admin 联动 / IO 字节抓取等副作用,Phase 1
#     只想要 "采 + 评 + 简单告警" 三件事),需要它的逻辑由本文件自己最小实现
#
# 公开入口:
#   sample_once(spec="perf_conf_v3") -> (sample, result)
#   format_log_line(sample, result)   -> str
#   should_alert(result)              -> bool
#   alert_actions(sample, result, watchdog=False, ws_url=None) -> list[str]
#   run_loop(interval, spec, dry_run, max_iter, watchdog, ws_url, output_path) -> int
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# 把 companion/ 加到 sys.path — 复用已有 perf_collector / classify_perf / adapter_registry
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_COMPANION = Path("C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION) not in sys.path:
    sys.path.insert(0, str(_COMPANION))

# 静默 torch 的各种 warning,不致命
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# 等级 → emoji + 标签(对齐示例输出)
RISK_BADGES = {
    "safe":     ("✅", ""),
    "low":      ("ℹ️ ", ""),
    "medium":   ("⚠️ ", "WARNING"),
    "high":     ("🔴", "ALERT"),
    "critical": ("🚨", "CRITICAL"),
}

ALERT_LEVELS = {"high", "critical"}
WARNING_LEVELS = {"medium", "high", "critical"}


# ----------------------------------------------------------------
# 采样 + 分类
# ----------------------------------------------------------------
def sample_once(spec: str = "perf_conf_v3") -> tuple[dict, dict]:
    """采一次 + 评一次。返 (sample, result)。失败抛 RuntimeError。"""
    from perf_collector import collect_one
    from classify_perf import classify_perf
    from adapter_registry import get_adapter

    sample = collect_one()
    adapter = get_adapter(spec)
    result = classify_perf(adapter, sample)
    return sample, result


def should_alert(result: dict) -> bool:
    """是否要触发告警(high / critical)。"""
    return (result.get("risk") or "").lower() in ALERT_LEVELS


def should_warn(result: dict) -> bool:
    """是否要 WARNING(medium / high / critical)。"""
    return (result.get("risk") or "").lower() in WARNING_LEVELS


# ----------------------------------------------------------------
# 输出格式化
# ----------------------------------------------------------------
def _now_clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_log_line(sample: dict, result: dict) -> str:
    """单行紧凑文本,匹配示例输出:
        [10:23:00] safe        CPU 12% mem 38% NIC up
        [10:23:20] medium     CPU 78% mem 72% NIC up  ⚠️
    """
    risk = (result.get("risk") or "?").lower()
    badge, _tag = RISK_BADGES.get(risk, ("?", ""))
    cpu = sample.get("cpu", {}).get("pct", 0) or 0
    mem = sample.get("memory", {}).get("used_pct", 0) or 0
    # NIC 状态:取一个有代表性的("up" if any up else "down")
    net = sample.get("net", {})
    nics = net.get("nics", [])
    tap_down = 0
    any_up = False
    for n in nics:
        name = (n.get("nic") or "").lower()
        if "tap" in name and n.get("isup") is False:
            tap_down += 1
        if n.get("isup") is True:
            any_up = True
    nic_str = "NIC up" if any_up else "NIC down"
    if tap_down:
        nic_str += f" TAP down({tap_down})"

    line = (f"[{_now_clock()}] {risk:<8} "
            f"CPU {cpu:.0f}% mem {mem:.0f}% {nic_str}")
    if should_warn(result):
        line += f"  {badge}"
    return line


def format_json_line(sample: dict, result: dict) -> str:
    """JSONL 一行(可写文件)。"""
    out = {
        "ts": sample.get("ts") or datetime.now().isoformat(),
        "risk": result.get("risk"),
        "action": result.get("action"),
        "risk_conf": result.get("risk_conf"),
        "action_conf": result.get("action_conf"),
        "jailbreak": result.get("jailbreak"),
        "latency_ms": result.get("latency_ms"),
        "parse_fail": result.get("parse_fail"),
        "cpu_pct": sample.get("cpu", {}).get("pct"),
        "mem_pct": sample.get("memory", {}).get("used_pct"),
        "n_nics": sample.get("net", {}).get("n_total"),
        "n_nics_up": sample.get("net", {}).get("n_up"),
    }
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


# ----------------------------------------------------------------
# 告警触发动作(Phase 1: print 模拟 + WebSocket optional)
# ----------------------------------------------------------------
def _disconnected_taps(sample: dict) -> list[str]:
    out = []
    for n in sample.get("net", {}).get("nics", []):
        if n.get("isup") is False:
            name = (n.get("nic") or "").lower()
            if any(t in name for t in ("tap0901", "tapprotonvpn", "vktap", "tap", "tun")):
                out.append(n.get("nic") or "?")
    return out


def alert_actions(
    sample: dict,
    result: dict,
    watchdog: bool = False,
    ws_url: Optional[str] = None,
) -> list[str]:
    """根据 risk 返回要执行的告警动作列表(已 print 过的行)。"""
    lines: list[str] = []
    risk = (result.get("risk") or "").lower()

    if risk == "critical":
        lines.append(" → 触发 critical alert")
        taps = _disconnected_taps(sample)
        if taps:
            lines.append(f" → TAP disconnected: {', '.join(taps)}")
        if watchdog:
            lines.append("[WATCHDOG] kill_mode=kill (模拟触发 — Phase 2 真接)")
            evt = {
                "event": "PERF_BLACKLIST",
                "risk": risk,
                "ts": sample.get("ts"),
                "taps": taps,
                "source": "perf_alert_cli",
            }
            lines.append(f"[WATCHDOG] PERF_BLACKLIST {json.dumps(evt, ensure_ascii=False)}")
        lines.append(" → 建议: 同步 process_numa 检查 top CPU 进程亲和性")
    elif risk == "high":
        lines.append(" → 触发 high alert")
        if watchdog:
            lines.append("[WATCHDOG] kill_mode=off (high 仅 dry-run,critical 才真杀)")
        lines.append(" → 建议: 关注 top 进程 / 内存释放")
    elif risk == "medium":
        lines.append(" → WARNING: 注意观察")

    if ws_url:
        ok = _ws_send(ws_url, sample, result)
        if ok:
            lines.append(f"[WS] pushed to {ws_url}")
        else:
            lines.append(f"[WS] skip (no server at {ws_url})")

    return lines


def _ws_send(url: str, sample: dict, result: dict) -> bool:
    """尝试推一条 WebSocket 消息。失败 → False(优雅跳过,不抛)。"""
    try:
        import websockets  # type: ignore
    except Exception:
        return False
    try:
        import asyncio
        payload = json.dumps({
            "type": "perf_alert",
            "ts": sample.get("ts"),
            "risk": result.get("risk"),
            "action": result.get("action"),
            "cpu_pct": sample.get("cpu", {}).get("pct"),
            "mem_pct": sample.get("memory", {}).get("used_pct"),
        }, ensure_ascii=False)

        async def _send():
            async with websockets.connect(url, open_timeout=1.5,
                                          close_timeout=1.5) as ws:
                await ws.send(payload)

        asyncio.run(_send())
        return True
    except Exception:
        return False


# ----------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------
def run_loop(
    interval: int = 60,
    spec: str = "perf_conf_v3",
    dry_run: bool = False,
    max_iter: int = 0,
    watchdog: bool = False,
    ws_url: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> int:
    """主循环:每 interval 秒采一次 + 评。

    dry_run=True  → 不打告警动作(只格式化文本)
    max_iter=0    → 无限循环
    max_iter=N    → 跑 N 次退出
    output_path   → 追加 JSONL
    """
    header = (f"[perf_alert] interval={interval}s  spec={spec}  "
              f"watchdog={'on' if watchdog else 'off'}  "
              f"ws={ws_url or 'off'}  dry_run={dry_run}")
    print(header, flush=True)

    output_fh = None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_fh = output_path.open("a", encoding="utf-8")

    n = 0
    try:
        while True:
            t0 = time.time()
            try:
                sample, result = sample_once(spec=spec)
                text = format_log_line(sample, result)
                print(text, flush=True)
                if output_fh:
                    output_fh.write(format_json_line(sample, result) + "\n")
                    output_fh.flush()

                is_alert = should_alert(result)
                is_warn = should_warn(result)
                if (is_alert or is_warn) and not dry_run:
                    for ln in alert_actions(sample, result,
                                            watchdog=watchdog,
                                            ws_url=ws_url):
                        print(ln, flush=True)
            except KeyboardInterrupt:
                print("[perf_alert] interrupted, exit", flush=True)
                return 0
            except Exception as e:  # noqa: BLE001
                # 任何采样 / 推理错误 → 打印 + 继续下次,不退出
                print(f"[perf_alert] sample err: {type(e).__name__}: {e}",
                      flush=True)

            n += 1
            if max_iter and n >= max_iter:
                print(f"[perf_alert] reached max_iter={max_iter}, exit",
                      flush=True)
                return 0

            # sleep(扣掉已用时间,但最少睡 1s 防止 hot-loop)
            elapsed = time.time() - t0
            sleep_for = max(1, interval - int(elapsed))
            try:
                time.sleep(sleep_for)
            except KeyboardInterrupt:
                print("[perf_alert] interrupted during sleep, exit",
                      flush=True)
                return 0
    finally:
        if output_fh:
            output_fh.close()
