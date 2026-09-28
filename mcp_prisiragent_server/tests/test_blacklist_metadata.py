#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_blacklist_metadata.py — M3.79 auto_blacklist 误伤防护 e2e 测试

覆盖:
  1. unit: _load_blacklist 兼容旧 flat list(自动迁移到 dict)
  2. unit: _load_blacklist TTL 过期清理
  3. unit: blacklist_add_impl 普通加(所有 metadata 字段)
  4. unit: blacklist_add_impl source=auto 设 expires_at = now+7d
  5. unit: blacklist_add_impl source=user 不设 expires_at(永久)
  6. unit: blacklist_add_impl 满 50 条拒绝
  7. unit: blacklist_add_impl 重复名 count++ + refreshed added_at
  8. unit: blacklist_review_impl 过滤 source=auto
  9. unit: blacklist_unfreeze_recent_impl 批量删

设计:
  - 用 monkeypatch 把 BLACKLIST_PATH 指向 tmp 目录(避免污染 ~/.claude/process_blacklist.json)
  - 用 mock 替换 _toast_blacklist_added(避免弹 toast 阻塞)
  - 跑速目标 ~3-5s
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))

import process_controller_tools as p  # noqa: E402


# ============================================================
# 工具
# ============================================================
def _isolated_blacklist(tmpdir: Path):
    """monkeypatch BLACKLIST_PATH 指向 tmpdir/process_blacklist.json + 关闭 toast。

    Returns context manager (mock.patch 多重)。
    """
    # 1. 换路径
    fake_path = tmpdir / "process_blacklist.json"
    # 2. 关 toast
    noop_toast = mock.MagicMock()
    return mock.patch.multiple(
        p,
        BLACKLIST_PATH=fake_path,
        _toast_blacklist_added=noop_toast,
    )


def _write_flat_list(tmpdir: Path, names: list[str]) -> None:
    """模拟旧版 M3.55 flat list 文件。"""
    path = tmpdir / "process_blacklist.json"
    path.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")


def _read_json(tmpdir: Path) -> list[dict]:
    """读 raw blacklist 文件。"""
    return json.loads((tmpdir / "process_blacklist.json").read_text(encoding="utf-8"))


# ============================================================
# 1. _load_blacklist 兼容旧 flat list
# ============================================================
def test_load_blacklist_migrates_flat_list():
    """旧版 ["tap", "vpn"] flat list → 自动迁移到 list[dict]。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            _write_flat_list(Path(tmp), ["tap", "vpn"])
            items = p._load_blacklist()
            assert isinstance(items, list)
            assert len(items) == 2, f"应迁移 2 条,实际 {len(items)}"
            for e in items:
                assert isinstance(e, dict), f"flat list 没迁到 dict: {e}"
                assert "name" in e
                assert "added_at" in e
                assert "added_by" in e
                assert "source" in e
                assert "reason" in e
                assert "expires_at" in e
                assert "count" in e
            names = {e["name"] for e in items}
            assert names == {"tap", "vpn"}
            # 持久化也应写入迁移结果
            persisted = _read_json(Path(tmp))
            assert len(persisted) == 2
            print(f"[1] OK  flat list → dict migration ({len(items)} 条,字段全)")


# ============================================================
# 2. _load_blacklist TTL 过期清理
# ============================================================
def test_load_blacklist_purges_expired():
    """expires_at < now 的 entry → 自动剔除 + 写回。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            now = time.time()
            items = [
                {"name": "fresh", "added_at": now, "added_by": "u",
                 "source": "auto", "reason": "r",
                 "expires_at": now + 1000, "count": 1},
                {"name": "stale1", "added_at": now - 10000, "added_by": "u",
                 "source": "auto", "reason": "r",
                 "expires_at": now - 100, "count": 1},  # 已过期
                {"name": "stale2", "added_at": now - 20000, "added_by": "u",
                 "source": "auto", "reason": "r",
                 "expires_at": now - 200, "count": 1},  # 已过期
            ]
            (Path(tmp) / "process_blacklist.json").write_text(
                json.dumps(items, ensure_ascii=False), encoding="utf-8"
            )
            loaded = p._load_blacklist()
            names = {e["name"] for e in loaded}
            assert names == {"fresh"}, f"应只剩 fresh,实际 {names}"
            # 持久化应剔除过期项
            persisted = _read_json(Path(tmp))
            assert len(persisted) == 1
            assert persisted[0]["name"] == "fresh"
            print(f"[2] OK  TTL purge: 2 过期 → 剔除,剩 1 条 + 写回")


# ============================================================
# 3. blacklist_add_impl 普通加(所有 metadata 字段)
# ============================================================
def test_blacklist_add_all_metadata_fields():
    """add 一个普通 entry → ok + 所有 metadata 字段填充正确。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            r = json.loads(p.blacklist_add_impl("procA", added_by="u-test",
                                                source="user", reason="manual"))
            assert r["ok"] is True
            assert r["added"] == "procA"
            assert r["blacklist_size"] == 1
            items = p._load_blacklist()
            assert len(items) == 1
            e = items[0]
            assert e["name"] == "procA"
            assert e["added_by"] == "u-test"
            assert e["source"] == "user"
            assert e["reason"] == "manual"
            assert e["count"] == 1
            assert e["expires_at"] is None  # user 永久
            print(f"[3] OK  add 普通 entry 字段全(name/source/reason/count/expires_at)")


# ============================================================
# 4. blacklist_add_impl source=auto 设 expires_at = now+7d
# ============================================================
def test_blacklist_add_auto_sets_ttl():
    """source=auto → expires_at = now + BLACKLIST_TTL_SEC_DEFAULT(7 天)。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            t0 = time.time()
            r = json.loads(p.blacklist_add_impl("procB", added_by="perf_guard.M3.55",
                                                source="auto", reason="TAP disconnected"))
            assert r["ok"] is True
            assert r["ttl_sec"] == p.BLACKLIST_TTL_SEC_DEFAULT
            items = p._load_blacklist()
            e = items[0]
            assert e["source"] == "auto"
            assert e["expires_at"] is not None
            # expires_at 应在 [now + 7d - 5s, now + 7d + 5s]
            expected = t0 + p.BLACKLIST_TTL_SEC_DEFAULT
            assert abs(e["expires_at"] - expected) < 5, \
                f"expires_at 偏离 {abs(e['expires_at'] - expected):.1f}s"
            print(f"[4] OK  source=auto expires_at = now+{p.BLACKLIST_TTL_SEC_DEFAULT//3600}h "
                  f"(7 天 TTL 生效)")


# ============================================================
# 5. blacklist_add_impl source=user 不设 expires_at(永久)
# ============================================================
def test_blacklist_add_user_permanent():
    """source=user → expires_at = None(永久有效)。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            r = json.loads(p.blacklist_add_impl("procC", added_by="u-test",
                                                source="user", reason="manual freeze"))
            assert r["ok"] is True
            assert r["ttl_sec"] is None
            items = p._load_blacklist()
            assert items[0]["expires_at"] is None
            print(f"[5] OK  source=user expires_at=None (永久)")


# ============================================================
# 6. blacklist_add_impl 满 50 条拒绝
# ============================================================
def test_blacklist_add_size_cap():
    """预填 50 条 → 第 51 条拒,ok=False + suggest review。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            now = time.time()
            items = [
                {"name": f"proc{i}", "added_at": now, "added_by": "u",
                 "source": "user", "reason": "fill",
                 "expires_at": None, "count": 1}
                for i in range(p.BLACKLIST_MAX_SIZE)
            ]
            (Path(tmp) / "process_blacklist.json").write_text(
                json.dumps(items, ensure_ascii=False), encoding="utf-8"
            )
            r = json.loads(p.blacklist_add_impl("procFull", source="user",
                                                reason="overflow test"))
            assert r["ok"] is False, "满 50 应拒绝"
            assert "blacklist-review" in r.get("suggest", "")
            assert r.get("error"), "应有 error 字段"
            # 持久化应不变(没追加第 51 条)
            persisted = _read_json(Path(tmp))
            assert len(persisted) == p.BLACKLIST_MAX_SIZE
            print(f"[6] OK  size cap=50 拒绝第 51 条 + suggest review")


# ============================================================
# 7. blacklist_add_impl 重复名 count++
# ============================================================
def test_blacklist_add_increments_count():
    """同名重复加 → count++,added_at/expires_at 刷新。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            # 第 1 次
            t1 = time.time()
            p.blacklist_add_impl("dup", source="auto", reason="r1",
                                 added_by="u")
            items = p._load_blacklist()
            assert items[0]["count"] == 1
            first_added = items[0]["added_at"]
            # 等 0.05s 让 added_at 区分
            time.sleep(0.05)
            t2 = time.time()
            # 第 2 次
            p.blacklist_add_impl("DUP", source="auto", reason="r2",
                                 added_by="u")
            items = p._load_blacklist()
            assert len(items) == 1, "应只剩 1 条同名 entry"
            assert items[0]["count"] == 2, f"count 应=2,实际 {items[0]['count']}"
            assert items[0]["reason"] == "r2", f"reason 应更新到 r2,实际 {items[0]['reason']}"
            assert items[0]["added_at"] > first_added, "added_at 应刷新"
            print(f"[7] OK  重复名 count 1→2, added_at/expires_at 刷新")


# ============================================================
# 8. blacklist_review_impl 过滤 source=auto
# ============================================================
def test_blacklist_review_filter_by_source():
    """blacklist_review_impl(source='auto') → 只返 auto 来源。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            p.blacklist_add_impl("auto1", source="auto", reason="r",
                                 added_by="u")
            p.blacklist_add_impl("auto2", source="auto", reason="r",
                                 added_by="u")
            p.blacklist_add_impl("user1", source="user", reason="r",
                                 added_by="u")
            # filter source=auto
            r = json.loads(p.blacklist_review_impl(source="auto"))
            assert r["ok"] is True
            assert r["count"] == 2
            names = {e["name"] for e in r["blacklist"]}
            assert names == {"auto1", "auto2"}
            for e in r["blacklist"]:
                assert e["source"] == "auto"
                assert "expires_in_sec" in e  # M3.79 metadata
            # filter source=user
            r2 = json.loads(p.blacklist_review_impl(source="user"))
            assert r2["count"] == 1
            assert r2["blacklist"][0]["name"] == "user1"
            # no filter
            r3 = json.loads(p.blacklist_review_impl())
            assert r3["count"] == 3
            print(f"[8] OK  review filter source=auto/user/all(2/1/3)")


# ============================================================
# 9. blacklist_unfreeze_recent_impl 批量删
# ============================================================
def test_blacklist_unfreeze_recent():
    """unfreeze_recent(source='auto', age=7d) → 删 auto + 年龄 ≤ 7d。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _isolated_blacklist(Path(tmp)):
            now = time.time()
            # 3 auto 都在 7 天内
            p.blacklist_add_impl("a1", source="auto", reason="r",
                                 added_by="u")
            p.blacklist_add_impl("a2", source="auto", reason="r",
                                 added_by="u")
            p.blacklist_add_impl("a3", source="auto", reason="r",
                                 added_by="u")
            # 1 user(不应被删)
            p.blacklist_add_impl("u1", source="user", reason="r",
                                 added_by="u")
            # 默认 age=7d
            r = json.loads(p.blacklist_unfreeze_recent_impl(age_sec=7 * 24 * 3600))
            assert r["ok"] is True
            assert r["removed_count"] == 3
            assert sorted(r["removed"]) == ["a1", "a2", "a3"]
            assert r["remaining"] == 1
            items = p._load_blacklist()
            assert len(items) == 1
            assert items[0]["name"] == "u1"
            # name_filter 子串匹配
            p.blacklist_add_impl("x1", source="auto", reason="r", added_by="u")
            p.blacklist_add_impl("y1", source="auto", reason="r", added_by="u")
            r2 = json.loads(p.blacklist_unfreeze_recent_impl(age_sec=7 * 24 * 3600,
                                                            name_filter="x"))
            assert r2["removed_count"] == 1
            assert r2["removed"] == ["x1"]
            assert r2["remaining"] == 2  # u1 + y1
            print(f"[9] OK  unfreeze-recent 默认删 auto 7d + name_filter 子串匹配")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== process_controller_tools M3.79 auto_blacklist 误伤防护 e2e 测试 ===\n")
    print(f"路径: {_SERVER}\n")

    tests = [
        test_load_blacklist_migrates_flat_list,
        test_load_blacklist_purges_expired,
        test_blacklist_add_all_metadata_fields,
        test_blacklist_add_auto_sets_ttl,
        test_blacklist_add_user_permanent,
        test_blacklist_add_size_cap,
        test_blacklist_add_increments_count,
        test_blacklist_review_filter_by_source,
        test_blacklist_unfreeze_recent,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t_func in tests:
        try:
            t_func()
            passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(tests)}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())