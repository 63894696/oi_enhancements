#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_subprocess_runner.py — M3.78 perf_conf_v3 子进程隔离 runner

目的:
  - watchdog loop 每 N 轮调 1 次,采系统 perf snapshot + 调 perf_conf_v3 算 risk
  - **关键:adapter 加载在子进程**,父进程(watchdog/MCP server)不污染内存
    - perf_conf_v3 adapter 17.5MB + base model ~850MB
    - 父进程用 subprocess.run() 调本脚本,只读 stdout JSON,内存代价 ~0
  - 失败/timeout → stdout JSON 含 error 字段,父进程只记 stderr 不触发

用法(供 watchdog_loop subprocess.run 调用):
    python perf_subprocess_runner.py

设计:
  - 单文件脚本,无参数,无 CLI
  - exit code:0 = 成功(含 risk),1 = fatal error(stdout 仍写 JSON error)
  - 复用 companion/classify_perf.classify_perf(已有 _build_text + adapter classify)
  - 复用 companion/perf_collector.collect_with_event(已有 cpu/mem/net/crash/system/proc)

stdout JSON schema:
    {
      "ok": True/False,
      "risk": "safe"/"low"/"medium"/"high"/"critical",
      "action": "keep"/"review"/"alert",
      "risk_conf": float,
      "action_conf": float,
      "jailbreak": "yes"/"no",
      "latency_ms": int,
      "parse_fail": bool,
      "backend": "perf_conf_v3",
      "sample": <精简 sample dict>,  # 父进程供 _handle_perf_risk 用
      "error": str or None,
    }
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 把 companion/ 加到 path,导入 classify_perf + perf_collector
_COMPANION_DIR = Path("C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))


def _emit(payload: dict) -> None:
    """写 JSON 到 stdout + flush。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.flush()


def run_once() -> int:
    """采一次 + 分类,返 exit code(0=ok, 1=fatal)。"""
    t0 = time.time()
    try:
        from adapter_registry import get_adapter
        from classify_perf import classify_perf
        from perf_collector import collect_with_event
    except Exception as e:
        _emit({"ok": False, "error": f"import 失败: {type(e).__name__}: {e}",
               "backend": None})
        return 1

    try:
        adapter = get_adapter("perf_conf_v3")
    except Exception as e:
        _emit({"ok": False, "error": f"adapter 加载失败: {type(e).__name__}: {e}",
               "backend": None})
        return 1

    try:
        sample = collect_with_event()
    except Exception as e:
        _emit({"ok": False, "error": f"perf_collect 失败: {type(e).__name__}: {e}",
               "backend": "perf_conf_v3"})
        return 1

    try:
        out = classify_perf(adapter, sample)
    except Exception as e:
        _emit({"ok": False, "error": f"classify_perf 失败: {type(e).__name__}: {e}",
               "backend": "perf_conf_v3"})
        return 1

    elapsed_ms = int((time.time() - t0) * 1000)

    # 简化 sample 给父进程(避免 stdin/stdout 太大):
    # 只保留 net.nics + crash + cpu/memory pct,父进程用于判断 TAP 路径
    sample_compact = {
        "ts": sample.get("ts"),
        "cpu": {"pct": sample.get("cpu", {}).get("pct")},
        "memory": {"used_pct": sample.get("memory", {}).get("used_pct")},
        "net": {
            "nics": [
                {"nic": n.get("nic"), "isup": n.get("isup")}
                for n in sample.get("net", {}).get("nics", [])
            ]
        },
        "crash": {
            "bugcheck_count": sample.get("crash", {}).get("bugcheck_count"),
            "kp41_count": sample.get("crash", {}).get("kp41_count"),
        },
        "system": {
            "uptime_s": sample.get("system", {}).get("uptime_s"),
        },
    }

    _emit({
        "ok": True,
        "risk": out.get("risk"),
        "action": out.get("action"),
        "risk_conf": out.get("risk_conf"),
        "action_conf": out.get("action_conf"),
        "jailbreak": out.get("jailbreak"),
        "latency_ms": elapsed_ms,
        "parse_fail": out.get("parse_fail", True),
        "backend": "perf_conf_v3",
        "sample": sample_compact,
        "error": None,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())