#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# process_numa.py — M3.52 S4 CPU 亲和性建议(2026-09-23)
#
# 目的:
#   - 列出当前 CPU 高占用进程 → 推荐 affinity mask(避免某个进程吃满所有核)
#   - **admin 限定**:实际 SetProcessAffinityMask 需要 admin,本脚本只**输出建议**
#   - perf_guard 在 high/critical 时附加 process_numa 建议,用户以 admin 启动
#     companion 时启用
#
# 用法:
#   python process_numa.py --list             # 列出 top 进程 + 推荐 affinity
#   python process_numa.py --pid 1234 --cores 4  # 给某 PID 推荐 4 核
#   python process_numa.py --apply --pid 1234 --cores 4  # 实际设置(需 admin)
#
# 决策规则(简化版 Process Lasso ProBalance):
#   - top 进程 CPU% >= 70% + 占 >= 4 核 → 建议限制到 50% 的核
#   - top 进程 CPU% >= 90% → 建议限制到 25% 的核
#   - 其他进程保持默认(全核可用)
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# M3.54:委托 PrisirAI process_controller_tools(跨平台 + admin 检测 + 持久化)
_PRISIRAI_PATH = (_HERE.parent / "mcp_prisiragent_server").resolve()
if str(_PRISIRAI_PATH) not in sys.path:
    sys.path.insert(0, str(_PRISIRAI_PATH))

# M3.59:复用 admin_state 单例,避免与 perf_guard 各自 ctypes
try:
    from admin_state import is_admin as _admin_state_is_admin  # noqa: E402
except Exception:  # noqa: BLE001
    _admin_state_is_admin = None  # import 失败时走 fallback


def _is_admin() -> bool:
    """Windows admin 检测(M3.59:复用 admin_state 单例,保留 ctypes fallback)。

    优先调 admin_state.is_admin()(缓存到进程级,避免重复 ctypes);
    若 admin_state 自身 import 失败(异常环境),退到直接 ctypes。
    """
    if _admin_state_is_admin is not None:
        try:
            return _admin_state_is_admin()
        except Exception:  # noqa: BLE001
            pass
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _set_affinity(pid: int, core_mask: int) -> bool:
    """M3.54:委托 PrisirAI process_cpu_limit_impl 执行 affinity(跨平台 + token 权限更稳)。

    原 ctypes Win32 SetProcessAffinityMask 实现保留为 fallback。
    """
    # 从 core_mask 算 cores 数(最低 1 bit set 数)
    if core_mask <= 0:
        print(f"❌ core_mask 必须 > 0", file=sys.stderr)
        return False
    cores = bin(core_mask).count("1")
    try:
        from process_controller_tools import process_cpu_limit_impl
        result = json.loads(process_cpu_limit_impl(pid, cores))
        ok = result.get("ok", False)
        if ok:
            print(f"✅ PrisirAI 已设置 pid={pid} cores={cores} mask=0x{core_mask:x}",
                  file=sys.stderr)
        else:
            # process_cpu_limit_impl 不返 error 字段(非用户路径处理),把整个 result 打 stderr 方便排查
            err_msg = result.get("error") or json.dumps(result, ensure_ascii=False)
            print(f"❌ PrisirAI 失败: {err_msg}", file=sys.stderr)
        return ok
    except Exception as e:
        # fallback:走原 ctypes 路径
        return _set_affinity_native(pid, core_mask)


def _set_affinity_native(pid: int, core_mask: int) -> bool:
    """原 ctypes Win32 SetProcessAffinityMask(需 admin)。作为 PrisirAI 失败时的 fallback。"""
    if not _is_admin():
        print(f"❌ 需要 admin 权限才能设置 affinity", file=sys.stderr)
        return False
    try:
        import ctypes
        PROCESS_SET_INFORMATION = 0x0200
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
        if not handle:
            print(f"❌ OpenProcess({pid}) 失败", file=sys.stderr)
            return False
        ok = kernel32.SetProcessAffinityMask(handle, core_mask)
        kernel32.CloseHandle(handle)
        return bool(ok)
    except Exception as e:
        print(f"❌ SetProcessAffinityMask 异常: {e}", file=sys.stderr)
        return False


def recommend_for_process(cpu_pct: float, total_cores: int = 6) -> dict:
    """根据 CPU% 推荐 affinity mask。

    Returns:
        {"recommended_cores": int, "core_mask": int, "reason": str}
    """
    if cpu_pct >= 90:
        cores = max(1, total_cores // 4)
        reason = f"CPU {cpu_pct:.0f}% 持续高负载,建议限制到 25% 的核"
    elif cpu_pct >= 70:
        cores = max(1, total_cores // 2)
        reason = f"CPU {cpu_pct:.0f}% 中高负载,建议限制到 50% 的核"
    elif cpu_pct >= 40:
        cores = max(2, int(total_cores * 0.66))
        reason = "CPU 中等负载,保持灵活调度"
    else:
        cores = total_cores
        reason = "CPU 低,不限"
    # core_mask = (1 << cores) - 1
    core_mask = (1 << cores) - 1
    return {
        "recommended_cores": cores,
        "core_mask": core_mask,
        "core_mask_hex": hex(core_mask),
        "reason": reason,
    }


def list_top(cpu_threshold: float = 5.0, top_n: int = 5) -> list[dict]:
    """列出 CPU% >= 阈值的进程,带 affinity 建议。"""
    import psutil
    total_cores = psutil.cpu_count(logical=True) or 6
    out = []
    procs = []
    for p in psutil.process_iter(['name', 'pid', 'username']):
        try:
            with p.oneshot():
                cpu = p.cpu_percent(interval=None)
                if cpu < cpu_threshold:
                    continue
                procs.append({
                    "name": p.info['name'],
                    "pid": p.info['pid'],
                    "username": p.info['username'],
                    "cpu_pct": cpu,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["cpu_pct"], reverse=True)
    for p in procs[:top_n]:
        rec = recommend_for_process(p["cpu_pct"], total_cores)
        out.append({**p, **rec, "total_cores": total_cores})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="process_numa — CPU 亲和性建议")
    ap.add_argument("--list", action="store_true", help="列出 top 进程 + 建议")
    ap.add_argument("--pid", type=int, help="对指定 PID 推荐")
    ap.add_argument("--cores", type=int, help="强制指定核心数(配合 --pid)")
    ap.add_argument("--apply", action="store_true", help="实际设置(需 admin)")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="CPU% 阈值(默认 5)")
    args = ap.parse_args()

    if args.list:
        items = list_top(cpu_threshold=args.threshold)
        if not items:
            print("✅ 无高 CPU 进程", file=sys.stderr)
            return 0
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    if args.pid:
        if args.cores is None:
            print("❌ --pid 必须配 --cores", file=sys.stderr)
            return 2
        mask = (1 << args.cores) - 1
        if args.apply:
            ok = _set_affinity(args.pid, mask)
            return 0 if ok else 1
        else:
            print(json.dumps({
                "pid": args.pid,
                "recommended_cores": args.cores,
                "core_mask_hex": hex(mask),
                "apply_command": f"python process_numa.py --apply --pid {args.pid} --cores {args.cores}",
                "admin_required": True,
                "delegated_to": "PrisirAI process_controller_tools.process_cpu_limit_impl",
            }, ensure_ascii=False, indent=2))
            return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())