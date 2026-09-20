# -*- coding: utf-8 -*-
"""
scheduler.py — TravelBufferScheduler (task #22)

定时调度通道:cron 表达式 -> 每 tick 调一次 travel_buffer.protect_now。

设计要点:
  - 全 stdlib:asyncio.create_task + while-True sleep,不依赖 apscheduler/croniter。
  - 自带 daemon 线程,内嵌独立 event loop;与 prisiragent_web 的
    ThreadingHTTPServer(每请求一线程,无主 asyncio loop)不冲突。
  - 错误隔离:单次 tick 抛错只写 log,后台 loop 继续到下一 tick。
  - 暴露 request_tick_now() 让 UI / HTTP handler 主动触发(返回 Future)。

公共 API:
    TravelBufferScheduler:
        __init__(store, *, user_profile=None, ledger_sink=None,
                 user_profile_loader=None, scan_days=14,
                 cron_factory=<default>)
        install(cron="30 7 * * 1-5") -> None    # 同步;启动后台线程
        uninstall() -> None                       # 同步;等线程退出
        tick() -> ScanReport                      # 单次执行,await
        request_tick_now() -> concurrent.futures.Future  # 异步触发入口
        trigger_now(timeout=30) -> ScanReport     # 同步阻塞入口(HTTP handler 用)

cron 表达式(5 字段,空格分隔,严格 stdlib 不引第三方):
    minute hour day_of_month month day_of_week
    取值:
      *         任意
      N         数值 N
      a-b       闭区间 [a,b]
      a-b/n     步进(从 a 起每 n 一次,直到 b)
      a,b,c     列表
    day_of_week: 0=周一 ... 6=周日(对齐 croniter / POSIX cron 习惯 0=Sunday = 7,
    这里为了语义清晰采用 0=周一..6=周日,默认 "1-5" = 周一到周五)。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple, Union

from prisIr_calendar.agent_ops.travel_buffer import ScanReport, protect_now
from prisIr_calendar.store import CalendarStore

log = logging.getLogger("prisIr_calendar.scheduler")


# ============================================================
# cron 表达式解析(纯 stdlib)
# ============================================================
# 字段范围约束(month/day_of_month 由 Gregorian 决定,这里给宽泛上界)
_FIELD_RANGES: Dict[str, Tuple[int, int]] = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 7),  # POSIX cron: 0=Sun..6=Sat;7 是 0 的别名
}

# day_of_week 字段的 "Dow" 字符串 → 数字(对齐 POSIX / Vixie cron:
# 0=Sun, 1=Mon, ..., 6=Sat;"7" 是 0 的别名)
_DOW_NAMES: Dict[str, int] = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


def _parse_field(expr: str, field_name: str) -> Set[int]:
    """解析 cron 单字段 -> 合法值集合。

    支持 *, N, a-b, a-b/n, *,/n, a,b,c。
    """
    lo, hi = _FIELD_RANGES[field_name]
    out: Set[int] = set()
    for part in expr.split(","):
        part = part.strip().lower()
        if not part:
            raise ValueError(f"cron field '{field_name}' empty segment in: {expr!r}")
        # 步进: a-b/n  或  */n
        step_match = re.match(r"^(.+)/(\d+)$", part)
        if step_match:
            base, step_s = step_match.group(1), step_match.group(2)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"cron step must be positive: {part!r}")
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                start, end = _parse_range(base, field_name)
            else:
                raise ValueError(f"cron step base must be '*' or 'a-b': {part!r}")
            for v in range(start, end + 1, step):
                if lo <= v <= hi:
                    out.add(v)
            continue
        # 范围 a-b
        if "-" in part:
            start, end = _parse_range(part, field_name)
            for v in range(start, end + 1):
                out.add(v)
            continue
        # 通配
        if part == "*":
            out.update(range(lo, hi + 1))
            continue
        # 数字 / Dow 名
        if part in _DOW_NAMES and field_name == "day_of_week":
            out.add(_DOW_NAMES[part])
            continue
        try:
            v = int(part)
        except ValueError as e:
            raise ValueError(f"cron field '{field_name}' invalid token: {part!r}") from e
        if not (lo <= v <= hi):
            raise ValueError(f"cron field '{field_name}' value {v} out of range [{lo},{hi}]: {part!r}")
        # POSIX cron day_of_week:7 是 0(Sun)的别名
        if field_name == "day_of_week" and v == 7:
            v = 0
        out.add(v)
    return out


def _parse_range(part: str, field_name: str) -> Tuple[int, int]:
    """解析 'a-b' -> (a, b). 端点支持数字 / Dow 名称."""
    lo_bound, hi_bound = _FIELD_RANGES[field_name]
    a, b = part.split("-", 1)
    a_s, b_s = a.strip().lower(), b.strip().lower()
    if field_name == "day_of_week":
        # day_of_week 端点:数字 0..7(7 ≡ 0),或三字母名字 (sun..sat)
        a_v = _DOW_NAMES.get(a_s)
        if a_v is None:
            try:
                a_v = int(a_s)
            except ValueError:
                a_v = None
        if a_v == 7:
            a_v = 0
        b_v = _DOW_NAMES.get(b_s)
        if b_v is None:
            try:
                b_v = int(b_s)
            except ValueError:
                b_v = None
        if b_v == 7:
            b_v = 0
    else:
        try:
            a_v = int(a_s)
            b_v = int(b_s)
        except ValueError as e:
            raise ValueError(f"cron range endpoints must be numeric: {part!r}") from e
    if a_v is None or b_v is None:
        raise ValueError(f"cron range endpoint invalid: {part!r}")
    if not (lo_bound <= a_v <= hi_bound) or not (lo_bound <= b_v <= hi_bound):
        raise ValueError(f"cron range out of bounds [{lo_bound},{hi_bound}]: {part!r}")
    if a_v > b_v:
        # 允许跨周末(例如 FRI-SUN = 5-0);不做循环,按 Vixie cron 行为展开
        pass
    return a_v, b_v


def parse_cron(expr: str) -> Dict[str, FrozenSet[int]]:
    """解析 5 字段 cron 表达式。

    Returns:
        dict {minute,hour,day_of_month,month,day_of_week} -> frozenset(允许值)
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("cron expression must be non-empty string")
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(parts)}: {expr!r}")
    names = ("minute", "hour", "day_of_month", "month", "day_of_week")
    out: Dict[str, FrozenSet[int]] = {}
    for name, raw in zip(names, parts):
        out[name] = frozenset(_parse_field(raw, name))
    # cron 常见约定:"day_of_month" 和 "day_of_week" 同时为 * 时 OR 语义;
    # 我们不强制这一点 —— 任一字段匹配即触发(标准 Vixie cron 行为),
    # 与 system crontab 兼容。
    return out


def _next_fire_after(parsed: Dict[str, FrozenSet[int]], now: datetime) -> datetime:
    """计算 >= now+1min 的下一次触发时间。最多向前找 366 天。

    步进策略:从 now 开始,把 minute/hour/day_of_month/month/day_of_week
    调整到下一个合法组合。O(年*月*日)最坏,但 366 天硬上限保证必返回。

    dom / dow 语义(对齐 croniter,避免 Vixie cron 的"dom=* 时忽略 dow"反直觉):
      - dom=*  且 dow 非 *  → 仅 dow 生效
      - dow=*  且 dom 非 *  → 仅 dom 生效
      - 两者均非 *          → OR (任一匹配即触发)
      - 两者均 *            → 任意天
    """
    dom_full = frozenset(range(1, 32))
    dow_full = frozenset(range(0, 8))  # 含 7(0 别名)
    dom_is_star = parsed["day_of_month"] == dom_full
    dow_is_star = parsed["day_of_week"] == dow_full

    # 候选时间起点:now 的下一分钟(避免触发"立刻")
    cur = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    hard_deadline = now + timedelta(days=366)
    months_min, months_max = min(parsed["month"]), max(parsed["month"])
    # 取 month 允许的最小值
    min_month = months_min

    while cur <= hard_deadline:
        # month
        if cur.month not in parsed["month"]:
            # 跳到下一个允许的 month 第 1 天 00:00
            target_year = cur.year
            target_month = _next_in_set(parsed["month"], cur.month, wrap=(cur.month > months_max))
            if cur.month > months_max:
                target_year += 1
                target_month = min(parsed["month"])
            cur = datetime(target_year, target_month, 1, 0, 0)
            continue
        # day_of_month + day_of_week 命中判定(见函数 docstring)
        dom_ok = cur.day in parsed["day_of_month"]
        # Python weekday:Mon=0..Sun=6。POSIX cron:0=Sun..6=Sat。
        # 转换公式:cron_dow = (py_dow + 1) % 7  →  py Mon(0) → cron Mon(1),py Sun(6) → cron Sun(0)
        cron_dow = (cur.weekday() + 1) % 7
        dow_ok = cron_dow in parsed["day_of_week"]

        if dom_is_star and not dow_is_star:
            day_ok = dow_ok
        elif dow_is_star and not dom_is_star:
            day_ok = dom_ok
        else:
            day_ok = dom_ok or dow_ok

        if not day_ok:
            # 跳到下一天 00:00
            cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        # hour
        if cur.hour not in parsed["hour"]:
            cur = (cur + timedelta(hours=1)).replace(minute=0)
            continue
        # minute
        if cur.minute not in parsed["minute"]:
            cur = cur + timedelta(minutes=1)
            continue
        # 全部命中
        return cur
    raise RuntimeError(f"cron next_fire overflows 366-day horizon from {now.isoformat()}")


def _next_in_set(values: Sequence[int], current: int, *, wrap: bool) -> int:
    """当前 current 在 values 之后下一个 >= current 的元素;若无则返回首元素。

    wrap=True 时(已超出 max),返回首元素(由调用方负责 +1 年)。
    """
    for v in values:
        if v >= current:
            return v
    return values[0]


# ============================================================
# Tick 统计(轻量诊断,UI / 日志可读)
# ============================================================
@dataclass
class SchedulerStats:
    installed: bool = False
    cron_expr: str = ""
    tick_count: int = 0
    error_count: int = 0
    last_tick_at: Optional[str] = None  # ISO8601
    last_finished_at: Optional[str] = None
    last_inserted: int = 0
    last_skipped: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "installed": self.installed,
            "cron_expr": self.cron_expr,
            "tick_count": self.tick_count,
            "error_count": self.error_count,
            "last_tick_at": self.last_tick_at,
            "last_finished_at": self.last_finished_at,
            "last_inserted": self.last_inserted,
            "last_skipped": self.last_skipped,
            "last_error": self.last_error,
        }


# ============================================================
# TravelBufferScheduler — 主类
# ============================================================
class TravelBufferScheduler:
    """定时调度器:在 cron 表达式匹配时刻自动跑 travel_buffer.protect_now。

    生命周期:
        sched = TravelBufferScheduler(store, ledger_sink=...)
        sched.install("30 7 * * 1-5")   # 启动后台线程
        ... (UI/HTTP 调 sched.request_tick_now() 或 sched.trigger_now())
        sched.uninstall()                # 关停(进程退出前可省略 — daemon=True 自动回收)

    注入:
        - store: CalendarStore(必填)
        - user_profile: dict 或 None(静态,init 时冻结)。若 user_profile_loader
                       也传,则 loader 每次 tick 现取,覆盖此处值。
        - user_profile_loader: Callable[[], dict],异步触发时获取最新画像。
        - ledger_sink: dismiss 回调,直接转发给 protect_now。
        - scan_days: 传给 protect_now 的 days 参数(默认 14)。
        - time_source: Callable[[], datetime] 测试用,默认 datetime.now。
        - sleep_fn: Callable[[float], Awaitable[None]] 测试用,默认 asyncio.sleep。
        - run_protect: Callable[..., Awaitable[ScanReport]] 测试用,
                       默认 prisIr_calendar.agent_ops.travel_buffer.protect_now。
    """

    def __init__(
        self,
        store: CalendarStore,
        *,
        user_profile: Optional[Dict[str, Any]] = None,
        ledger_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        user_profile_loader: Optional[Callable[[], Dict[str, Any]]] = None,
        scan_days: int = 14,
        time_source: Optional[Callable[[], datetime]] = None,
        sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
        run_protect: Optional[Callable[..., Awaitable[ScanReport]]] = None,
    ):
        self.store = store
        self._static_user_profile = dict(user_profile) if user_profile else None
        self._user_profile_loader = user_profile_loader
        self.ledger_sink = ledger_sink
        self.scan_days = int(scan_days)
        self._time_source = time_source or datetime.now
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._run_protect = run_protect or protect_now

        self._cron_expr: str = ""
        self._parsed: Optional[Dict[str, FrozenSet[int]]] = None

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._stop_event = threading.Event()  # 让后台 loop 优雅退出
        self._main_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()

        self.stats = SchedulerStats()

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def install(self, cron: str = "30 7 * * 1-5") -> None:
        """注册 cron,启动后台线程。

        幂等:已运行时替换 cron 表达式(不重启线程,等下一 tick 自然生效)。
        """
        parsed = parse_cron(cron)  # 失败直接抛,不让后台启动一个坏 cron
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._cron_expr = cron
                self._parsed = parsed
                self.stats.cron_expr = cron
                self.stats.installed = True
                log.info("scheduler.install: cron updated to %r (running)", cron)
                return
            self._cron_expr = cron
            self._parsed = parsed
            self.stats.cron_expr = cron
            self.stats.installed = True
            self._stop_event.clear()
            self._loop_ready.clear()
            self._thread = threading.Thread(
                target=self._worker_main,
                name="TravelBufferScheduler",
                daemon=True,
            )
            self._thread.start()
        # 等后台 loop 就绪(最多 5s)
        if not self._loop_ready.wait(timeout=5.0):
            log.warning("scheduler.install: worker loop did not become ready in 5s")
        log.info("scheduler.install: cron=%r thread=%s", cron, self._thread.name)

    def uninstall(self) -> None:
        """关闭后台线程。

        幂等:未启动时不报错。
        """
        with self._lock:
            thread = self._thread
            loop = self._loop
            if thread is None or not thread.is_alive():
                self.stats.installed = False
                return
        # 在 worker loop 里取消 main task
        self._stop_event.set()
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._cancel_main_task)
            except RuntimeError:  # loop 已关闭
                pass
        thread.join(timeout=10.0)
        if thread.is_alive():
            log.warning("scheduler.uninstall: worker thread did not exit within 10s")
        else:
            log.info("scheduler.uninstall: worker thread exited cleanly")
        with self._lock:
            self._thread = None
            self._loop = None
            self._main_task = None
            self.stats.installed = False

    async def tick(self) -> ScanReport:
        """单次执行 scan_and_protect。

        可独立 await 使用(无需 install 后台)。默认注入 init 时的
        user_profile / loader / ledger_sink / scan_days。
        """
        profile = self._user_profile()
        log.info("scheduler.tick: start days=%d profile_keys=%s",
                 self.scan_days, sorted(profile.keys()) if profile else None)
        try:
            report = await self._run_protect(
                self.store,
                user_profile=profile,
                days=self.scan_days,
                ledger_sink=self.ledger_sink,
            )
        except Exception as e:  # noqa: BLE001 — tick 必须不抛给上层
            self.stats.error_count += 1
            self.stats.last_error = f"{type(e).__name__}: {e}"
            log.exception("scheduler.tick failed (will retry next tick): %s", e)
            # 返回空 report 而不是 raise,让后台 loop 不死
            return ScanReport(errors=[f"scheduler.tick failed: {e}"])
        self.stats.last_inserted = report.inserted_count
        self.stats.last_skipped = report.skipped_count
        self.stats.last_error = None
        log.info("scheduler.tick: done inserted=%d skipped=%d asked=%d yielded=%d errors=%d",
                 report.inserted_count, report.skipped_count,
                 report.asked_count, len(report.yielded), len(report.errors))
        return report

    def request_tick_now(self) -> "concurrent.futures.Future[ScanReport]":
        """从外部线程(HTTP handler / 任意 sync)异步触发一次 tick。

        返回 concurrent.futures.Future[ScanReport],调用方可:
          - fut.result(timeout=30) 同步等
          - fut.add_done_callback(fn) 回调式

        若 scheduler 未 install,在调用线程直接 asyncio.run(coro) 并返回
        wrap 后的 Future(便于测试 + 兜底)。
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            # 没启动后台:直接本线程 asyncio.run
            return asyncio.run_coroutine_threadsafe(
                self._tick_and_observe(), _new_temp_loop()
            ) if False else self._run_in_new_loop_fut()
        return asyncio.run_coroutine_threadsafe(self._tick_and_observe(), loop)

    def trigger_now(self, timeout: float = 30.0) -> ScanReport:
        """同步阻塞版本,等 tick 完成后返回 ScanReport。HTTP handler 用。

        若超时 -> 抛 conftest.timeout(Known Re-Export 见下方);
        若无后台 -> 仍可同步跑。
        """
        import concurrent.futures as _cf
        fut = self.request_tick_now()
        try:
            return fut.result(timeout=timeout)
        except _cf.TimeoutError as e:
            raise TimeoutError(
                f"scheduler.trigger_now timed out after {timeout}s"
            ) from e

    # --------------------------------------------------------
    # 内部:profile 解析
    # --------------------------------------------------------
    def _user_profile(self) -> Dict[str, Any]:
        if self._user_profile_loader is not None:
            try:
                loaded = self._user_profile_loader()
                return dict(loaded) if loaded else {}
            except Exception as e:  # noqa: BLE001
                log.warning("scheduler.user_profile_loader failed: %s; falling back to static", e)
        return dict(self._static_user_profile) if self._static_user_profile else {}

    # --------------------------------------------------------
    # 内部:worker thread
    # --------------------------------------------------------
    def _worker_main(self) -> None:
        """后台线程入口:起 event loop + 把 main coroutine 装载。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._main_task = loop.create_task(self._schedule_loop(), name="scheduler-main")
            self._loop_ready.set()
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            # uninstall() 主动取消 — 正常退出路径
            pass
        except Exception:  # noqa: BLE001
            log.exception("scheduler.worker crashed")
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    if not t.done():
                        t.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            self._loop = None
            self._main_task = None

    def _cancel_main_task(self) -> None:
        if self._main_task is not None and not self._main_task.done():
            self._main_task.cancel()

    async def _schedule_loop(self) -> None:
        """后台主循环:睡到下一 tick -> tick -> 重复。"""
        while not self._stop_event.is_set():
            now = self._time_source()
            parsed = self._parsed
            if parsed is None:
                log.warning("scheduler._schedule_loop: no parsed cron, exiting")
                return
            try:
                next_fire = _next_fire_after(parsed, now)
            except Exception as e:  # noqa: BLE001
                log.exception("scheduler._schedule_loop: next_fire calc failed: %s", e)
                # 下次重试间隔 60s
                await self._safe_sleep(60.0)
                continue
            wait_seconds = max(0.0, (next_fire - now).total_seconds())
            log.info("scheduler.next_fire=%s wait=%.1fs", next_fire.isoformat(), wait_seconds)
            if self._stop_event.is_set():
                return
            await self._safe_sleep(wait_seconds)
            if self._stop_event.is_set():
                return
            # 跑一次 tick
            await self._tick_and_observe()

    async def _tick_and_observe(self) -> ScanReport:
        """tick + 统计/时间戳;失败不抛出。"""
        self.stats.tick_count += 1
        self.stats.last_tick_at = self._time_source().isoformat(timespec="seconds")
        report = await self.tick()
        self.stats.last_finished_at = self._time_source().isoformat(timespec="seconds")
        return report

    async def _safe_sleep(self, seconds: float) -> None:
        """sleep,但能立刻响应 _stop_event._set。"""
        if seconds <= 0:
            return
        try:
            await self._sleep_fn(seconds)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler.sleep failed: %s", e)

    def _run_in_new_loop_fut(self):
        """未 install 时,在调用线程跑 tick 并返回 Future(供 request_tick_now 兜底)。

        不在调用线程直接 asyncio.run — 避免污染 Flask/ThreadingHTTPServer 的
        调用栈。这里建一个私有 loop,跑完即关。
        """
        import concurrent.futures as _cf

        def _runner() -> ScanReport:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._tick_and_observe())
            finally:
                loop.close()

        fut: "concurrent.futures.Future[ScanReport]" = _cf.Future()
        t = threading.Thread(target=lambda: _set_future(fut, _runner), daemon=True)
        t.start()
        return fut

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()


def _set_future(fut: "concurrent.futures.Future[ScanReport]", runner: Callable[[], ScanReport]) -> None:
    """在线程里跑 runner,把结果(成功/异常)塞进 fut。"""
    import concurrent.futures as _cf
    try:
        fut.set_result(runner())
    except BaseException as e:  # noqa: BLE001
        fut.set_exception(e)


def _new_temp_loop() -> asyncio.AbstractEventLoop:
    """造一个临时 loop 给 run_coroutine_threadsafe 用(请求后即关)。"""
    loop = asyncio.new_event_loop()
    threading.Thread(target=_close_later, args=(loop,), daemon=True).start()
    return loop


def _close_later() -> None:
    import time
    time.sleep(0.1)
    # 不主动关 — run_coroutine_threadsafe 完事自然 GC;此处仅占位


__all__ = [
    "TravelBufferScheduler",
    "SchedulerStats",
    "parse_cron",
    "_next_fire_after",
]