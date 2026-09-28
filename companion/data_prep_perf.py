"""data_prep_perf.py — M3.51 S4

把 perf_collector.py 真实采集的 perf 流 → Qwen3Guard 训练样本。

schema(与 disk_cleanup / tempfile / email / log 同):
  {"text": "性能采样: <ts> ...", "risk_label": "safe|low|medium|high|critical",
   "jailbreak_label": False, "_action": "delete|review|keep|alert"}

风险分级规则(借鉴 ISLC + Lasso 阈值,基于用户 32GB 机器):
  safe    : cpu<60% AND standby_list<1GB AND no_disconnected_tap AND bugcheck_7d==0
  low     : cpu<80% AND standby_list<2GB AND <=1 disconnected_tap AND bugcheck_7d<=1
  medium  : cpu>=80% OR standby_list>=2GB OR 2 disconnected_tap OR ProBalance 已触发
  high    : cpu>=90% 持续10s OR standby_list>=4GB OR 3 disconnected_tap OR kp41>=2
  critical: bugcheck_7d>=2 OR boot_within_60s_bugcheck OR ndis_compliance_detected OR cpu==100%_5min

action 映射:
  safe → keep
  low → keep (记录但不干预)
  medium → review (ProBalance 评估 + working set trim)
  high → alert (ProBalance 强制降级 + 提示用户禁可疑 NIC)
  critical → alert (强制 disable 任意 disconnected TAP + 写 dump 给 admin)

输出: companion/data/data_perf.jsonl + data_perf_conf.jsonl(teacher 用)
"""
from __future__ import annotations
import argparse, json, os, random, re, sys
from pathlib import Path
from typing import Any

# ---- 风险阈值(用户机器 32GB / 6 核 / Windows 10) ----
THRESHOLDS = {
    "cpu_pct_safe": 60,
    "cpu_pct_medium": 80,
    "cpu_pct_high": 90,
    "standby_list_safe_gb": 1.0,
    "standby_list_medium_gb": 2.0,
    "standby_list_high_gb": 4.0,
    "disconnected_tap_safe": 0,
    "disconnected_tap_medium": 2,
    "disconnected_tap_high": 3,
    "bugcheck_7d_high": 1,
    "bugcheck_7d_critical": 2,
    "kp41_7d_high": 2,
    "boot_grace_s": 60,  # 启动 60 秒内崩 = critical
}

# ---- action 映射 ----
ACTION_MAP = {
    "safe": "keep",
    "low": "keep",
    "medium": "review",
    "high": "alert",
    "critical": "alert",
}

# ---- 关键词 fingerprint(用于 synthetic samples 与 perf 数据共训)----
SYN_CRITICAL_KEYWORDS = [
    "BSOD", "BugCheck 0x3b", "SYSTEM_SERVICE_EXCEPTION", "ndis.sys", "NIC compliance",
    "0xc0000005", "ACCESS_VIOLATION", "tap0901", "KERNEL_SECURITY_CHECK_FAILURE",
    "蓝屏", "系统服务异常", "Driver Verifier 触发",
]
SYN_HIGH_KEYWORDS = [
    "cpu 持续 90%", "ProBalance 强制降级", "工作集膨胀", "内存泄漏",
    "内存已耗尽", "OOM", "page fault in nonpaged area", "DRIVER_IRQL_NOT_LESS_OR_EQUAL",
    "thermal throttling", "CPU 频率降到 800MHz", "卡死 30 秒",
]
SYN_MEDIUM_KEYWORDS = [
    "cpu 峰值 85%", "ProBalance 触发一次", "工作集 trim", "standby list 增长",
    "后台进程优先级降级", "container restart 第 3 次", "service timeout",
    "GPU 90%", "磁盘 IO 100%", "swap 使用 4GB",
]
SYN_LOW_KEYWORDS = [
    "cpu 偶尔 70%", "空闲内存 18GB", "工作集 trim 一次", "checkpoint 完成",
    "容器启动", "cron job 完成", "后端异步任务结束", "ProBalance 解禁",
]
SYN_SAFE_KEYWORDS = [
    "cpu 空闲 30%", "内存 16GB available", "后台无 ProBalance 触发",
    "磁盘 IO 平稳", "网络流量 10Mbps", "一切正常", "定时清理完成",
]

# ---- TAP miniport fingerprint 列表(从 S2 报告提炼)----
TAP_DRIVERS = ["tap0901", "tapprotonvpn", "vktap", "tun", "win-tap"]


def _detect_disconnected_taps(net_sample: dict) -> list[str]:
    """从 perf_collector 输出找 Disconnected + TAP 关键词的 NIC"""
    out = []
    for nic in net_sample.get("nics", []):
        isup = nic.get("isup")
        name = (nic.get("nic") or "").lower()
        if isup is False and any(t in name for t in TAP_DRIVERS):
            out.append(name)
    return out


def _compute_risk(sample: dict) -> tuple[str, list[str]]:
    """根据 perf sample 算 risk + reason list。"""
    reasons = []
    cpu = sample.get("cpu", {})
    mem = sample.get("memory", {})
    net = sample.get("net", {})
    crash = sample.get("crash", {})
    sys_ = sample.get("system", {})

    cpu_pct = cpu.get("pct", 0)
    used_pct = mem.get("used_pct", 0)
    avail_gb = mem.get("available_gb", 32)
    disconnected_taps = _detect_disconnected_taps(net)
    bug = crash.get("bugcheck_count", 0) or 0
    kp41 = crash.get("kp41_count", 0) or 0
    uptime = sys_.get("uptime_s", 9999)

    # critical 判定
    if bug >= THRESHOLDS["bugcheck_7d_critical"]:
        reasons.append(f"7 天内 {bug} 次蓝屏")
    if uptime < THRESHOLDS["boot_grace_s"] and bug > 0:
        reasons.append(f"启动 {uptime}s 内发生蓝屏")
    if cpu_pct >= 99.0:
        reasons.append(f"CPU {cpu_pct:.1f}% 满载")
    if reasons:
        return "critical", reasons

    # high
    if bug >= THRESHOLDS["bugcheck_7d_high"]:
        reasons.append(f"7 天内 {bug} 次蓝屏")
    if cpu_pct >= THRESHOLDS["cpu_pct_high"]:
        reasons.append(f"CPU {cpu_pct:.1f}% ≥90%")
    if used_pct >= 95:
        reasons.append(f"内存已用 {used_pct:.1f}%")
    if avail_gb < 1.0:
        reasons.append(f"可用内存仅 {avail_gb:.1f}GB")
    if len(disconnected_taps) >= THRESHOLDS["disconnected_tap_high"]:
        reasons.append(f"{len(disconnected_taps)} 个 TAP miniport disconnected")
    if kp41 >= THRESHOLDS["kp41_7d_high"]:
        reasons.append(f"7 天内 {kp41} 次 Kernel-Power 41")
    if reasons:
        return "high", reasons

    # medium
    if cpu_pct >= THRESHOLDS["cpu_pct_medium"]:
        reasons.append(f"CPU {cpu_pct:.1f}% ≥80%")
    if used_pct >= 85:
        reasons.append(f"内存使用 {used_pct:.1f}%")
    if len(disconnected_taps) >= THRESHOLDS["disconnected_tap_medium"]:
        reasons.append(f"{len(disconnected_taps)} 个 TAP miniport disconnected")
    if reasons:
        return "medium", reasons

    # low
    if cpu_pct >= 50:
        reasons.append(f"CPU {cpu_pct:.1f}% 中等负载")
    if used_pct >= 70:
        reasons.append(f"内存使用 {used_pct:.1f}%")
    if len(disconnected_taps) >= 1:
        reasons.append(f"{len(disconnected_taps)} 个 TAP miniport disconnected")
    if reasons:
        return "low", reasons

    return "safe", ["全部指标在安全范围内"]


def _format_sample_text(sample: dict) -> str:
    """把 perf sample 渲染成可喂给 Qwen3Guard 的文本段。"""
    cpu = sample.get("cpu", {})
    mem = sample.get("memory", {})
    net = sample.get("net", {})
    crash = sample.get("crash", {})
    sys_ = sample.get("system", {})
    disconnected_taps = _detect_disconnected_taps(net)
    n_total = net.get("n_total", 0)
    n_up = net.get("n_up", 0)
    n_down = n_total - n_up
    boot_s = sys_.get("uptime_s", 0)

    parts = [
        f"时间戳: {sample.get('ts','?')}",
        f"CPU: {cpu.get('pct',0):.1f}% / {cpu.get('count',0)} 核 @ {cpu.get('freq_mhz',0)}MHz",
        f"内存: {mem.get('used_pct',0):.1f}% (used {mem.get('used_gb',0)}GB / total {mem.get('total_gb',0)}GB, available {mem.get('available_gb',0)}GB)",
        f"网络: {n_total} 个 NIC,{n_up} up,{n_down} down,TAP disconnected: {len(disconnected_taps)} ({', '.join(disconnected_taps) or 'none'})",
        f"系统: boot 后 {boot_s}s,用户 {sys_.get('user','?')}",
    ]
    if crash:
        parts.append(f"7 天内 BugCheck: {crash.get('bugcheck_count','?')}, Kernel-Power 41: {crash.get('kp41_count','?')}")
    parts.append("问: 这个性能快照的风险等级和推荐处理动作?")
    return "\n".join(parts)


def load_perf_stream(perf_dir: Path) -> list[dict]:
    """读 data/perf_*.jsonl 中所有采样"""
    out = []
    for f in sorted(perf_dir.glob("perf_*.jsonl")):
        with f.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def from_real_samples(perf_dir: Path) -> list[dict]:
    """真实采样 → 训练样本(用 _compute_risk 打标)"""
    samples = load_perf_stream(perf_dir)
    out = []
    for s in samples:
        risk, reasons = _compute_risk(s)
        out.append({
            "text": _format_sample_text(s),
            "risk_label": risk,
            "jailbreak_label": False,
            "_action": ACTION_MAP[risk],
            "_reasons": reasons,
            "_source": "real",
        })
    return out


# ---- 合成样本:基于关键词 fingerprint,补足长尾 + 边界样本 ----
def _synth(text_hint: str, risk: str, reason: str) -> dict:
    return {
        "text": f"性能采样: {text_hint}\n问: 这个性能快照的风险等级和推荐处理动作?",
        "risk_label": risk,
        "jailbreak_label": False,
        "_action": ACTION_MAP[risk],
        "_reasons": [reason],
        "_source": "synth",
    }


SYNTH_TEMPLATES = {
    "critical": [
        ("08:50 蓝屏 BSOD BugCheck 0x3b SYSTEM_SERVICE_EXCEPTION at ndis.sys+0x8e61b7, NIC compliance detected", "11:55 再次触发"),
        ("boot 后 18 秒 BugCheck 0x3b ACCESS_VIOLATION, RIP 0xfffff806228e61b7, Kernel-Power 41 触发", "启动期崩"),
        ("cpu 100% 持续 5 分钟, swap 已耗尽, page fault in nonpaged area, driver verifier 检测到违规", "资源耗尽 + verifier"),
        ("蓝屏 dump 显示 tap0901.sys+0x2a30 调用 ndisMSendPacketsXToMiniport 出现 NULL pointer", "TAP miniport 触发"),
    ],
    "high": [
        ("cpu 92% 持续 30 秒, ProBalance 已强制降级 2 个后台进程, 内存剩余 0.8GB", "CPU 满载 + ProBalance 触发"),
        ("3 个 TAP miniport disconnected (tap0901 / tapprotonvpn / vktap), ndis 启动期扫描慢", "TAP 多"),
        ("7 天内 1 次 BugCheck 0x3b, Kernel-Power 41 触发 3 次, NIC compliance 告警", "近期 BSOD"),
        ("cpu 95%, GPU 90%, 内存 96%, swap 4GB, IO 100% 持续 12 秒, 系统无响应 30 秒", "资源全打满"),
    ],
    "medium": [
        ("cpu 85% 持续 5 秒, ProBalance 触发一次降级, 工作集 trim 后内存回到 60%", "CPU 短峰"),
        ("standby list 2.5GB, free 内存 3GB, 2 个 TAP disconnected, ISLC 未启用", "内存压力"),
        ("cpu 82%, 后台 docker 容器重启第 3 次, postgres slow query 12s", "进程不稳"),
        ("内存使用 87%, 进程 280 个, containerd-shim RSS 异常高", "内存泄漏嫌疑"),
    ],
    "low": [
        ("cpu 65% 持续 1 分钟, 内存空闲 18GB, ProBalance 解禁, 一切正常", "瞬时负载"),
        ("空闲内存 20GB, 1 个 TAP disconnected (未用), 网络流量 50Mbps", "基本平稳"),
        ("cron job 完成, 内存释放 1GB, ProBalance 触发零次", "低噪声"),
        ("cpu 50%, 容器启动一次成功, ISLC 触发 standby 清零", "正常清理"),
    ],
    "safe": [
        ("cpu 30%, 内存 16GB available, 网络 10Mbps, ProBalance 未触发, 一切正常", "正常负载"),
        ("定时清理完成, 内存释放 500MB, cpu 20%, 进程 270 个", "后台常规"),
        ("空闲状态, cpu 5%, 内存 25GB available, 网络空闲", "真空闲"),
        ("checkpoint 完成, cpu 40%, 内存 60%, ProBalance 未触发", "稳态"),
    ],
}


def from_synth(count: int = 400) -> list[dict]:
    """合成样本,4:3:5:4:2 = safe:low:medium:high:critical 配比"""
    out = []
    # 按权重生成
    weights = {"safe": 4, "low": 6, "medium": 8, "high": 5, "critical": 4}
    total = sum(weights.values())
    by_risk = {k: max(1, count * w // total) for k, w in weights.items()}
    for risk, n in by_risk.items():
        pool = SYNTH_TEMPLATES[risk]
        for i in range(n):
            text_hint, reason = pool[i % len(pool)]
            # 加点变异:数字 / 时间窗
            t = text_hint
            t = re.sub(r'\b(\d+)\b', lambda m: str(int(m.group(1)) + random.randint(-3, 3)), t)
            out.append(_synth(t, risk, reason))
    random.shuffle(out)
    return out


# ---- main ----
def build_dataset(real_samples: list, synth_samples: list) -> list[dict]:
    """合并真实 + 合成,按比例"""
    # 真实样本当 anchor,合成补足
    out = list(real_samples)
    out.extend(synth_samples)
    random.shuffle(out)
    # 去掉内部字段
    return [{"text": x["text"], "risk_label": x["risk_label"],
             "jailbreak_label": x["jailbreak_label"], "_action": x["_action"]} for x in out]


def by_risk_stats(samples: list[dict]) -> dict:
    out = {}
    for s in samples:
        out[s["risk_label"]] = out.get(s["risk_label"], 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/data_perf.jsonl")
    ap.add_argument("--synth", type=int, default=400, help="合成样本数")
    ap.add_argument("--perf-dir", default="data", help="perf_collector 输出目录")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    here = Path(__file__).parent
    perf_dir = here / args.perf_dir
    real = from_real_samples(perf_dir)
    print(f"[data_prep_perf] real samples loaded: {len(real)}")
    synth = from_synth(args.synth)
    print(f"[data_prep_perf] synth samples generated: {len(synth)}")
    dataset = build_dataset(real, synth)
    stats = by_risk_stats(dataset)
    print(f"[data_prep_perf] by_risk: {stats}")
    print(f"[data_prep_perf] total: {len(dataset)}")

    out_path = here / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for s in dataset:
            f.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[data_prep_perf] written: {out_path}")


if __name__ == "__main__":
    main()