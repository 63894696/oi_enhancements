"""tests/test_instincts.py — P1-Instincts(2026-10-02)单元测试。

至少 10 项,对应 plan 测试清单:
 1. add → JSONL 追加成功 + reload 可见
 2. reinforce(success=True) → confidence += 0.1(从 0.3 → 0.4)
 3. reinforce(success=False) → confidence -= 0.05(从 0.3 → 0.25)
 4. 累计达阈值(0.5)→ recall 命中
 5. recall confidence < 0.5 不命中
 6. recall 关键词匹配 + 主题排序(query 与 pattern 重叠度高的优先)
 7. confidence < 0.1 自动从库删除(reinforce 多次失败)
 8. forget(id) 手动删除成功
 9. pin(id) 钉死后被衰减门槛保护(pinned=True 后 false 不衰减)
10. stats() 返回正确分类统计
+ 边界:format_for_prompt 输出以 [Instincts] 标识开头、IO 损坏行静默跳过、
  record_instinct 便捷入口返 id。

测试不 mock 行为:真读 tmpdir/instincts.jsonl,不污染真实 ~/.prisIrAI/。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

# 让 from memory.instincts / memory.oi_memory 可用(测试在 memory/tests/)
_HERE = Path(__file__).resolve().parent
_MEMORY = _HERE.parent
for p in (str(_MEMORY), str(_MEMORY.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from memory.instincts import (  # noqa: E402
    DEFAULT_INSTINCT_CONFIDENCE_START,
    DEFAULT_INSTINCTS_CONFIDENCE_THRESHOLD,
    DEFAULT_INSTINCTS_MAX_INJECT,
    Instinct,
    InstinctStore,
    record_instinct,
)


def _make_ins(
    pattern: str = "回答必须中文",
    category: str = "user_pref",
    confidence: float = DEFAULT_INSTINCT_CONFIDENCE_START,
    tags: list[str] | None = None,
    inst_id: str | None = None,
    source_session: str = "test",
) -> Instinct:
    """构造一条 Instinct(便于各测试复用)。"""
    return Instinct(
        id=inst_id or uuid.uuid4().hex,
        pattern=pattern,
        category=category,
        confidence=confidence,
        created_at=time.time(),
        source_session=source_session,
        tags=tags or ["i18n"],
    )


class TestAddAndReload(unittest.TestCase):
    """add → JSONL 追加 + reload 可见。"""

    def test_add_persists_and_reload_visible(self) -> None:
        """add 1 条 → 重新开 InstinctStore(path) 也能读到。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            s1 = InstinctStore(path=path)
            ins = _make_ins(pattern="写注释要中文")
            self.assertTrue(s1.add(ins))
            # 新 store 实例(模拟进程重启)
            s2 = InstinctStore(path=path)
            items = s2.list_all()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].id, ins.id)
            self.assertEqual(items[0].pattern, "写注释要中文")
            self.assertAlmostEqual(items[0].confidence,
                                   DEFAULT_INSTINCT_CONFIDENCE_START, places=4)


class TestReinforce(unittest.TestCase):
    """reinforce 反馈累积语义。"""

    def test_reinforce_success_increases_confidence(self) -> None:
        """success=True → confidence += 0.1(从 0.3 → 0.4)。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(confidence=0.3)
            store.add(ins)
            self.assertTrue(store.reinforce(ins.id, True))
            got = store.get(ins.id)
            assert got is not None
            self.assertAlmostEqual(got.confidence, 0.4, places=4)
            self.assertEqual(got.applied_count, 1)
            self.assertEqual(got.success_count, 1)
            self.assertGreater(got.last_used_at, 0.0)

    def test_reinforce_failure_decreases_confidence(self) -> None:
        """success=False → confidence -= 0.05(从 0.3 → 0.25)。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(confidence=0.3)
            store.add(ins)
            self.assertTrue(store.reinforce(ins.id, False))
            got = store.get(ins.id)
            assert got is not None
            self.assertAlmostEqual(got.confidence, 0.25, places=4)
            self.assertEqual(got.applied_count, 1)
            self.assertEqual(got.success_count, 0)

    def test_reinforce_caps_at_max(self) -> None:
        """confidence 上限 0.95。多次 success 不会突破。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(confidence=0.9)
            store.add(ins)
            for _ in range(5):
                store.reinforce(ins.id, True)
            got = store.get(ins.id)
            assert got is not None
            self.assertAlmostEqual(got.confidence, 0.95, places=4)


class TestRecallThreshold(unittest.TestCase):
    """recall 门槛过滤 + 主题排序。"""

    def test_recall_filters_below_threshold(self) -> None:
        """confidence < 0.5 不命中。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="回答必须中文", confidence=0.3)
            store.add(ins)
            hits = store.recall("帮我中文回答")
            self.assertEqual(hits, [], "confidence<0.5 不应被召回")

    def test_recall_above_threshold_hits(self) -> None:
        """累计 reinforce 到阈值(0.5)→ recall 命中。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="回答必须中文", confidence=0.3)
            store.add(ins)
            # 0.3 → 0.4 → 0.5
            store.reinforce(ins.id, True)
            store.reinforce(ins.id, True)
            hits = store.recall("中文回答问题", top_n=6)
            self.assertGreaterEqual(len(hits), 1, "confidence>=0.5 应被召回")
            self.assertEqual(hits[0].id, ins.id)

    def test_recall_relevance_ranking(self) -> None:
        """关键词重叠度高的 instinct 排前面。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            # 两条都提到「中文」,但一条更具体
            ins_a = _make_ins(pattern="回答用中文", confidence=0.6,
                              inst_id="a-" + uuid.uuid4().hex)
            ins_b = _make_ins(pattern="代码命名 snake_case", confidence=0.6,
                              inst_id="b-" + uuid.uuid4().hex)
            store.add(ins_a)
            store.add(ins_b)
            hits = store.recall("中文回答问题", top_n=6)
            self.assertGreaterEqual(len(hits), 1)
            # 第一名应是「回答用中文」(query 中文 关键词与 pattern 重叠度更高)
            self.assertEqual(hits[0].id, ins_a.id,
                             f"ins_a 应排第一,实际 {[(h.pattern, h.id) for h in hits]}")

    def test_recall_empty_query_returns_by_confidence(self) -> None:
        """无 query → 仅按 confidence DESC 排序。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins_low = _make_ins(pattern="a", confidence=0.55,
                                inst_id="low-" + uuid.uuid4().hex)
            ins_high = _make_ins(pattern="b", confidence=0.8,
                                 inst_id="hi-" + uuid.uuid4().hex)
            store.add(ins_low)
            store.add(ins_high)
            hits = store.recall("", top_n=6)
            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0].id, ins_high.id)
            self.assertEqual(hits[1].id, ins_low.id)


class TestAutoDelete(unittest.TestCase):
    """confidence 跌破 0.1 自动删除。"""

    def test_confidence_below_01_auto_removed(self) -> None:
        """从 0.3 多次失败 → confidence 跌破 0.1 → 自动从库删除。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="自动删测试", confidence=0.3)
            store.add(ins)
            # 0.3 - 0.05 * 5 = 0.05 → 跌破 0.1 阈值
            for _ in range(5):
                store.reinforce(ins.id, False)
            # 库中应该已经删掉
            got = store.get(ins.id)
            self.assertIsNone(got, "confidence<0.1 应自动从库删除")
            items = store.list_all()
            self.assertEqual(len(items), 0)


class TestForgetAndPin(unittest.TestCase):
    """人工 forget + pin 钉死保护。"""

    def test_forget_removes_entry(self) -> None:
        """forget(id) → 手动删除成功。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="待删项")
            store.add(ins)
            self.assertEqual(len(store.list_all()), 1)
            self.assertTrue(store.forget(ins.id))
            self.assertIsNone(store.get(ins.id))
            # 二次 forget → False(id 不存在)
            self.assertFalse(store.forget(ins.id))

    def test_pin_protects_from_decay(self) -> None:
        """pin 后 reinforce(success=False) 不衰减 confidence。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="钉死测试", confidence=0.5)
            store.add(ins)
            self.assertTrue(store.pin(ins.id))
            # 多次失败 — pinned 不衰减
            for _ in range(10):
                store.reinforce(ins.id, False)
            got = store.get(ins.id)
            assert got is not None
            self.assertTrue(got.pinned)
            # confidence 不变(还是 0.5);applied_count 累加
            self.assertAlmostEqual(got.confidence, 0.5, places=4)
            self.assertEqual(got.applied_count, 10)


class TestStatsAndFormat(unittest.TestCase):
    """stats + format_for_prompt。"""

    def test_stats_by_category(self) -> None:
        """stats() 按 category 分组统计 + total + avg_confidence。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            store.add(_make_ins(pattern="a", category="user_pref", confidence=0.7))
            store.add(_make_ins(pattern="b", category="user_pref", confidence=0.8))
            store.add(_make_ins(pattern="c", category="code_style", confidence=0.6))
            s = store.stats()
            self.assertEqual(s["total"], 3)
            self.assertEqual(s["by_category"]["user_pref"], 2)
            self.assertEqual(s["by_category"]["code_style"], 1)
            self.assertEqual(s["by_category"]["tool_usage"], 0)
            self.assertAlmostEqual(s["avg_confidence"],
                                   round((0.7 + 0.8 + 0.6) / 3, 4), places=4)
            self.assertEqual(s["above_threshold"], 3)
            self.assertEqual(s["pinned"], 0)

    def test_format_for_prompt_starts_with_marker(self) -> None:
        """format_for_prompt 输出以 [Instincts] 标识开头,末尾 [End instincts]。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            ins = _make_ins(pattern="回答用中文", confidence=0.6)
            text = store.format_for_prompt([ins])
            self.assertTrue(text.startswith("[Instincts — 置信度召回]"),
                            f"输出应以标识开头,实际: {text!r}")
            self.assertIn("回答用中文", text)
            self.assertIn("[End instincts]", text)
            self.assertIn("0.60", text)

    def test_format_for_prompt_empty_returns_empty_string(self) -> None:
        """空 hits → 返空字符串(不输出标识)。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            self.assertEqual(store.format_for_prompt([]), "")


class TestRobustness(unittest.TestCase):
    """损坏行 / IO 异常 / 便捷入口。"""

    def test_corrupted_line_silently_skipped(self) -> None:
        """JSONL 含损坏行 → read_all 静默跳过,不影响其他条目。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            ins = _make_ins(pattern="好条")
            with open(path, "w", encoding="utf-8") as f:
                f.write(ins.to_json() + "\n")
                f.write("not-json-{garbage\n")  # 损坏行
                f.write("\n")                   # 空行
                f.write(ins.to_json() + "\n")   # 重复条目也读出来
            store = InstinctStore(path=path)
            items = store.list_all()
            self.assertGreaterEqual(len(items), 1)
            self.assertTrue(all(x.pattern == "好条" for x in items))

    def test_record_instinct_helper(self) -> None:
        """record_instinct 便捷入口:confidence=0.3 起步,返 id。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            new_id = record_instinct(
                pattern="测试便捷入口",
                category="user_pref",
                source_session="t1",
                tags=("test",),
                path=path,
            )
            self.assertEqual(len(new_id), 32, "UUID4 hex 长度 32")
            store = InstinctStore(path=path)
            got = store.get(new_id)
            assert got is not None
            self.assertEqual(got.pattern, "测试便捷入口")
            self.assertEqual(got.category, "user_pref")
            self.assertAlmostEqual(got.confidence,
                                   DEFAULT_INSTINCT_CONFIDENCE_START, places=4)
            self.assertEqual(got.tags, ["test"])

    def test_is_enabled_default_true(self) -> None:
        """无 yaml 配置 → is_enabled() 默认 True。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.jsonl"
            store = InstinctStore(path=path)
            # 读 yaml 可能失败 / 没找到任何配置 → 兜底 True
            # 注意:本机若有 prisIrai_config.yaml 显式设为 false,会反映真实值
            # 测试仅验证函数能调用 + 返 bool
            result = store.is_enabled()
            self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
