#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_e2e.py — M3.69 perf_alert 端到端测试(2026-09-24)
#
# 3 case:
#   1. --help 打印完整
#   2. --once 真采一次 + 输出风险等级
#   3. --dry-run --iterations 3 跑 3 次不出告警
#
# 不写 unit tests(用户要求 e2e only)。
# 设计:每个 case 用 subprocess 跑 main.py,验证 stdout / exit code。
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
MAIN = str(_PROJECT / "main.py")
PYTHON = sys.executable


# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------
def run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """跑一次 main.py,捕 stdout/stderr/returncode。"""
    t0 = time.time()
    cp = subprocess.run(
        [PYTHON, MAIN, *args],
        cwd=str(_PROJECT),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    dt = time.time() - t0
    cp.elapsed = dt  # type: ignore[attr-defined]
    return cp


def banner(name: str) -> None:
    print(f"\n=== {name} ===")


def assert_(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  OK: {msg}")


# ----------------------------------------------------------------
# case 1: --help
# ----------------------------------------------------------------
def case_help() -> None:
    banner("case 1: --help")
    cp = run(["--help"], timeout=30)
    print(cp.stdout)
    print(f"[exit={cp.returncode} elapsed={cp.elapsed:.1f}s]",
          file=sys.stderr)
    assert_(cp.returncode == 0, "exit code = 0")
    assert_("--interval" in cp.stdout, "--interval 在 help 中")
    assert_("--spec" in cp.stdout, "--spec 在 help 中")
    assert_("--watchdog" in cp.stdout, "--watchdog 在 help 中")
    assert_("--dry-run" in cp.stdout, "--dry-run 在 help 中")
    assert_("--iterations" in cp.stdout, "--iterations 在 help 中")
    assert_("--once" in cp.stdout, "--once 在 help 中")
    assert_("--output" in cp.stdout, "--output 在 help 中")
    assert_("--ws" in cp.stdout, "--ws 在 help 中")
    assert_("perf_conf_v3" in cp.stdout, "默认 spec = perf_conf_v3 在 help 中")


# ----------------------------------------------------------------
# case 2: --once 真采一次
# ----------------------------------------------------------------
def case_once(tmp: Path) -> None:
    banner("case 2: --once (real sample + classify)")
    out_jsonl = tmp / "once.jsonl"
    if out_jsonl.exists():
        out_jsonl.unlink()
    cp = run(["--once", "--output", str(out_jsonl)], timeout=180)
    print(cp.stdout)
    if cp.stderr:
        print("[stderr]", cp.stderr, file=sys.stderr)
    print(f"[exit={cp.returncode} elapsed={cp.elapsed:.1f}s]",
          file=sys.stderr)
    assert_(cp.returncode == 0, "exit code = 0")
    # 必须出现 risk: 行
    assert_("risk" in cp.stdout.lower(), "stdout 包含 'risk'")

    # 必须写出 JSONL
    assert_(out_jsonl.exists(), f"output 文件被写出: {out_jsonl}")
    lines = [ln for ln in out_jsonl.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert_(len(lines) >= 1, f"JSONL 至少 1 行(实际 {len(lines)})")
    rec = json.loads(lines[-1])
    assert_("risk" in rec, "JSONL 记录有 risk 字段")
    assert_("cpu_pct" in rec, "JSONL 记录有 cpu_pct 字段")
    assert_("mem_pct" in rec, "JSONL 记录有 mem_pct 字段")
    print(f"  样本 risk={rec['risk']} action={rec['action']} "
          f"cpu={rec['cpu_pct']}% mem={rec['mem_pct']}% "
          f"latency={rec['latency_ms']}ms", file=sys.stderr)


# ----------------------------------------------------------------
# case 3: --dry-run --iterations 3 不出告警
# ----------------------------------------------------------------
def case_dry_run(tmp: Path) -> None:
    banner("case 3: --dry-run --iterations 3 (no alert actions)")
    out_jsonl = tmp / "dry.jsonl"
    if out_jsonl.exists():
        out_jsonl.unlink()
    # CPU 推理 ~30s/次,所以 interval=35s 跑 3 次 ≈ 90s
    cp = run(["--dry-run", "--iterations", "3",
              "--interval", "35",
              "--output", str(out_jsonl)],
             timeout=300)
    print(cp.stdout)
    if cp.stderr:
        print("[stderr]", cp.stderr, file=sys.stderr)
    print(f"[exit={cp.returncode} elapsed={cp.elapsed:.1f}s]",
          file=sys.stderr)
    assert_(cp.returncode == 0, "exit code = 0")
    # 不该出现 "触发 alert" / "[WATCHDOG]" 这种告警动作行
    forbidden = ["触发 alert", "[WATCHDOG]", "[WS] pushed"]
    for f in forbidden:
        assert_(f not in cp.stdout,
                f"dry-run 不应输出: {f!r}")
    # 但应该有 header 和 3 条采样行(每条含 "CPU" "mem")
    assert_("[perf_alert] interval=35s" in cp.stdout,
            "header 行 '[perf_alert] interval=35s' 出现")
    cpu_count = cp.stdout.count("CPU ")
    assert_(cpu_count >= 3, f"至少 3 条采样行(实际 CPU 行={cpu_count})")
    # reached max_iter 退出
    assert_("reached max_iter=3" in cp.stdout,
            "正常退出(reached max_iter=3)")
    # output JSONL 也至少 3 条
    lines = [ln for ln in out_jsonl.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert_(len(lines) >= 3, f"JSONL 至少 3 行(实际 {len(lines)})")


# ----------------------------------------------------------------
# runner
# ----------------------------------------------------------------
def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            case_help()
            case_once(tmp)
            case_dry_run(tmp)
        except AssertionError as e:
            print(f"\nFAIL: {e}", file=sys.stderr)
            return 1
    print("\nALL E2E OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
