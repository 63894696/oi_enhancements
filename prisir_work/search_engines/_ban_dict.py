"""_ban_dict.py — 仿 SearXNG ban_time_on_fail 的失败阶梯 ban 字典。

阶梯:
  第 1 次失败:5s(避免反复打)
  第 2 次失败:120s(2 分钟)
  第 3 次失败:3600s(1 小时,疑似 IP 风控)
  第 4 次失败:86400s(24 小时,疑似 CAPTCHA / 封 IP)
  第 5+ 次:封顶 24h

每次成功清空 ban 累积。

使用:
    b = BanDict()
    if b.is_banned(name):
        return []
    try:
        result = ...
        b.record_success(name)
    except Exception:
        b.record_failure(name)
        return []
"""
from __future__ import annotations

import time

_BAN_INITIAL = 5.0
_BAN_ESCALATION = (120.0, 3600.0, 86400.0)


class BanDict:
    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._fail_count: dict[str, int] = {}

    def is_banned(self, name: str) -> bool:
        until = self._until.get(name, 0.0)
        return time.monotonic() < until

    def record_failure(self, name: str) -> None:
        n = self._fail_count.get(name, 0) + 1
        self._fail_count[name] = n
        if n == 1:
            ban = _BAN_INITIAL
        elif n - 1 < len(_BAN_ESCALATION):
            ban = _BAN_ESCALATION[n - 2]
        else:
            ban = _BAN_ESCALATION[-1]
        self._until[name] = time.monotonic() + ban

    def record_success(self, name: str) -> None:
        self._fail_count.pop(name, None)
        self._until.pop(name, None)

    def stats(self) -> dict[str, dict[str, float]]:
        """当前 ban 状态快照(调试 / 观测)。"""
        now = time.monotonic()
        out: dict[str, dict[str, float]] = {}
        for name in set(self._fail_count) | set(self._until):
            out[name] = {
                "fail_count": float(self._fail_count.get(name, 0)),
                "remaining_s": max(0.0, self._until.get(name, 0.0) - now),
            }
        return out

    def reset(self) -> None:
        """测试用。"""
        self._until.clear()
        self._fail_count.clear()


# 单例(全模块共享一个 ban 字典,跨 provider 失效状态)
_BAN = BanDict()


def get_ban_dict() -> BanDict:
    return _BAN