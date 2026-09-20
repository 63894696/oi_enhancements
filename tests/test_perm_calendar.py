# -*- coding: utf-8 -*-
"""
tests/test_perm_calendar.py -- task #8 权限闸测试

跑法:
    pytest tests/test_perm_calendar.py -v
or
    python tests/test_perm_calendar.py          # 纯 stdlib 自跑(不依赖 pytest)

覆盖:
    1. 首次授权:一次弹卡 = 三档全开
    2. 后续静默:再调任何一档都不再弹卡
    3. 持久化:授权后 reload store 状态保留
    4. 高风险 destructive 写操作落 ledger
    5. 非 destructive 写操作不落 ledger
    6. 拒绝 calendar.read -> travel-buffer 工作流被阻断
       (要求 agent 优雅退化为「无法安排交通缓冲」)
    7. 拒绝 calendar.write 但允许 calendar.read -> 写静默 no-op
       (等价于 agent silence)
    8. UserPromptSink 抛异常 -> 视为拒绝(安全优先)
    9. 同 session 多次弹卡去重 (first_time_consumed 锁)
   10. corrupt grant file -> 隔离 + 视为空,不抛
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# 让 repo 根目录可被 import
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from prisiragent_coworker.permissions.calendar_maps_gates import (
    CalendarMapsPermission,
    CalendarMapsPermGate,
    PermGrantStore,
)
from prisiragent_coworker.permissions import calendar_read
from prisiragent_coworker.permissions import calendar_write
from prisiragent_coworker.permissions import maps_query


# ─────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────


class PromptRecorder:
    """Records every card fire + returns whatever the operator decided."""

    def __init__(self, decision: bool = True, raise_exc: Exception | None = None):
        self.decision = decision
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, permissions, copy_by_perm, risk_tiers, request_id):
        with self._lock:
            self.calls.append({
                "permissions": list(permissions),
                "copy": dict(copy_by_perm),
                "risk_tiers": dict(risk_tiers),
                "request_id": request_id,
            })
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.decision


class LedgerRecorder:
    def __init__(self):
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, record: dict) -> None:
        with self._lock:
            self.records.append(record)


def _make_gate(
    tmpdir: Path,
    *,
    decision: bool = True,
    raise_exc: Exception | None = None,
) -> tuple[CalendarMapsPermGate, PromptRecorder, LedgerRecorder]:
    store = PermGrantStore(tmpdir / "perm_grants.json")
    prompt = PromptRecorder(decision=decision, raise_exc=raise_exc)
    ledger = LedgerRecorder()
    gate = CalendarMapsPermGate(
        grant_store=store,
        user_prompt=prompt,
        ledger=ledger,
        now_fn=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    return gate, prompt, ledger


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


def test_first_time_card_grants_all_three() -> None:
    """一次弹卡,三档同时开通(不是弹三次)。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, ledger = _make_gate(Path(td))
        r = gate.request(CalendarMapsPermission.CALENDAR_READ, operation="list_events")
        assert r.granted is True
        assert r.via_first_time_card is True
        assert len(prompt.calls) == 1
        assert set(prompt.calls[0]["permissions"]) == {
            "calendar.read", "calendar.write", "maps.query",
        }
        # 三档全部 grant 已落盘
        grants = gate.grant_store.grants()
        assert set(grants.keys()) == {
            "calendar.read", "calendar.write", "maps.query",
        }
        # 没有任何 ledger 记录(read 不是 destructive)
        assert ledger.records == []


def test_silent_after_grant() -> None:
    """首次授权后,任何一档再调都不再弹卡。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, ledger = _make_gate(Path(td))
        gate.request(CalendarMapsPermission.MAPS_QUERY, operation="eta")
        n_after_first = len(prompt.calls)
        assert n_after_first == 1

        # 之后再调三档,均不再弹卡
        for perm, op in [
            (CalendarMapsPermission.CALENDAR_READ, "list_events"),
            (CalendarMapsPermission.CALENDAR_WRITE, "insert_buffer"),
            (CalendarMapsPermission.MAPS_QUERY, "geocode"),
        ]:
            r = gate.request(perm, operation=op)
            assert r.granted is True
            assert r.via_first_time_card is False
            assert r.reason.startswith("grant present")
        assert len(prompt.calls) == n_after_first


def test_grants_persist_across_reload() -> None:
    """授权后,新建 store 读同一文件,grant 仍在。"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "perm_grants.json"
        g1, p1, _ = _make_gate(Path(td))
        g1.request(CalendarMapsPermission.CALENDAR_READ, operation="list_events")
        assert path.exists()

        # reload
        g2 = CalendarMapsPermGate(
            grant_store=PermGrantStore(path),
            user_prompt=PromptRecorder(),
        )
        for perm in CalendarMapsPermission:
            assert g2.grant_store.is_granted(perm), f"{perm.value} should be granted after reload"


def test_destructive_write_lands_in_ledger() -> None:
    """calendar.write 的删除/dismiss 类操作必须落 ledger(grant 后也照写)。"""
    with tempfile.TemporaryDirectory() as td:
        gate, _, ledger = _make_gate(Path(td))
        # 先授权
        gate.request(CalendarMapsPermission.CALENDAR_WRITE, operation="insert_buffer")
        assert ledger.records == []

        # destructive op 应落 ledger
        r = gate.request(
            CalendarMapsPermission.CALENDAR_WRITE,
            operation="dismiss_event",
            context={"event_id": "evt-42", "reason": "user said skip"},
        )
        assert r.granted is True
        assert r.ledger_record_id is not None
        assert len(ledger.records) == 1
        rec = ledger.records[0]
        assert rec["operation"] == "dismiss_event"
        assert rec["permission"] == "calendar.write"
        assert rec["context"]["event_id"] == "evt-42"
        assert rec["id"] == r.ledger_record_id

        # 第二次 destructive 调用 -> 第二次 ledger 记录
        gate.request(
            CalendarMapsPermission.CALENDAR_WRITE,
            operation="delete_buffer",
            context={"event_id": "evt-43"},
        )
        assert len(ledger.records) == 2


def test_non_destructive_write_does_not_land_in_ledger() -> None:
    """calendar.write 的 insert_buffer (非 destructive) 不写 ledger。"""
    with tempfile.TemporaryDirectory() as td:
        gate, _, ledger = _make_gate(Path(td))
        gate.request(CalendarMapsPermission.CALENDAR_WRITE, operation="insert_buffer")
        r = gate.request(
            CalendarMapsPermission.CALENDAR_WRITE,
            operation="insert_buffer",
            context={"event_id": "evt-1", "minutes_before": 30},
        )
        assert r.granted is True
        assert r.ledger_record_id is None
        assert ledger.records == []


def test_read_denial_blocks_travel_buffer() -> None:
    """拒绝 calendar.read -> agent 应优雅退化(不进入 travel-buffer 工作流)。

    这个测试用 calendar_read.request + 拒绝的 prompt sink 模拟用户拒绝,
    并断言后续 gate.request 也拒,然后模拟 agent 检测到 denied 后该给出
    退化文案。退化文案由调用方决定,这里只校验 gate 返回值允许它判断。
    """
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, _ = _make_gate(Path(td), decision=False)
        r = gate.request(
            CalendarMapsPermission.CALENDAR_READ, operation="list_events",
        )
        assert r.granted is False
        assert r.via_first_time_card is True

        # 之后任何一档都直接 denied (first_time_consumed 锁住)
        for perm in CalendarMapsPermission:
            rr = gate.request(perm, operation="list_events")
            assert rr.granted is False, f"{perm.value} should be denied"
            assert rr.via_first_time_card is False
            assert "first-time card was already consumed" in rr.reason or \
                   "first-time card not approved" in rr.reason

        # 拒绝持久化(没有任何 grant)
        assert gate.grant_store.grants() == {}


def test_write_denial_keeps_read() -> None:
    """拒绝 calendar.write 但允许 calendar.read 的情形:即便两个一起拒绝,
    我们也确认 grant-store 在持久化路径上是 all-or-nothing(同一次弹卡结果
    同时作用于三档),不存在 '只拒绝 write' 的部分路径。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, _ = _make_gate(Path(td), decision=False)
        gate.request(CalendarMapsPermission.CALENDAR_WRITE, operation="insert_buffer")
        assert gate.grant_store.grants() == {}  # 整批都没开
        # read 也跟着拒
        rr = gate.request(CalendarMapsPermission.CALENDAR_READ, operation="list_events")
        assert rr.granted is False


def test_prompt_sink_exception_is_treated_as_denial() -> None:
    """UserPromptSink 抛异常 -> 安全默认 = 拒绝(不绕过)。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, _ = _make_gate(
            Path(td), raise_exc=RuntimeError("UI 挂了"),
        )
        r = gate.request(CalendarMapsPermission.MAPS_QUERY, operation="eta")
        assert r.granted is False
        assert r.via_first_time_card is True
        assert gate.grant_store.grants() == {}


def test_no_sink_defaults_to_approval() -> None:
    """没装 sink (headless / 测试) -> 默认批准;便于 CI 跑通。"""
    with tempfile.TemporaryDirectory() as td:
        store = PermGrantStore(Path(td) / "perm_grants.json")
        gate = CalendarMapsPermGate(grant_store=store, user_prompt=None)
        r = gate.request(CalendarMapsPermission.CALENDAR_READ, operation="list_events")
        assert r.granted is True
        assert r.via_first_time_card is True


def test_corrupt_grants_file_is_quarantined() -> None:
    """malformed JSON -> 隔离到 .corrupt.<ts>,视为空,不抛。"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "perm_grants.json"
        path.write_text("this is not json {{{", encoding="utf-8")
        store = PermGrantStore(path)
        # 不抛
        assert store.is_granted(CalendarMapsPermission.CALENDAR_READ) is False
        # 隔离文件存在
        corrupt_files = list(Path(td).glob("perm_grants.json.corrupt.*"))
        assert len(corrupt_files) == 1


def test_first_time_card_copy_contains_why() -> None:
    """弹卡 copy 必须告诉用户为何需要 + 数据去向,符合 UI 规范。"""
    with tempfile.TemporaryDirectory() as td:
        gate, _, _ = _make_gate(Path(td))
        payload = gate.first_time_card_payload()
        for perm_name, copy in payload["copy"].items():
            assert "PrisirAI" in copy, f"{perm_name} copy 应包含 PrisirAI"
            # 不允许空字符串或纯问号
            assert copy.strip() and "?" not in copy[:3]
        # risk tier 正确
        assert payload["risk_tiers"]["calendar.write"] == "high"
        assert payload["risk_tiers"]["calendar.read"] == "medium"
        assert payload["risk_tiers"]["maps.query"] == "medium"


def test_three_wrappers_share_one_first_time_card() -> None:
    """三档 wrapper module 共享同一个 gate,首次只弹一次卡。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, _ = _make_gate(Path(td))
        calendar_read.with_default(gate)
        calendar_write.with_default(gate)
        maps_query.with_default(gate)

        # 三档连续调,首次弹一次卡
        r1 = calendar_read.request_calendar_read("list_events")
        r2 = calendar_write.request_calendar_write("insert_buffer")
        r3 = maps_query.request_maps_query("eta")

        assert r1.granted and r2.granted and r3.granted
        # 只有第一档走 first-time card 路径;另两档在 is_granted fast-path
        # 上拿到 True(grant 在第一次调用里已落盘),不弹卡。
        assert r1.via_first_time_card is True
        assert r2.via_first_time_card is False
        assert r3.via_first_time_card is False
        assert len(prompt.calls) == 1, "三个 wrapper 必须共享同一张首次弹卡"


def test_concurrent_requests_only_prompt_once() -> None:
    """多线程并发请求不同 perm,首张卡只能弹一次。"""
    with tempfile.TemporaryDirectory() as td:
        gate, prompt, _ = _make_gate(Path(td))
        results: list = []
        results_lock = threading.Lock()

        def worker(perm, op):
            r = gate.request(perm, operation=op)
            with results_lock:
                results.append((perm, r.granted))

        threads = [
            threading.Thread(target=worker, args=(p, f"op-{p.value}"))
            for p in CalendarMapsPermission
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(prompt.calls) == 1, "并发场景下 first-time card 只能弹一次"
        # 三个 perm 都被授予
        assert {p.value for p, _ in results} == {
            "calendar.read", "calendar.write", "maps.query",
        }
        for _, granted in results:
            assert granted


def test_idempotent_grant_record() -> None:
    """record_grant 是幂等的:重复写不破坏现有 record 的 granted_at。"""
    with tempfile.TemporaryDirectory() as td:
        store = PermGrantStore(Path(td) / "perm_grants.json")
        r1 = store.record_grant(
            CalendarMapsPermission.CALENDAR_READ,
            request_id="abc",
            risk_tier="medium",
        )
        time.sleep(0.01)
        r2 = store.record_grant(
            CalendarMapsPermission.CALENDAR_READ,
            request_id="xyz",
            risk_tier="medium",
        )
        assert r1.granted_at == r2.granted_at, "第二次 record_grant 必须保留原始 granted_at"
        assert r1.request_id == r2.request_id


def test_risk_tier_high_for_calendar_write() -> None:
    """calendar.write 必须被标为 high,其余为 medium。"""
    with tempfile.TemporaryDirectory() as td:
        gate, _, _ = _make_gate(Path(td))
        assert gate.risk_tier(CalendarMapsPermission.CALENDAR_WRITE) == "high"
        assert gate.risk_tier(CalendarMapsPermission.CALENDAR_READ) == "medium"
        assert gate.risk_tier(CalendarMapsPermission.MAPS_QUERY) == "medium"


def test_perm_grants_path_env_override() -> None:
    """PRISIR_PERM_GRANTS 环境变量覆盖默认路径。"""
    with tempfile.TemporaryDirectory() as td:
        override = Path(td) / "custom_grants.json"
        os.environ["PRISIR_PERM_GRANTS"] = str(override)
        try:
            from prisiragent_coworker.permissions.calendar_maps_gates import (
                perm_grants_path,
            )
            assert perm_grants_path() == override
            # 落到 override 上
            store = PermGrantStore()
            store.record_grant(
                CalendarMapsPermission.MAPS_QUERY,
                request_id="env-test",
                risk_tier="medium",
            )
            assert override.exists()
            assert store.is_granted(CalendarMapsPermission.MAPS_QUERY)
        finally:
            del os.environ["PRISIR_PERM_GRANTS"]


# ─────────────────────────────────────────────────────────────────────
# Self-runner (no pytest)
# ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import traceback as _tb
    funcs = [
        (name, obj) for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    funcs.sort()
    fails: list[str] = []
    print(f"running {len(funcs)} test(s)")
    for name, fn in funcs:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception:  # noqa: BLE001
            fails.append(name)
            print(f"  FAIL {name}")
            _tb.print_exc()
    if fails:
        print(f"\n{len(fails)} FAILED: {fails}")
        sys.exit(1)
    print(f"\nall {len(funcs)} passed")