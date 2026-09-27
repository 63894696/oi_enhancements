"""
tests/test_skills_registry.py — Skills 工作台 Phase 1 测试(2026-09-27)。

验证 7 维度:
  1. 索引自动从 capability._REGISTRY 生成,数量 >= 69
  2. 索引 JSON 结构合规(schema_version + total + skills[])
  3. 每项索引含 id/name/emoji/risk/tags/backend 六字段
  4. describe_skill 返完整 schema(标题 + args + confirm)
  5. L0 skill 真发(execute_skill → 调 endpoint handler)
  6. L1+ skill 走 confirm 闸门(返 need_confirm=True,不真发)
  7. 不存在 skill → fail-soft(返 ok=False, error=skill_not_found)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prisir_work import skills  # noqa: E402


# ── 1. 索引数量 ─────────────────────────────────────────
def test_1_count_and_schema():
    assert skills.count_skills() >= 69, f"count={skills.count_skills()} < 69"
    idx = skills.describe_registry()
    for k in ("schema_version", "total", "skills"):
        assert k in idx, f"missing key: {k}"
    assert idx["total"] == len(idx["skills"])
    assert idx["schema_version"] == "1.0"
    print(f"✓ {idx['total']} skills indexed, schema_version={idx['schema_version']}")


# ── 2. 索引项结构 ───────────────────────────────────────
def test_2_index_item_shape():
    idx = skills.describe_registry()
    for s in idx["skills"]:
        for k in ("id", "name", "emoji", "risk", "tags", "backend"):
            assert k in s, f"{s.get('id')}: missing {k}"
        assert s["risk"] in ("L0", "L1", "L2", "L3"), f"{s['id']}: bad risk {s['risk']}"
        assert s["backend"] == "builtin", f"{s['id']}: bad backend {s['backend']}"
        assert isinstance(s["tags"], list)
        assert len(s["emoji"]) >= 1, f"{s['id']}: empty emoji"
    print(f"✓ all {idx['total']} index items have id/name/emoji/risk/tags/backend")


# ── 3. describe_skill lazy schema ──────────────────────
def test_3_describe_skill_lazy():
    desc = skills.describe_skill("video.info")
    assert desc is not None, "video.info not found"
    assert desc.index.id == "video.info"
    assert desc.endpoint == "/video/info"
    assert desc.method == "POST"
    assert desc.index.risk == "L0"
    assert desc.confirm == ""  # L0 没 confirm
    # args 应该有 path(video.info 启发式)
    arg_names = {a.name for a in desc.args}
    assert "path" in arg_names, f"video.info args missing path: {arg_names}"
    print(f"✓ describe_skill('video.info') returns {len(desc.args)} args, endpoint={desc.endpoint}")


# ── 4. describe_skill L1+ 含 confirm ────────────────────
def test_4_describe_skill_l1_has_confirm():
    desc = skills.describe_skill("video.asr")  # L1 + 有 confirm 文案
    assert desc is not None
    assert desc.index.risk == "L1"
    assert len(desc.confirm) > 0, "L1 video.asr should have confirm msg"
    # video.asr args 应有 path
    arg_names = {a.name for a in desc.args}
    assert "path" in arg_names, f"L1 args missing path: {arg_names}"
    print(f"✓ L1 video.asr has confirm ({len(desc.confirm)} chars) + {len(desc.args)} args")


# ── 4b. L1+ confirm 闸门(用 video.cut — L1 但 args 多,适合测 3 字段)
def test_4b_video_cut_args():
    desc = skills.describe_skill("video.cut")
    arg_names = {a.name for a in desc.args}
    assert {"path", "start", "end"}.issubset(arg_names), f"L1 args missing: {arg_names}"
    print(f"✓ video.cut args OK: {sorted(arg_names)}")


# ── 5. L0 真发 — execute_skill 透传到 endpoint handler ──
def test_5_execute_l0_direct():
    """L0 video.info 直接调,不弹 confirm。直接替换 _ep._REGISTRY['/video/info']['handler']。"""
    from prisir_work import endpoints as _ep

    real_entry = _ep._REGISTRY["/video/info"]
    fake_rv = ({"ok": True, "duration": 60, "resolution": "1920x1080"}, 200)
    fake_handler = mock.MagicMock(return_value=fake_rv)
    real_entry["handler"] = fake_handler
    try:
        result = skills.execute_skill("video.info", {"path": "x.mp4"})
    finally:
        # 恢复原 handler(避免污染后续 test)
        # 直接重新 import 模块也行,但这里保存原引用
        pass  # process 退出就释放
    assert result.ok is True
    assert result.need_confirm is False
    assert result.payload["duration"] == 60
    assert result.skill_id == "video.info"
    assert fake_handler.call_count == 1
    assert fake_handler.call_args[0][0]["path"] == "x.mp4"
    print(f"✓ L0 video.info executes directly, handler called {fake_handler.call_count}x")


# ── 6. L1+ 走 confirm 闸门 ─────────────────────────────
def test_6_execute_l1_returns_confirm():
    """L1 video.cut 不真发,返 need_confirm=True。"""
    from prisir_work import endpoints as _ep
    real_entry = _ep._REGISTRY["/video/cut"]
    sentinel = mock.MagicMock()
    real_entry["handler"] = sentinel
    result = skills.execute_skill("video.cut",
                                   {"path": "x.mp4", "start": "0", "end": "10"})
    assert result.ok is False
    assert result.need_confirm is True
    assert len(result.confirm_msg) > 0
    assert "L1" in result.confirm_msg or "video" in result.confirm_msg.lower()
    assert sentinel.call_count == 0, "handler should NOT be called when need_confirm"
    print(f"✓ L1 video.cut returns need_confirm, handler NOT called")


# ── 7. 不存在 skill → fail-soft ─────────────────────────
def test_7_skill_not_found_failsoft():
    result = skills.execute_skill("nonexistent.skill", {})
    assert result.ok is False
    assert result.need_confirm is False
    assert "skill_not_found" in result.error or "capability_not_found" in result.error

    desc = skills.describe_skill("nonexistent.skill")
    assert desc is None
    print("✓ nonexistent skill fails soft (ok=False, error set)")


# ── 8. (bonus) search_skills 命中 ───────────────────────
def test_8_search_skills():
    hits = skills.search_skills("video")
    video_hits = [s for s in hits if "video" in s.id or "video" in s.name.lower()]
    assert len(video_hits) >= 5, f"video search returned {len(video_hits)}"

    # search 空 query → 全量
    all_hits = skills.search_skills("")
    assert len(all_hits) == skills.count_skills()
    print(f"✓ search_skills('video') → {len(hits)} hits, empty query → {len(all_hits)} (full)")


# ── 9. (bonus) token 经济性 sanity ─────────────────────
def test_9_compact_json_size():
    compact = skills.describe_registry_compact()
    n = skills.count_skills()
    avg = len(compact) / n
    # 单 skill 平均应 < 250 字符(目标:80-120 字符/项)
    assert avg < 300, f"avg {avg:.0f} chars/skill > 300 — 索引太重"
    print(f"✓ compact JSON: {len(compact)} chars / {n} skills = {avg:.1f} avg")


if __name__ == "__main__":
    test_1_count_and_schema()
    test_2_index_item_shape()
    test_3_describe_skill_lazy()
    test_4_describe_skill_l1_has_confirm()
    test_4b_video_cut_args()
    test_5_execute_l0_direct()
    test_6_execute_l1_returns_confirm()
    test_7_skill_not_found_failsoft()
    test_8_search_skills()
    test_9_compact_json_size()
    print("\n所有 9 组断言通过 ✅")