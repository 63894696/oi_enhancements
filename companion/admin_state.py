#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# admin_state.py — M3.59 统一 admin 探测单例(2026-09-23)
#
# 目的:
#   - 进程启动时探测一次 admin(Windows token)
#   - 缓存结果到模块级,避免重复 ctypes 调用(perf_guard 热路径)
#   - 给所有"需要 admin 才能真生效"的模块(dry_run / Service mask / IFEO /
#     process_numa native fallback)提供统一查询入口
#   - fail-soft 包装:不强制 raise,可以是 warn + return bool
#
# 设计:
#   - is_admin()         走 ctypes shell32.IsUserAnAdmin,cache 到 _STATE
#   - get_state()        返 {is_admin, checked_at, source} 给 UI / 日志
#   - require_or_warn()  非 admin 时 log.warning + 返 False(给 perf_guard)
#   - require_admin()    非 admin 时 raise PermissionError(给强一致路径)
#
# 与 process_numa._is_admin 关系:
#   - process_numa 之前 line 38-44 独立实现,本次改复用本模块
#   - 保留 process_numa 内部 ctypes fallback 以防本模块 import 失败
#
# 用法:
#   from admin_state import is_admin, require_or_warn
#
#   if not require_or_warn("watchdog kill_mode=kill"):
#       return None  # 拒绝执行,fallback 到只记录
#
#   dry_run = not is_admin()  # 显式表达意图
#
# CLI:
#   python admin_state.py            # 打印当前 admin 状态
#   python admin_state.py --verbose   # 含 checked_at / source / cache age
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("prisiragent-companion.admin_state")

# ------------------------------------------------------------
# 模块级 cache(进程启动后第一探测固定,token 不会自动降权)
# ------------------------------------------------------------
_STATE: dict = {
    "is_admin": None,         # None=未探测,True/False=已探测
    "checked_at": 0.0,       # time.time() 时间戳
    "source": "",            # "shell32.IsUserAnAdmin" 或 "exception_fallback"
    "error": "",             # 探测失败的异常信息(若有)
}

# ------------------------------------------------------------
# 探测
# ------------------------------------------------------------
def _probe_admin() -> bool:
    """实际探测一次(走 ctypes Windows shell32.IsUserAnAdmin)。

    Returns:
        True if current process token is admin/elevated; False otherwise.
        任何异常(非 Windows / ctypes 加载失败)→ 返 False(保守,不假定 admin)
    """
    try:
        import ctypes
        result = bool(ctypes.windll.shell32.IsUserAnAdmin())
        _STATE["source"] = "shell32.IsUserAnAdmin"
        return result
    except Exception as e:  # noqa: BLE001
        # 非 Windows / ctypes 加载失败 / shell32 缺失 → 保守
        _STATE["source"] = "exception_fallback"
        _STATE["error"] = f"{type(e).__name__}: {e}"
        return False


def _ensure_probed() -> None:
    """首次调用 is_admin / get_state 时 lazy 探测一次。"""
    if _STATE["is_admin"] is None:
        _STATE["is_admin"] = _probe_admin()
        _STATE["checked_at"] = time.time()


# ------------------------------------------------------------
# 公共 API
# ------------------------------------------------------------
def is_admin() -> bool:
    """当前进程是否 admin(elevated token)。lazy 探测一次后 O(1) 读 cache。

    关键决策:缓存到进程生命周期内,避免 perf_guard 热路径每分钟 ctypes 调用。
    token 在运行中降权极罕见;若发生,需重启 companion。
    """
    _ensure_probed()
    return bool(_STATE["is_admin"])


def get_state() -> dict:
    """完整 admin 状态(给 UI / 日志 / API 端点)。

    Returns:
        {
            "is_admin": bool,
            "checked_at": float (time.time()),
            "checked_at_iso": str (ISO 8601 UTC),
            "source": str,
            "error": str (探测失败时非空),
            "cache_age_sec": float (从探测到现在秒数),
        }
    """
    _ensure_probed()
    now = time.time()
    return {
        "is_admin": bool(_STATE["is_admin"]),
        "checked_at": _STATE["checked_at"],
        "checked_at_iso": datetime.fromtimestamp(
            _STATE["checked_at"], tz=timezone.utc,
        ).isoformat(),
        "source": _STATE["source"],
        "error": _STATE["error"],
        "cache_age_sec": round(now - _STATE["checked_at"], 3) if _STATE["checked_at"] else 0.0,
    }


def reset_cache() -> None:
    """手动清 cache(测试用,生产不应调用)。"""
    _STATE["is_admin"] = None
    _STATE["checked_at"] = 0.0
    _STATE["source"] = ""
    _STATE["error"] = ""


def require_or_warn(action: str) -> bool:
    """非 admin 时 log.warning + 返 False;admin 时返 True。

    适用场景:perf_guard._trigger_kill_mode 等"非 admin 时应降级但不应 raise" 的路径。
    调用方拿到 False 应**主动**返回 None / False / 改走 dry_run 路径,
    不要 silently 继续(否则 watchdog_start_impl 会硬调 Windows API 失败)。

    Args:
        action: 描述要执行的动作(给日志用,如 "watchdog kill_mode=kill")

    Returns:
        True if admin(可继续执行真杀路径)
        False if 非 admin(应降级 / 返回失败 / 走 dry_run)
    """
    if is_admin():
        return True
    log.warning(
        "admin_state: %s 需要 admin 权限,当前进程非 admin — "
        "将降级到 dry_run / 仅记录(不真执行)。若需真生效,请以 admin 启动 companion。",
        action,
    )
    return False


def require_admin(action: str) -> None:
    """非 admin 时 raise PermissionError;admin 时 None。

    适用场景:强一致路径,如 process_numa._set_affinity_native、Service mask、
    IFEO debugger 注册。调用方明确知道非 admin 不应继续。

    Args:
        action: 描述要执行的动作(给错误信息用)

    Raises:
        PermissionError: 非 admin 时
    """
    if is_admin():
        return
    raise PermissionError(
        f"admin_state: {action} 需要 admin 权限,当前进程 token 是普通用户。"
        f"请以 admin 启动 companion / IDE / 任务计划程序。"
    )


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="M3.59 统一 admin 探测")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="含 checked_at / source / cache age")
    ap.add_argument("--reset", action="store_true",
                    help="清 cache(测试用,生产不应调)")
    ap.add_argument("--require", metavar="ACTION",
                    help="模拟 require_admin:非 admin 时 exit 1 + 报错")
    args = ap.parse_args()

    if args.reset:
        reset_cache()
        print("[reset] cache cleared")
        return 0

    state = get_state()

    if args.verbose:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "is_admin": state["is_admin"],
            "source": state["source"],
        }, ensure_ascii=False))
        print(f"详情:{state['checked_at_iso']}  cache_age={state['cache_age_sec']}s")

    if args.require:
        try:
            require_admin(args.require)
            print(f"✅ require_admin('{args.require}') OK")
            return 0
        except PermissionError as e:
            print(f"❌ {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())