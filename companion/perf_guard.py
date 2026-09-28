#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# perf_guard.py — M3.51 S10 本地性能守护(2026-09-23)
#
# 目的:
#   - 定时采 perf 快照 → classify_perf → 超过阈值时记录 + 推 ws 告警
#   - 目标风险等级 medium / high / critical → 触发 alert(给前端 + 日志)
#   - 低频采(默认 60s),critical 立刻追加一次重采
#   - 离线工作(本地 adapter 已加载),无 OpenRouter 调用
#
# 用法:
#   python perf_guard.py --once       # 跑一次采样+分类(测试用)
#   python perf_guard.py --interval 60 # 守护模式,60s 采一次
#
# 设计:
#   - 复用 classify_perf(已有 _build_text + adapter classify)
#   - 复用 perf_collector.collect_with_event()
#   - 输出 perf_alerts.jsonl(追加)和 stdout(toast 给 companion 监听)
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# M3.59:统一 admin 探测(单例 + cache + require_or_warn)
from admin_state import is_admin, require_or_warn  # noqa: E402

ALERT_LOG = _HERE / "perf_alerts.jsonl"
HISTORY_LOG = _HERE / "perf_history.jsonl"  # M3.52 S3:所有 alert 持久化,7 天滚动趋势

# 触发 alert 的风险等级(>= medium)
ALERT_RISKS = {"medium", "high", "critical"}


# M3.52 S2:alert hint 映射(risk + 触发条件 → 具体建议)
ALERT_HINTS: dict[str, list[str]] = {
    "critical": [
        "立即检查: 禁用 disconnected TAP miniport (pnputil /disable-device)",
        "考虑卸载 VPN 客户端(ProtonVPN/OpenVPN/V2RayN)改用 WireGuard",
        "提取最近 .dmp 文件给 Verifier 跑 !analyze -v 看具体函数偏移",
        "如多次蓝屏,考虑 Driver Verifier exclude ndis.sys(临时绕过)",
    ],
    "high": [
        "启用 Process Lasso ProBalance(若未启用)",
        "ISLC(IdleLikeSleepCorrectly): 把 standby list 调小到 1GB 释放工作集",
        "检查 top 进程占用 CPU%,若是 docker / WSL 容器可降级或暂停",
        "检查 disconnected TAP 是否需禁用(防止下次 idle 期崩)",
    ],
    "medium": [
        "观察 1-2 分钟,如持续 medium → 升级 high",
        "检查是否后台进程(防病毒/索引服务)在 IO/内存压力大",
        "如启用 VPN,断开 5 分钟后观察 NIC 是否回归",
    ],
}


def _hints_for(rec: dict, sample: Optional[dict] = None) -> list[str]:
    """根据 rec.risk + sample 中的 disconnected_taps / cpu_pct 给具体 hint。"""
    base = ALERT_HINTS.get(rec["risk"], [])
    extra = []
    if sample:
        net = sample.get("net", {})
        for nic in net.get("nics", []):
            if nic.get("isup") is False:
                name = (nic.get("nic") or "").lower()
                if any(t in name for t in ("tap0901", "tapprotonvpn", "vktap")):
                    extra.append(f"⚠ disconnected TAP 命中: {name}")
        # M3.54 S2:high/critical 时委托 PrisirAI process_list_impl 拿真实 top 进程 + IO 字节
        #         再用 process_numa.recommend_for_process 给推荐核数(本地算法,无 admin 也能用)
        if rec["risk"] in ("high", "critical"):
            try:
                import sys
                _pris_path = Path(__file__).resolve().parent.parent / "mcp_prisiragent_server"
                if str(_pris_path) not in sys.path:
                    sys.path.insert(0, str(_pris_path))
                from process_controller_tools import process_list_impl as pris_list
                from process_numa import recommend_for_process
                total_cores = 6
                try:
                    import psutil
                    total_cores = psutil.cpu_count(logical=True) or 6
                except ImportError:
                    pass
                # PrisirAI 拉 top 10(包含 IO 字节)
                pls = json.loads(pris_list("cpu", 10))
                procs = [p for p in pls.get("processes", []) if p.get("cpu", 0) >= 20.0][:3]
                for p in procs:
                    rec_numa = recommend_for_process(p["cpu"], total_cores)
                    extra.append(
                        f"  📊 [PrisirAI] {p['name']} (pid {p['pid']}) "
                        f"CPU {p['cpu']:.0f}% / MEM {p.get('mem_mb', 0):.0f}MB / "
                        f"IO_R {p.get('io_read_mb', 0):.1f}MB / "
                        f"IO_W {p.get('io_write_mb', 0):.1f}MB "
                        f"→ 建议 {rec_numa['recommended_cores']} 核 "
                        f"(mask {rec_numa['core_mask_hex']},原因:{rec_numa['reason']})"
                    )
                if procs:
                    extra.append(
                        "  💡 调用 PrisirAI process_cpu_limit 真改 affinity "
                        "(管理员启动 companion 后 python process_numa.py --apply --pid X --cores N)"
                    )
                    extra.append(
                        "  📦 同样可调:process_lower(降优先级) / process_io_priority(限 IO) / "
                        "process_blacklist_add(ProBalance 自动降)"
                    )
            except Exception as e:
                # 兜底:PrisirAI 不可用时退回本地 psutil
                try:
                    from process_numa import list_top
                    numa = list_top(cpu_threshold=20.0, top_n=3)
                    for p in numa:
                        extra.append(
                            f"  📊 [local-fallback] {p['name']} (pid {p['pid']}) "
                            f"CPU {p['cpu_pct']:.0f}% "
                            f"→ 建议 {p['recommended_cores']} 核 "
                            f"(mask {p['core_mask_hex']})"
                        )
                except Exception:
                    pass
    return base + extra


def _load_perf_conf_adapter():
    """懒加载 perf_conf_v2 adapter(本地 / aliyun 自动探测)。"""
    from adapter_registry import get_adapter
    return get_adapter("perf_conf_v2")


def run_once(verbose: bool = True, sample: Optional[dict] = None) -> dict:
    """采一次 + 分类。返 {sample, result, alert: bool}。"""
    if sample is None:
        from perf_collector import collect_with_event
        sample = collect_with_event()
    adapter = _load_perf_conf_adapter()
    from classify_perf import classify_perf
    out = classify_perf(adapter, sample)
    is_alert = out["risk"] in ALERT_RISKS
    rec = {
        "ts": sample.get("ts"),
        "risk": out["risk"],
        "action": out["action"],
        "risk_conf": out["risk_conf"],
        "action_conf": out["action_conf"],
        "latency_ms": out["latency_ms"],
        "parse_fail": out["parse_fail"],
        "alert": is_alert,
        "raw": out["raw"],
    }
    if is_alert:
        rec["hints"] = _hints_for(rec, sample)
        rec["_sample"] = sample  # M3.55:供 _trigger_kill_mode 用(disconnected TAP)
    if verbose:
        tag = "🚨 ALERT" if is_alert else "✅ OK"
        print(f"[{rec['ts']}] {tag} {rec['risk']:8s} | {rec['action']:6s} | "
              f"conf={rec['risk_conf']} | {rec['latency_ms']}ms",
              file=sys.stderr)
    return rec


def log_alert(rec: dict) -> None:
    """追加到 perf_alerts.jsonl + history log + stdout(JSON line 给 companion ws 抓)。"""
    line = json.dumps(rec, ensure_ascii=False)
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # M3.52 S3:写 history(供 trend 检测)
    with HISTORY_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # 同时打 stdout(JSON line,companion 可 grep)
    print(f"PERF_ALERT {line}", flush=True)
    # S2:打印 human-readable 提示
    for h in rec.get("hints", []):
        print(f"  💡 {h}", flush=True)
    # S3:trend 检测 — 同一 risk 在 24h 内出现 ≥ 3 次 → 升级"系统故障"
    trend = detect_trend(rec["risk"], window_h=24)
    if trend["escalate"]:
        print(f"  🔥 TREND ESCALATION: {rec['risk']} 出现 {trend['count']} 次/24h,建议立即处理!",
              flush=True)
        rec["trend_escalation"] = trend
        # 重写 stdout 给前端 toast 用
        print(f"PERF_TREND {json.dumps({'risk': rec['risk'], 'count': trend['count'], 'window_h': 24}, ensure_ascii=False)}",
              flush=True)
    # M3.55 S2:critical + disconnected TAP → 自动启 watchdog kill_mode
    kill_evt = _trigger_kill_mode(rec, sample=rec.get("_sample"))
    if kill_evt:
        rec["blacklist_action"] = kill_evt


# M3.55:critical alert 自动触发 PrisirAI watchdog kill_mode
def _trigger_kill_mode(rec: dict, sample: Optional[dict] = None,
                       auto_apply: bool = False) -> Optional[dict]:
    """critical alert + disconnected TAP → 建议加黑名单 + 启 watchdog kill_mode=kill。

    M3.87 P0-2:auto_apply 默认 False — perf_conf_v3 78% ACC (critical 7/10 30% 误判),
    自动加 blacklist + 杀进程不可逆。改成"建议模式":返 dict 含 blacklist_add_cmd /
    watchdog_start_cmd,user 确认后才真正调用。auto_apply=True 走老路径,保留向后兼容。

    Returns:
        dict 含 suggested_actions / event 给前端 toast;
        或 None 表示没触发条件。
    """
    if rec.get("risk") != "critical":
        return None
    # 找 sample 里 disconnected TAP 的进程名
    tap_names = []
    if sample:
        net = sample.get("net", {})
        for nic in net.get("nics", []):
            if nic.get("isup") is False:
                name = (nic.get("nic") or "").lower()
                if any(t in name for t in ("tap0901", "tapprotonvpn", "vktap", "tap", "tun")):
                    # 抽出 "进程名" 部分(去掉驱动后缀),用于 PrisirAI 黑名单
                    # TAP 设备的"进程名"是 driver sys 文件名,在 watchdog 里不能直接 match 进程
                    # 退一步:用 "tap" 作为模糊匹配关键词(任何含 tap 的进程名)
                    tap_names.append("tap")
    # 如果没 disconnected TAP,critical 来自其他原因 → 不触发 kill_mode(保守)
    if not tap_names:
        return None

    # ─── M3.87 P0-2:默认返"建议对象",user 决定是否执行 ───
    suggested_actions = {
        "blacklist_add": [
            {"name": n, "added_by": "perf_guard.M3.55",
             "source": "auto", "reason": "disconnected TAP (ProtonVPN/M3.55 trigger)",
             "ttl_sec": 7 * 24 * 3600}
            for n in tap_names
        ],
        "watchdog_start": {
            "interval_sec": 5, "cpu_threshold": 80.0,
            "kill_mode": "kill", "kill_cooldown_sec": 30,
            "kill_max_per_round": 5, "dry_run": False,
        },
    }

    if not auto_apply:
        # 建议模式 — 只返,不执行
        evt = {
            "ts": rec.get("ts"),
            "event": "PERF_BLACKLIST_SUGGEST",
            "risk": rec["risk"],
            "tap_names": tap_names,
            "suggested_actions": suggested_actions,
            "auto_applied": False,
            "note": ("perf_conf_v3 78% ACC 有 22% 误判风险(M3.87)。"
                     "已生成建议,需 user 一键确认才会真正加 blacklist + 启 kill_mode。"),
        }
        print(f"PERF_BLACKLIST_SUGGEST {json.dumps(evt, ensure_ascii=False)}", flush=True)
        return evt

    # ─── auto_apply=True:走老路径(向后兼容,CLI / 集成测试可能用)───
    try:
        import sys
        _pris_path = Path(__file__).resolve().parent.parent / "mcp_prisiragent_server"
        if str(_pris_path) not in sys.path:
            sys.path.insert(0, str(_pris_path))
        from process_controller_tools import (
            blacklist_add_impl, blacklist_list_impl,
            watchdog_start_impl, watchdog_status_impl,
        )
        # 1. 加黑名单(去重)
        added = []
        for n in tap_names:
            bl = json.loads(blacklist_list_impl())
            # M3.79:blacklist 现为 list[dict],取 name 字段
            existing = [x.get("name", "").lower() if isinstance(x, dict) else x.lower()
                        for x in bl.get("blacklist", [])]
            if n.lower() not in existing:
                # M3.79:传 source=auto + reason 让 toast 弹 + 7 天 TTL 生效
                r = json.loads(blacklist_add_impl(
                    n,
                    added_by="perf_guard.M3.55",
                    source="auto",
                    reason="disconnected TAP (ProtonVPN/M3.55 trigger)",
                ))
                if r.get("ok"):
                    added.append(n)
        # 2. 启 watchdog kill_mode=kill — M3.59:按 admin_state 切 dry_run
        #    之前 hardcode dry_run=False,非 admin 时 Windows 默默拒绝真杀
        #    (M3.55 隐藏 bug)。改 require_or_warn 显式标记未生效
        wd_status = json.loads(watchdog_status_impl())
        watchdog_started = False
        if not wd_status.get("running"):
            if not require_or_warn("watchdog kill_mode=kill"):
                # 非 admin:不打 dry_run=False 避免 Windows 失败;改为 dry_run=True 仅记录
                # 同时 note 写明未生效,前端 toast 能展示
                r = json.loads(watchdog_start_impl(
                    interval_sec=5, cpu_threshold=80.0,
                    kill_mode="off",  # dry_run=True 时降档到 off,避免误导用户以为真杀
                    kill_cooldown_sec=30, kill_max_per_round=5,
                    dry_run=True,
                ))
                watchdog_started = r.get("ok", False)
            else:
                # admin:真杀路径
                r = json.loads(watchdog_start_impl(
                    interval_sec=5, cpu_threshold=80.0,
                    kill_mode="kill", kill_cooldown_sec=30, kill_max_per_round=5,
                    dry_run=False,
                ))
                watchdog_started = r.get("ok", False)
        evt = {
            "ts": rec.get("ts"),
            "event": "PERF_BLACKLIST",
            "risk": rec["risk"],
            "blacklist_added": added,
            "watchdog_started": watchdog_started,
            "auto_applied": True,
        }
        # M3.59:note 按 admin 状态切 — 真杀 / 仅记录 显式区分
        if is_admin():
            evt["note"] = ("已加 TAP 黑名单并启 watchdog kill_mode=kill "
                          "(disconnected TAP 进程一出现立即杀)")
        else:
            evt["note"] = ("非 admin 启动 — watchdog 降级为 dry_run(仅记录不真杀)。"
                          "需 admin 提权后 watchdog kill_mode 才生效。"
                          "黑名单仍写入,重启 admin 可接管。")
        # 推 stdout 让前端 ws / 外部监听器抓
        print(f"PERF_BLACKLIST {json.dumps(evt, ensure_ascii=False)}", flush=True)
        return evt
    except Exception as e:
        print(f"[perf_guard] kill_mode trigger failed: {e}", file=sys.stderr)
        return None


def detect_trend(risk: str, window_h: int = 24, threshold: int = 3) -> dict:
    """M3.52 S3:扫 history log,统计同 risk 在 window_h 小时内出现次数。"""
    from datetime import datetime, timezone, timedelta
    if not HISTORY_LOG.exists():
        return {"count": 0, "escalate": False, "window_h": window_h}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_h)
    count = 0
    try:
        with HISTORY_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("risk") != risk:
                    continue
                ts = rec.get("ts")
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if t >= cutoff:
                    count += 1
    except Exception:
        return {"count": 0, "escalate": False, "window_h": window_h}
    return {
        "count": count,
        "escalate": count >= threshold,
        "window_h": window_h,
        "threshold": threshold,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="perf_conf_v2 守护:采 + alert")
    ap.add_argument("--interval", type=int, default=60,
                    help="守护模式采间隔秒数(默认 60)")
    ap.add_argument("--once", action="store_true",
                    help="跑一次即退出(测试用)")
    ap.add_argument("--quiet", action="store_true",
                    help="只打 alert,不打印 OK")
    args = ap.parse_args()

    if args.once:
        rec = run_once(verbose=True)
        if rec["alert"]:
            log_alert(rec)
        return 0 if not rec["alert"] else 2

    print(f"[perf_guard] 启动守护,interval={args.interval}s,alert_log={ALERT_LOG}",
          file=sys.stderr)
    while True:
        try:
            rec = run_once(verbose=not args.quiet)
            if rec["alert"]:
                log_alert(rec)
                # critical 立刻追加一次重采
                if rec["risk"] == "critical":
                    time.sleep(5)
                    rec2 = run_once(verbose=False)
                    log_alert({**rec2, "_verify": True})
        except KeyboardInterrupt:
            print("[perf_guard] Ctrl-C, 退出", file=sys.stderr)
            return 0
        except Exception as e:
            print(f"[perf_guard] ERR: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())