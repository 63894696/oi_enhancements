#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_perf.py — M3.51 本地性能快照风险分类(2026-09-23)
#
# 目的:
#   - 对本地 perf_collector 采样的 JSON 快照做 5 类风险分级 + action
#   - perf schema: 5 类 safe/low/medium/high/critical + 4 action delete/review/keep/alert
#   - 实际 perf 数据用 action = keep/review/alert(delete 不适用 perf)
#
# 用法:
#   python classify_perf.py --file perf_samples.jsonl
#   python classify_perf.py --stdin < perf_samples.jsonl
#   python classify_perf.py --once   # 用 perf_collector 实时采 + 分类
#
# Python 嵌入:
#   from classify_perf import classify_perf
#   out = classify_perf(adapter, sample_dict)
#   out = {"risk": "critical", "action": "alert", "risk_conf": 0.92,
#          "action_conf": 0.88, "raw": "...", "latency_ms": 800, "parse_fail": False}
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


# ------------------------------------------------------------
# 与 data_prep_perf._synth + data_prep_perf_v2._gen 对齐(单行紧凑短语)
# ------------------------------------------------------------
def _build_text(sample: dict) -> str:
    """sample = perf_collector 的 dict(含 ts/cpu/memory/net/crash/system)。

    **M3.51 S9-retry 修复**(2026-09-23):
        旧版用 `_format_sample_text`(多行结构化:时间戳/CPU/内存/网络/系统/BugCheck),
        但训练数据 99.5% 是 `_synth` 单行 `性能采样: 短语...` 格式。模型从未见过
        这种结构化输入,所以默认降档到 medium。
        改为生成**单行紧凑短语**,对齐训练时形态。
    """
    cpu = sample.get("cpu", {})
    mem = sample.get("memory", {})
    net = sample.get("net", {})
    crash = sample.get("crash", {})
    sys_ = sample.get("system", {})
    proc = sample.get("proc", {})  # M3.52 S1
    boot_s = sys_.get("uptime_s", 0)
    avail = mem.get("available_gb", 0)
    used_pct = mem.get("used_pct", 0)
    cpu_pct = cpu.get("pct", 0)
    bugcheck = crash.get("bugcheck_count", 0)
    kp41 = crash.get("kp41_count", 0)
    disconnected_taps = []
    for nic in net.get("nics", []):
        if nic.get("isup") is False:
            name = (nic.get("nic") or "").lower()
            if any(t in name for t in ("tap0901", "tapprotonvpn", "vktap", "tap", "tun")):
                disconnected_taps.append(name)

    # M3.52 S1:process attribution(谁在吃 CPU/MEM)
    top_procs = proc.get("top", []) if isinstance(proc, dict) else []
    procs_attr = ""
    if top_procs:
        # 取 cpu_pct >= 5% 的进程(过滤背景噪声)
        hot = [p for p in top_procs if p.get("cpu_pct", 0) >= 5.0][:3]
        if hot:
            summary = ", ".join(f"{p.get('name','?')}({p.get('cpu_pct',0):.0f}%)"
                                for p in hot)
            procs_attr = f", top 进程: {summary}"

    parts = []

    # 严重度排序:critical/high anchor 优先(对齐 v2 抽象模板)
    if boot_s < 60 and bugcheck >= 2:
        parts.append(f"boot 后 {boot_s} 秒 BugCheck 0x3b ACCESS_VIOLATION, "
                     f"RIP ndis.sys+0x8e61b7, Kernel-Power {kp41 or bugcheck} 触发")
    elif boot_s < 60 and bugcheck >= 1:
        parts.append(f"boot {boot_s}s 内出现 1 次 BugCheck, Kernel-Power 41 触发, "
                     f"NIC compliance 告警")
    elif bugcheck >= 5:
        parts.append(f"10:49 蓝屏 BSOD BugCheck 0x3b SYSTEM_SERVICE_EXCEPTION "
                     f"at ndis.sys+0x8e61b7, NIC compliance detected")
    elif bugcheck >= 1:
        parts.append(f"7 天内 {bugcheck} 次 BugCheck 0x3b, Kernel-Power {kp41 or bugcheck} "
                     f"触发, NIC compliance 告警")
    elif cpu_pct >= 95 and avail < 3:
        parts.append(f"cpu {cpu_pct:.0f}% 持续 12 秒, 内存 {used_pct:.0f}% 仅剩 {avail}GB, "
                     f"ProBalance 已强制降级 5 个后台进程")
    elif cpu_pct >= 80 and avail < 5:
        parts.append(f"cpu {cpu_pct:.0f}% 持续 {int(20)} 秒, 内存剩余 {avail}GB, "
                     f"ProBalance 已强制降级 2 个后台进程")

    # TAP 信息
    if len(disconnected_taps) >= 3:
        parts.append(f"{len(disconnected_taps)} 个 TAP miniport disconnected "
                     f"(tap0901 / tapprotonvpn / vktap), ndis 启动期扫描慢")
    elif len(disconnected_taps) >= 1:
        parts.append(f"{len(disconnected_taps)} 个 TAP disconnected, NIC compliance, "
                     f"cpu {cpu_pct:.0f}% 中等负载")

    # CPU + 内存 fallback(无 critical anchor)
    if not parts:
        if cpu_pct >= 85:
            parts.append(f"cpu {cpu_pct:.0f}% 持续 {int(5)} 秒, ProBalance 触发一次降级{procs_attr}")
        elif cpu_pct >= 50:
            parts.append(f"cpu {cpu_pct:.0f}% 持续 -1 分钟, 内存空闲 {avail}GB, ProBalance 解禁, 一切正常{procs_attr}")
        elif cpu_pct >= 30:
            parts.append(f"cpu {cpu_pct:.0f}%, 内存空闲 {avail}GB, 容器启动一次成功{procs_attr}")
        else:
            if avail >= 20:
                parts.append(f"cpu {cpu_pct:.0f}%, 内存 {avail}GB available, "
                             f"网络空闲, ProBalance 未触发, 一切正常{procs_attr}")
            else:
                parts.append(f"cpu {cpu_pct:.0f}%, 内存空闲 {avail}GB, 一切正常{procs_attr}")

    return f"性能采样: {', '.join(parts)}\n问: 这个性能快照的风险等级和推荐处理动作?"


# 复用 classify_log 的 _parse_output(perf schema 同 safety+action 结构)
_PARSE_PAT = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_output(raw: str):
    """抽 (risk, jb_bool, action, risk_conf, jb_conf, action_conf)。复读时只取第一段。"""
    idx = raw.lower().find("safety:")
    if idx < 0:
        return None, None, None, None, None, None
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = _PARSE_PAT.search(seg)
    if not m:
        return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return risk, jb, action, risk_conf, jb_conf, action_conf


VALID_RISKS = {"safe", "low", "medium", "high", "critical"}
VALID_ACTIONS = {"delete", "review", "keep", "alert"}

# M3.87 P2:置信度阈值。<0.85 时标"不确定",action 强制 review
# (perf_conf_v3 safe/high/medium 100% ACC 但 critical 7/10 = 30% 误判,
# 低 conf 不能直接触发 alert/kill_mode 等不可逆动作)
PERF_CONF_THRESHOLD = 0.85


def classify_perf(adapter, sample: dict) -> dict:
    """对一份 perf sample 分类。

    adapter = LoadedAdapter 实例(get_adapter("perf_conf") 取)
    sample = perf_collector 输出的 dict

    M3.87 P2:risk_conf < 0.85 时,加 risk_confidence="low" 标记,
    action 强制 review(不让 alert/kill_mode 等不可逆动作触发)。
    risk 字段保持 5 类有限集不变(下游消费方代码不破坏)。
    """
    text = _build_text(sample)
    t0 = time.time()
    res = adapter.classify(text)
    dt_ms = int((time.time() - t0) * 1000)
    raw = res["raw"]
    risk, jb, action, rc, jc, ac = _parse_output(raw)
    # 兜底:风险未识别 → medium / review
    if risk not in VALID_RISKS:
        risk = "medium"
    # M3.87 P2:置信度分级
    if rc is None:
        risk_confidence = "none"
    elif rc >= 0.85:
        risk_confidence = "high"
    elif rc >= 0.6:
        risk_confidence = "medium"
    else:
        risk_confidence = "low"
    # M3.87 P2:低/中/none conf 时把 action 强制 review(不让 alert/kill_mode 触发)
    # risk 字段保持原值(下游可读),但配套的 action 必须保守
    if risk_confidence != "high":
        # 即使 risk 报 critical,action 也只能 review(不可逆动作禁用)
        action = "review"
    elif action not in VALID_ACTIONS:
        # perf 实际不会出 delete,3 类映射
        action = {"safe": "keep", "low": "keep"}.get(risk, "review")
    return {
        "risk": risk,
        "action": action,
        "jailbreak": "yes" if jb else "no",
        "risk_conf": rc,
        "risk_confidence": risk_confidence,  # M3.87 P2 新增
        "uncertain": risk_confidence in ("low", "medium", "none"),  # M3.87 P2 新增
        "action_conf": ac,
        "jb_conf": jc,
        "raw": raw,
        "tokens": res["tokens"],
        "latency_ms": dt_ms,
        "parse_fail": rc is None,
    }


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def _load_samples(args) -> list[dict]:
    items: list[dict] = []
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append(d)
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append(d)
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="perf_conf 推理入口")
    ap.add_argument("--file", help="perf sample JSONL 文件")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 JSONL")
    ap.add_argument("--once", action="store_true", help="实时采一次 + 分类")
    args = ap.parse_args()

    # 加载 adapter
    from adapter_registry import get_adapter
    adapter = get_adapter("perf_conf")
    print(f"[classify_perf] adapter loaded: {adapter.spec.name}", file=sys.stderr)

    if args.once:
        from perf_collector import collect_with_event
        sample = collect_with_event()
        out = classify_perf(adapter, sample)
        print(json.dumps({"sample": sample, "result": out}, ensure_ascii=False, indent=2))
        return 0

    items = _load_samples(args)
    if not items:
        print("no samples to classify", file=sys.stderr)
        return 1
    n = 0
    parse_fail = 0
    t0 = time.time()
    for sample in items:
        out = classify_perf(adapter, sample)
        if out["parse_fail"]:
            parse_fail += 1
        # 简化输出
        print(json.dumps({
            "ts": sample.get("ts", "?"),
            "risk": out["risk"],
            "action": out["action"],
            "risk_conf": out["risk_conf"],
            "action_conf": out["action_conf"],
            "latency_ms": out["latency_ms"],
        }, ensure_ascii=False))
        n += 1
    dt = time.time() - t0
    print(f"[classify_perf] {n} samples in {dt:.1f}s, p50={int(dt*1000/n)}ms, parse_fail={parse_fail}/{n}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())