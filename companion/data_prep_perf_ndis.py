"""data_prep_perf_ndis.py — M3.51 S5

ndis BSOD 专项训练数据生成器 — 复用 perf schema,聚焦用户真实 fingerprint:
  - BugCheck 0x3b + ACCESS_VIOLATION + RIP 0x...e61b7
  - Kernel-Power 41 + NIC compliance
  - tap0901 / tapprotonvpn / vktap Disconnected miniport
  - 启动 60 秒内 BSOD

输出: data/data_perf_ndis.jsonl + 自动合并入 data/data_perf.jsonl。

风险规则:
  critical: 5/5 fingerprint 命中(ndis RIP + NIC compliance + TAP disconnected + 夜间 + boot 内)
  high    : 3-4 个 fingerprint
  medium  : 1-2 个 fingerprint
  low     : 仅 ProBalance / ISLC 状态
  safe    : 全部干净

注意:这是边界样本增强器,主要补 critical/high 类的多 fingerprint 组合,
避免 M3.51 S4 的 4:6:8:5:4 配比里 critical 太少。
"""
from __future__ import annotations
import argparse, json, random, re
from pathlib import Path
from typing import Any

# 真实 BSOD fingerprint 元素(S1 + S2 汇总)
FINGERPRINT_NDIS_RIP = ["ndis.sys+0x8e61b7", "ndis!ndisMSendPacketsXToMiniport",
                        "ndis!ndisMSendNetBufferListsToMiniport", "ndis!NdisMSendNetBufferLists",
                        "RIP 0xfffff806228e61b7", "RIP 0xfffff8071f2e61b7"]
FINGERPRINT_NIC_COMPLIANCE = ["NIC compliance", "ndis compliance scan",
                              "_NIC.NIC compliance", "Adapter compliance failed",
                              "Network adapter disconnected due to compliance"]
FINGERPRINT_TAP_DISCONNECTED = ["tap0901 disconnected", "tapprotonvpn disconnected",
                                "vktap disconnected", "3 TAP miniport disconnected",
                                "ProtonVPN TAP v9 disconnected"]
FINGERPRINT_BOOT_GRACE = ["boot 后 18 秒 BSOD", "启动 30 秒内蓝屏", "boot 60s 崩",
                          "刚启动 NDIS 扫描即崩", "system_event 5_19 triggered at boot+18s"]
FINGERPRINT_NIGHT = ["夜间 03:26 UTC", "02:50 UTC NIC compliance", "凌晨 03:00 BSOD",
                     "idle 期间 NIC 扫合规"]
FINGERPRINT_CPU_IDLE = ["CPU 293K (20°C) 空闲", "idle 状态 NIC compliance 触发",
                        "CPU 频率 800MHz idle", "低负载但 NDIS 崩"]
FINGERPRINT_BLACKSCREEN_HISTORY = ["7 天内 4 次 BugCheck", "近 24 小时 2 次 KP41",
                                   "本周 5/5 BSOD 同一 RIP", "近一周 BSOD 频率 1.3 次/天"]

# BSOD 误归类样本(噪声)— 模拟其他 0x3b 触发(非 ndis)避免模型只认 ndis 关键词
NOISE_OTHER_BSODS = [
    "BugCheck 0x3b ACCESS_VIOLATION at win32kfull.sys+0x12ab9 (显卡驱动触发)",
    "BugCheck 0x3b at ntfs.sys+0x2f450 (文件系统层触发)",
    "BugCheck 0x3b at tcpip.sys+0x8e234 (TCP/IP 栈层)",
    "BugCheck 0x3b at Wdf01000.sys+0x1c5a (WDF 框架层)",
]


def _fingerprint_synth(risk: str, hit_count: int) -> str:
    """根据风险等级选 fingerprint 元素"""
    pools = [FINGERPRINT_NDIS_RIP, FINGERPRINT_NIC_COMPLIANCE, FINGERPRINT_TAP_DISCONNECTED,
             FINGERPRINT_BOOT_GRACE, FINGERPRINT_NIGHT, FINGERPRINT_CPU_IDLE,
             FINGERPRINT_BLACKSCREEN_HISTORY]

    if risk == "critical":
        # 5/5 命中
        chosen = random.sample(pools, k=min(hit_count or 5, len(pools)))
    elif risk == "high":
        chosen = random.sample(pools, k=min(hit_count or 3, len(pools)))
    elif risk == "medium":
        chosen = random.sample(pools, k=min(hit_count or 2, len(pools)))
    elif risk == "low":
        chosen = pools[5]  # CPU idle 类池(整个池随机抽 1 个即可)
    else:  # safe
        chosen = []
    # pools[5] 已是列表,其他 chosen 是 list-of-list,都展平
    flat = []
    for c in chosen:
        flat.extend(c) if isinstance(c, list) else flat.append(c)
    return ", ".join(flat[:max(1, hit_count or 1)])


def _build_text(fingerprint: str, ts: str = "03:26 UTC", nic_status: str = "6 up / 3 down") -> str:
    return (f"性能采样: 时间戳 {ts}, "
            f"网络: {nic_status}, "
            f"CPU 空闲温度 20°C 频率 3.7GHz, "
            f"BugCheck fingerprint: [{fingerprint}]\n"
            f"问: 这个性能快照的风险等级和推荐处理动作?")


def synth_ndis_samples(count_per_risk: dict[str, int] = None) -> list[dict]:
    """ndis 专项合成 — 5 类边界样本(聚焦 critical/high)"""
    count_per_risk = count_per_risk or {
        "critical": 80,  # 5/5 fingerprint + 部分 4/5
        "high": 60,
        "medium": 50,
        "low": 30,
        "safe": 20,
    }
    out = []
    action_map = {"safe": "keep", "low": "keep", "medium": "review", "high": "alert", "critical": "alert"}

    # critical 5/5 + 4/5
    for i in range(count_per_risk["critical"]):
        hits = 5 if i < 60 else 4
        fp = _fingerprint_synth("critical", hits)
        out.append({
            "text": _build_text(fp, ts=random.choice(["03:26 UTC", "02:50 UTC", "00:50 UTC", "14:37 UTC"])),
            "risk_label": "critical",
            "jailbreak_label": False,
            "_action": action_map["critical"],
            "_source": "ndis_synth",
        })

    # high 3/5 + 2/5(部分变体)
    for i in range(count_per_risk["high"]):
        hits = 3 if i < 40 else 2
        fp = _fingerprint_synth("high", hits)
        out.append({
            "text": _build_text(fp, ts=random.choice(["15:32 UTC", "12:43 UTC", "10:15 UTC"])),
            "risk_label": "high",
            "jailbreak_label": False,
            "_action": action_map["high"],
            "_source": "ndis_synth",
        })

    # medium 1-2 fingerprint
    for i in range(count_per_risk["medium"]):
        hits = 2 if i < 30 else 1
        fp = _fingerprint_synth("medium", hits)
        out.append({
            "text": _build_text(fp),
            "risk_label": "medium",
            "jailbreak_label": False,
            "_action": action_map["medium"],
            "_source": "ndis_synth",
        })

    # low — 仅 CPU idle + ProBalance 信息
    for i in range(count_per_risk["low"]):
        out.append({
            "text": _build_text("CPU 空闲温度 20°C 频率 3.7GHz"),
            "risk_label": "low",
            "jailbreak_label": False,
            "_action": action_map["low"],
            "_source": "ndis_synth",
        })

    # safe — 全干净
    for i in range(count_per_risk["safe"]):
        out.append({
            "text": _build_text("", nic_status="6 up / 0 down"),
            "risk_label": "safe",
            "jailbreak_label": False,
            "_action": action_map["safe"],
            "_source": "ndis_synth",
        })

    # 噪声:其他 BSOD 根因(critical 但 fingerprint 不是 ndis)
    for noise in NOISE_OTHER_BSODS:
        out.append({
            "text": f"性能采样: 时间戳 03:26 UTC, 网络 6 up / 3 down, CPU 空闲, "
                    f"BugCheck fingerprint: [{noise}]\n问: 这个性能快照的风险等级和推荐处理动作?",
            "risk_label": "critical",
            "jailbreak_label": False,
            "_action": "alert",
            "_source": "ndis_noise",
        })

    random.shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/data_perf_ndis.jsonl")
    ap.add_argument("--merge-into", default="data/data_perf.jsonl",
                    help="合并到 S4 输出文件(M3.51 一并训练)")
    ap.add_argument("--seed", type=int, default=43)
    args = ap.parse_args()
    random.seed(args.seed)

    here = Path(__file__).parent
    samples = synth_ndis_samples()
    by_risk = {}
    for s in samples:
        by_risk[s["risk_label"]] = by_risk.get(s["risk_label"], 0) + 1
    print(f"[data_prep_perf_ndis] samples: {len(samples)}")
    print(f"[data_prep_perf_ndis] by_risk: {by_risk}")

    out_path = here / args.output
    with out_path.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[data_prep_perf_ndis] written: {out_path}")

    # 合并到 S4 输出
    merge_path = here / args.merge_into
    if merge_path.exists():
        appended = 0
        with merge_path.open("a", encoding="utf-8") as f:
            for s in samples:
                # 去掉 _source 字段,S4 标准输出
                f.write(json.dumps({k: v for k, v in s.items() if not k.startswith("_")},
                                   ensure_ascii=False, separators=(",", ":")) + "\n")
                appended += 1
        print(f"[data_prep_perf_ndis] merged {appended} samples into {merge_path}")


if __name__ == "__main__":
    main()