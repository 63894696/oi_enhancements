#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_perf_v2.py — M3.51 S9-retry 扩 perf 数据集(2026-09-23)
#
# 目的:
#   - 修 S9 bench 22% 准确率的根因:模型学到的只是固定 anchor 短语
#     (如 `boot 后 18 秒 BugCheck 0x3b ACCESS_VIOLATION, RIP 0xfffff806228e61b7`),
#     而 bench test cases 用了**抽象的 sample dict 字段**(boot_s < 60, bugcheck_count >= 2)。
#   - 扩数据集加入**抽象语义模板**,让模型学到:
#     - "boot<60s + bugcheck>=2" → critical
#     - "boot<60s + bugcheck=1" → high
#     - ">=3 个 TAP disconnected" → high
#     - "1-2 个 TAP disconnected + CPU 60-90%" → low/medium
#     - "0 TAP + 0 crash + CPU <50%" → safe
#
# 与 v1 的关系:
#   - v1 645 条样本(_format_sample_text 3 条 + _synth 642 条)完整保留作为 base
#   - v2 新增 ~400 条**抽象模式样本**(全是 _synth 格式)
#
# 用法:
#   python data_prep_perf_v2.py --output data_perf_v2.jsonl
#   # 默认会 load v1 data_perf.jsonl 拼起来 → data_perf_v2.jsonl
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

_HERE = Path(__file__).resolve().parent

ACTION_MAP = {
    "safe": "keep",
    "low": "keep",
    "medium": "review",
    "high": "alert",
    "critical": "alert",
}


# ---- 抽象模式:boot 后 + bugcheck_count + ndis fingerprint ----
# 设计:不重复具体 RIP 地址(留给 v1 的 BSOD dump 模板),
#      而是教模型学"boot<60s + BugCheck X 次 → critical/high" 的高层语义
TEMPLATES_ABSTRACT = {
    "critical": [
        # boot<60s + 多次 bugcheck → critical (ndis fingerprint 触发)
        "boot 后 {boot_s} 秒, BugCheck 出现 {n} 次, ndis.sys+0x8e61b7 偏移, 系统未稳定就崩",
        "启动 {boot_s} 秒内 BugCheck {n} 次, NIC compliance 触发, Kernel-Power 41 {kp} 次记录",
        "boot {boot_s}s 内连崩 {n} 次, BugCheck 0x3b ACCESS_VIOLATION, ndis miniport 异常",
        "凌晨 {ts_hour} 时 boot {boot_s}s, BugCheck {n} 次, ndis 启动期扫 NIC 触发 BSOD",
        "{ts_hour}:XX UTC boot {boot_s}s, {n} 次 BSOD, NIC compliance detected at ndis.sys",
        # cpu 持续满载 + swap 耗尽 → critical
        "cpu {cpu}% 持续 {dur_min} 分钟, swap 已耗尽, page fault in nonpaged area, driver verifier 检测到违规",
        # 蓝屏 dump + TAP miniport 直接 NULL
        "蓝屏 dump 显示 tap0901.sys+0x2a30 调用 ndisMSendPacketsXToMiniport 出现 NULL pointer",
        # NIC compliance 反复触发
        "NIC compliance 反复触发 {n} 次, 每次都 ndis.sys+0x8e61b7, 系统在 idle 期崩",
        # boot 后立刻崩
        "boot 后 {boot_s} 秒 BugCheck 0x3b ACCESS_VIOLATION, RIP ndis.sys+0x8e61b7, Kernel-Power {kp} 触发",
        # 系统未启动完就崩
        "系统未启动完成 boot {boot_s}s 时崩, BugCheck {n} 次, NIC compliance 夜间扫 NIC 触发",
        # 短时间内多次崩
        "{dur_min} 分钟内 {n} 次 BSOD, ndis.sys+0x8e61b7 同一偏移, NIC compliance detected",
    ],
    "high": [
        # 启动期 + bugcheck=1 (崩过但当前稳定)
        "boot {boot_s}s 后 BugCheck {n} 次, ndis 启动期扫 NIC 触发 BSOD, 后重启稳定",
        "启动 {boot_s} 秒内出现 1 次 BugCheck, Kernel-Power 41 触发, NIC compliance 告警",
        # TAP 多 + CPU 高
        "{n_taps} 个 TAP miniport disconnected (tap0901 / tapprotonvpn / vktap), ndis 启动期扫描慢",
        "{n_taps} 个 TAP disconnected, cpu {cpu}% 持续 {dur_s} 秒, ProBalance 已强制降级 {n_bg} 个后台进程",
        # cpu 满载 + 内存爆
        "cpu {cpu}% 持续 {dur_s} 秒, 内存剩余 {avail}GB, ProBalance 触发, 工作集 trim 后回到 {used}%",
        "cpu {cpu}%, GPU {gpu}%, 内存 {mem}%, swap {swap}GB, IO {io}% 持续 12 秒, 系统无响应 30 秒",
        # 近期 BSOD 但当前稳定
        "{days} 天内 {n} 次 BugCheck 0x3b, Kernel-Power {kp} 触发, NIC compliance 告警",
        # TAP 启动慢但未崩
        "{n_taps} 个 TAP disconnected, ndis 启动期扫 NIC, Kernel-Power 41 触发 {kp} 次",
    ],
    "medium": [
        # TAP 1-2 个 disconnected + CPU 中高
        "{n_taps} 个 TAP disconnected, NIC compliance, cpu {cpu}% 持续 {dur_s} 秒, 内存空闲 {avail}GB",
        "standby list {standby}GB, free 内存 {free}GB, {n_taps} 个 TAP disconnected, ISLC 未启用",
        # CPU 短峰
        "cpu {cpu}% 持续 {dur_s} 秒, ProBalance 触发一次降级, 工作集 trim 后内存回到 {used}%",
        # 后台进程不稳
        "cpu {cpu}%, 后台 docker 容器重启第 {restart_n} 次, postgres slow query 12s",
        # 内存使用高但未爆
        "内存使用 {used}%, 进程 {proc} 个, containerd-shim RSS 异常高, swap {swap}GB",
        # CPU 中等但 TAP 在
        "{n_taps} 个 TAP miniport disconnected, cpu {cpu}% 中等负载, 内存空闲 {avail}GB",
        # 内存压力
        "内存 {used}%, standby list {standby}GB, free {free}GB, {n_taps} 个 TAP disconnected",
    ],
    "low": [
        # TAP 1 个 + CPU 中等
        "{n_taps} 个 TAP disconnected (未用), cpu {cpu}% 持续 {dur_s} 秒, 内存空闲 {avail}GB",
        # CPU 中等 + 一切正常
        "cpu {cpu}% 持续 {dur_min} 分钟, 内存空闲 {avail}GB, ProBalance 解禁, 一切正常",
        # 空闲 + TAP 1 个
        "空闲内存 {avail}GB, {n_taps} 个 TAP disconnected (未用), 网络流量 {net_mbps}Mbps",
        # 容器启动
        "cpu {cpu}%, 容器启动一次成功, ISLC 触发 standby 清零, ProBalance 触发零次",
        # cron 完成
        "cron job 完成, 内存释放 {released}GB, ProBalance 触发零次, cpu {cpu}%",
        # 基础噪声
        "boot {boot_s}s 后系统稳, cpu {cpu}% 中等负载, {n_taps} 个 TAP disconnected 但未触发 NIC compliance",
    ],
    "safe": [
        # 空闲 + 一切正常
        "cpu {cpu}%, 内存 {avail}GB available, 网络 {net_mbps}Mbps, ProBalance 未触发, 一切正常",
        # 定时清理
        "定时清理完成, 内存释放 {released}GB, cpu {cpu}%, 进程 {proc} 个, 一切正常",
        # 空闲
        "空闲状态, cpu {cpu}%, 内存 {avail}GB available, 网络空闲, ProBalance 未触发",
        # checkpoint
        "checkpoint 完成, cpu {cpu}%, 内存 {used}%, ProBalance 未触发, 一切正常",
        # 稳态
        "稳态运行, boot {boot_s}s, cpu {cpu}%, 内存 {used}%, 0 TAP disconnected, ProBalance 未触发",
    ],
}


def _gen(template: str, risk: str) -> str:
    """填一个模板,用合理范围数字"""
    if "{boot_s}" in template:
        if risk == "critical":
            boot_s = random.choice([15, 18, 20, 22, 25, 28, 30, 35])
        elif risk == "high":
            boot_s = random.choice([40, 45, 50, 55, 60, 70, 80])
        else:
            boot_s = random.choice([200, 500, 1000, 2000, 3000, 4000])
        template = template.replace("{boot_s}", str(boot_s))
    if "{n}" in template:
        if risk == "critical":
            n = random.choice([2, 3, 4, 5])
        elif risk == "high":
            n = random.choice([1, 2])
        else:
            n = random.choice([0, 1])
        template = template.replace("{n}", str(n))
    if "{kp}" in template:
        kp = random.choice([1, 2, 3, 4, 5])
        template = template.replace("{kp}", str(kp))
    if "{n_taps}" in template:
        if risk == "high":
            n_taps = random.choice([3, 4, 5, 6])
        elif risk == "medium":
            n_taps = random.choice([1, 2])
        else:
            n_taps = random.choice([0, 1])
        template = template.replace("{n_taps}", str(n_taps))
    if "{n_bg}" in template:
        n_bg = random.choice([1, 2, 3, 5, 6])
        template = template.replace("{n_bg}", str(n_bg))
    if "{cpu}" in template:
        if risk in ("critical", "high"):
            cpu = random.choice([85, 88, 90, 92, 95])
        elif risk == "medium":
            cpu = random.choice([70, 75, 80, 82, 85])
        elif risk == "low":
            cpu = random.choice([45, 50, 55, 60, 65])
        else:
            cpu = random.choice([5, 10, 15, 20, 30])
        template = template.replace("{cpu}", str(cpu))
    if "{gpu}" in template:
        gpu = random.choice([80, 85, 90])
        template = template.replace("{gpu}", str(gpu))
    if "{mem}" in template:
        mem = random.choice([90, 92, 95])
        template = template.replace("{mem}", str(mem))
    if "{swap}" in template:
        swap = random.choice([2, 3, 4])
        template = template.replace("{swap}", str(swap))
    if "{io}" in template:
        io = random.choice([90, 95, 100])
        template = template.replace("{io}", str(io))
    if "{used}" in template:
        if risk == "high":
            used = random.choice([88, 90, 92, 94])
        elif risk == "medium":
            used = random.choice([70, 75, 80, 85])
        elif risk == "low":
            used = random.choice([40, 50, 55, 60])
        else:
            used = random.choice([25, 30, 35, 40])
        template = template.replace("{used}", str(used))
    if "{avail}" in template:
        if risk == "high":
            avail = random.choice([1, 2, 3])
        elif risk == "medium":
            avail = random.choice([5, 6, 8, 10])
        elif risk == "low":
            avail = random.choice([12, 15, 18, 20])
        else:
            avail = random.choice([20, 22, 24, 25])
        template = template.replace("{avail}", str(avail))
    if "{free}" in template:
        free = random.choice([2, 3, 4])
        template = template.replace("{free}", str(free))
    if "{standby}" in template:
        standby = random.choice([2, 3, 3.5, 4, 5])
        template = template.replace("{standby}", str(standby))
    if "{dur_s}" in template:
        if risk == "high":
            dur_s = random.choice([25, 28, 30, 32, 35])
        elif risk == "medium":
            dur_s = random.choice([5, 7, 10, 12])
        else:
            dur_s = random.choice([3, 5])
        template = template.replace("{dur_s}", str(dur_s))
    if "{dur_min}" in template:
        dur_min = random.choice([1, 2, 3])
        template = template.replace("{dur_min}", str(dur_min))
    if "{proc}" in template:
        proc = random.choice([260, 270, 280, 281])
        template = template.replace("{proc}", str(proc))
    if "{net_mbps}" in template:
        net = random.choice([10, 20, 50, 80])
        template = template.replace("{net_mbps}", str(net))
    if "{released}" in template:
        released = random.choice([0.5, 1, 1.5])
        template = template.replace("{released}", str(released))
    if "{restart_n}" in template:
        restart_n = random.choice([2, 3, 4])
        template = template.replace("{restart_n}", str(restart_n))
    if "{days}" in template:
        days = random.choice([3, 4, 7, 9])
        template = template.replace("{days}", str(days))
    if "{ts_hour}" in template:
        if risk == "critical":
            ts_hour = random.choice([2, 3, 4])
        else:
            ts_hour = random.choice([10, 11, 14])
        template = template.replace("{ts_hour}", str(ts_hour))
    return template


def generate_abstract(target_count: int = 400) -> list[dict]:
    """生成 ~400 条抽象模式样本。配比 critical:high:medium:low:safe = 2:2:3:3:2"""
    out = []
    ratios = {"critical": 0.18, "high": 0.18, "medium": 0.27, "low": 0.22, "safe": 0.15}
    rng = random.Random(42)
    for risk, ratio in ratios.items():
        n = int(target_count * ratio)
        templates = TEMPLATES_ABSTRACT[risk]
        for i in range(n):
            tpl = rng.choice(templates)
            text_hint = _gen(tpl, risk)
            out.append({
                "text": f"性能采样: {text_hint}\n问: 这个性能快照的风险等级和推荐处理动作?",
                "risk_label": risk,
                "jailbreak_label": False,
                "_action": ACTION_MAP[risk],
                "_reasons": ["abstract pattern"],
                "_source": "synth_v2",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="data_prep_perf_v2 — 抽象模式扩 perf 训练集")
    ap.add_argument("--base", default="data/data_perf.jsonl",
                    help="v1 base data path(默认 645 条)")
    ap.add_argument("--output", default="data/data_perf_v2.jsonl",
                    help="output path")
    ap.add_argument("--count", type=int, default=400,
                    help="新生成样本数")
    args = ap.parse_args()

    base_path = _HERE / args.base
    out_path = _HERE / args.output

    base_samples = []
    if base_path.exists():
        with base_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    base_samples.append(json.loads(line))
        print(f"[v2] base loaded: {len(base_samples)} from {base_path}")
    else:
        print(f"[v2] WARN: base not found: {base_path}")

    rng_samples = generate_abstract(args.count)
    print(f"[v2] abstract generated: {len(rng_samples)}")
    from collections import Counter
    cnt = Counter(s["risk_label"] for s in rng_samples)
    for k, v in sorted(cnt.items()):
        print(f"  {k}: {v}")

    all_samples = base_samples + rng_samples
    rng = random.Random(43)
    rng.shuffle(all_samples)
    with out_path.open("w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[v2] wrote {len(all_samples)} samples to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())