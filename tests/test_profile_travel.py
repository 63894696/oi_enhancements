"""tests/test_profile_travel.py — user_profile.py 出行画像 slot 单元测试(task #10)。

覆盖 user_profile.py:
  - extract_travel_facts 自动正则提取 office / home / mode / wfh
  - set_workplace / set_default_travel_mode 写入 + 幂等
  - append_dismissed_buffer 追加 ledger(task #8 权限闸对接点)
  - travel_block 系统提示注入块
  - 并发 10 线程写入不爆(防 409 / 防 JSON 半覆盖)
  - legacy flat-list 文件向后兼容(load_profile / archive_fact)

跑法: python tests/test_profile_travel.py  →  打印 PASS/FAIL
"""
from __future__ import annotations

import sys
import os
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import user_profile  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_fails: list[str] = []
_TMP_DIR = tempfile.mkdtemp(prefix="prisIr_profile_travel_test_")
os.environ["PRISIR_DATA_DIR"] = _TMP_DIR


def check(name: str, cond: bool) -> None:
    print(("✓ " if cond else "✗ ") + name)
    if not cond:
        _fails.append(name)


# --- 自动提取(关键词正则,无 LLM) ---

def test_extract_office():
    f = user_profile.extract_travel_facts("我公司在国贸 SK")
    check("extract office from '我公司在国贸 SK'",
          f.get("office") == "国贸 SK")


def test_extract_mode_bicycling():
    f = user_profile.extract_travel_facts("我骑自行车上班")
    check("extract mode bicycling from '我骑自行车上班'",
          f.get("mode") == "bicycling")


def test_extract_mode_car():
    f = user_profile.extract_travel_facts("平时我开车通勤")
    check("extract mode car", f.get("mode") == "car")


def test_extract_mode_transit():
    f = user_profile.extract_travel_facts("我坐地铁上班")
    check("extract mode transit", f.get("mode") == "transit")


def test_extract_home_address():
    f = user_profile.extract_travel_facts("我家在中关村南大街 5 号")
    check("extract home address", f.get("home") is not None and "中关村" in f["home"])


def test_extract_wfh_flag():
    f = user_profile.extract_travel_facts("今天WFH,别叫我")
    check("extract wfh flag from '今天WFH'", f.get("wfh") is True)


def test_extract_negative():
    f = user_profile.extract_travel_facts("今天天气不错")
    check("irrelevant text extracts nothing", f == {})


# --- 写入与幂等 ---

def test_set_workplace_idempotent():
    # 每个 test 用新 thread-id 隔离(共享 _TMP_DIR)
    user_profile.set_workplace("office", "北京市朝阳区建国路 88 号")
    ok1 = user_profile.set_workplace("office", "北京市朝阳区建国路 88 号")
    check("set_workplace idempotent (same value → False)", ok1 is False)
    t = user_profile.load_travel_profile()
    check("office address persisted", t["workplace_addresses"]["office"] ==
          "北京市朝阳区建国路 88 号")


def test_set_workplace_invalid_slot():
    ok = user_profile.set_workplace("garage", "somewhere")
    check("invalid slot returns False", ok is False)


def test_set_default_travel_mode():
    ok = user_profile.set_default_travel_mode("walking")
    check("set_default_travel_mode('walking')", ok is True)
    t = user_profile.load_travel_profile()
    check("default_travel_mode persisted", t["default_travel_mode"] == "walking")
    ok2 = user_profile.set_default_travel_mode("walking")
    check("set_default_travel_mode idempotent", ok2 is False)


def test_set_default_travel_mode_invalid():
    ok = user_profile.set_default_travel_mode("rocket")
    check("invalid mode rejected", ok is False)


# --- dismissed_buffer_ledger(task #8 权限闸对接点) ---

def test_append_dismissed_buffer():
    bid = user_profile.append_dismissed_buffer(
        event_id="evt-test-001",
        origin="北京市朝阳区建国路 88 号",
        destination="北京首都国际机场",
        mode="transit",
        reason="user_clicked_x",
    )
    check("append_dismissed_buffer returns UUID str", bid is not None and len(bid) > 8)
    t = user_profile.load_travel_profile()
    ledger = t["dismissed_buffer_ledger"]
    check("ledger has 1 entry", len(ledger) == 1)
    last = ledger[-1]
    check("buffer_id recorded", last["buffer_id"] == bid)
    check("event_id recorded", last["event_id"] == "evt-test-001")
    check("reason recorded", last["reason"] == "user_clicked_x")
    check("dismissed_at present", "dismissed_at" in last and last["dismissed_at"])


def test_append_dismissed_buffer_invalid_reason():
    bid = user_profile.append_dismissed_buffer("e", "o", "d", "m", "bogus_reason")
    check("invalid reason returns None", bid is None)


def test_append_multiple_ledger_entries():
    base_count = len(user_profile.load_travel_profile()["dismissed_buffer_ledger"])
    for i in range(5):
        user_profile.append_dismissed_buffer(
            event_id=f"evt-multi-{i}",
            origin="A", destination="B", mode="car", reason="user_edited",
        )
    ledger = user_profile.load_travel_profile()["dismissed_buffer_ledger"]
    check("multi-append grows ledger", len(ledger) == base_count + 5)


# --- travel_block(系统提示注入块) ---

def test_travel_block():
    blk = user_profile.travel_block()
    check("travel_block non-empty when travel slot filled", bool(blk))
    check("travel_block contains 出行画像", "出行画像" in blk)
    check("travel_block contains 默认通勤方式", "默认通勤方式" in blk or "通勤" in blk)


# --- 并发 409 防爆 ---

def test_concurrent_writes_no_409():
    """10 线程同时写 set_workplace(home, addr-i),不爆(锁保护 + 原子写)。"""
    # 用独立 key 防止与上面 home 冲突
    user_profile.set_default_travel_mode("car")  # 触发一次完整路径
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            # 反复写 mode + set_workplace 不同 slot 各 5 次
            for k in range(5):
                if k % 2 == 0:
                    user_profile.set_workplace("office", f"addr-{i}-{k}")
                else:
                    user_profile.append_dismissed_buffer(
                        event_id=f"evt-conc-{i}-{k}",
                        origin=f"O-{i}", destination=f"D-{k}",
                        mode="transit", reason="user_moved",
                    )
        except BaseException as e:  # noqa: BLE017
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("10 concurrent writers — no exception raised", not errors)
    # 文件仍是合法 JSON
    state = user_profile.load_full_state()
    check("post-concurrency state has items+travel keys",
          "items" in state and "travel" in state)
    ledger = state["travel"]["dismissed_buffer_ledger"]
    # worker 每轮 k∈{1,3} 各加 1 条 → 2 条/worker × 10 = 20
    check("post-concurrency ledger populated (>= 20)",
          len(ledger) >= 20)


# --- legacy 兼容 ---

def test_legacy_compat():
    """旧文件是 flat list,新 API 也能正常读写,且旧 items 字段保留可见。"""
    import json as _json
    legacy_path = Path(_TMP_DIR) / "user_profile.json"
    legacy_path.write_text(_json.dumps(
        [{"kind": "preference", "fact": "偏好分点作答", "count": 3, "ts": 1700000000.0}],
        ensure_ascii=False))
    items = user_profile.load_profile()
    check("legacy list file → load_profile returns 1 item", len(items) == 1)
    check("legacy item kind=preference", items[0].get("kind") == "preference")
    # 写入 travel slot 后,旧 items 仍可见
    user_profile.set_workplace("office", "上海徐汇")
    items2 = user_profile.load_profile()
    check("after travel write, legacy items still visible", len(items2) == 1)
    check("legacy items fact unchanged", items2[0].get("fact") == "偏好分点作答")
    t = user_profile.load_travel_profile()
    check("after travel write, travel slot populated",
          t["workplace_addresses"]["office"] == "上海徐汇")


# --- 端到端:用户说一句话,自动沉淀 + recall 可见 ---

def test_end_to_end_distill_and_recall():
    # 清空前序 test 状态,用一个新的小隔离目录
    isolated = tempfile.mkdtemp(prefix="prisIr_e2e_")
    os.environ["PRISIR_DATA_DIR"] = isolated
    # 强制 reload 模块使 _profile_path 用新目录
    import importlib
    importlib.reload(user_profile)

    # 用户说「我公司在国贸 SK,我骑自行车上班」
    user_profile.extract_travel_facts_sync("我公司在国贸 SK,我骑自行车上班")

    t = user_profile.load_travel_profile()
    check("E2E: office 自动沉淀", t["workplace_addresses"]["office"] == "国贸 SK")
    check("E2E: mode 自动沉淀", t["default_travel_mode"] == "bicycling")

    blk = user_profile.travel_block()
    check("E2E: travel_block 含 office + 自行车",
          "国贸 SK" in blk and ("bicycling" in blk or "自行车" in blk))

    # 还原隔离目录,避免污染后续
    os.environ["PRISIR_DATA_DIR"] = _TMP_DIR
    importlib.reload(user_profile)


def main() -> int:
    print("=== user_profile 出行画像 单元测试 (task #10) ===\n")
    test_extract_office(); print()
    test_extract_mode_bicycling(); print()
    test_extract_mode_car(); print()
    test_extract_mode_transit(); print()
    test_extract_home_address(); print()
    test_extract_wfh_flag(); print()
    test_extract_negative(); print()
    test_set_workplace_idempotent(); print()
    test_set_workplace_invalid_slot(); print()
    test_set_default_travel_mode(); print()
    test_set_default_travel_mode_invalid(); print()
    test_append_dismissed_buffer(); print()
    test_append_dismissed_buffer_invalid_reason(); print()
    test_append_multiple_ledger_entries(); print()
    test_travel_block(); print()
    test_concurrent_writes_no_409(); print()
    test_legacy_compat(); print()
    test_end_to_end_distill_and_recall(); print()
    print("=== 判定 ===")
    if _fails:
        print(f"FAIL: {len(_fails)} 项未过 -> {_fails}")
        return 1
    print("PASS: 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())