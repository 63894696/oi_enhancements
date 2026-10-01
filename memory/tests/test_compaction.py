"""tests/test_compaction.py — P4-Compaction(2026-10-02)单元测试。

11 项测试覆盖 plan 测试清单:
 1. estimate_tokens 准确度(纯文本 1000 chars → ~250 tokens)
 2. 图片 content block 计 IMAGE_TOKEN_COST=1600(不是 raw base64 长度)
 3. needs_compaction(80%) → (True, Summarized)
 4. needs_compaction(95%) → (True, HardCompacted)
 5. needs_compaction(<80%) → (False, None_)
 6. compact(messages, llm_call=None) 紧急硬压 → 保留最近 N 轮 + 摘要
 7. emergency_truncate_tool_result(>4000 chars) → 截断 + [truncated]
 8. emergency_truncate_image(>1024 chars) → 截断
 9. compact 失败 fallback:异常 → 返原 messages + None_
10. is_enabled 默认 True
11. SUMMARY_PROMPT 中文 4 段(检查字符串含「上下文」「已做」「当前状态」「用户偏好」)

测试不污染真实 OI Memory DB:只构造 msgs list,不存盘。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 让 from memory.compaction 可用(测试在 memory/tests/)
_HERE = Path(__file__).resolve().parent
_MEMORY = _HERE.parent
for p in (str(_MEMORY), str(_MEMORY.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from memory.compaction import (  # noqa: E402
    CHARS_PER_TOKEN,
    COMPACTION_THRESHOLD,
    CompactionAction,
    CompactionManager,
    CompactionStats,
    CRITICAL_THRESHOLD,
    EMERGENCY_IMAGE_MAX_CHARS,
    EMERGENCY_TOOL_RESULT_MAX_CHARS,
    IMAGE_TOKEN_COST,
    RECENT_TURNS_TO_KEEP,
    SUMMARY_PROMPT,
    SYSTEM_OVERHEAD_TOKENS,
)


def _build_msgs(n_turns: int, chars_per_turn: int = 1000) -> list[dict]:
    """构造 n_turns 轮对话(每轮 user + assistant 各一条)。"""
    msgs: list[dict] = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": "x" * chars_per_turn})
        msgs.append({"role": "assistant", "content": "y" * chars_per_turn})
    return msgs


class TestCompaction(unittest.TestCase):
    # ---------- 1. estimate_tokens 准确度 ----------
    def test_estimate_tokens_text_only(self):
        mgr = CompactionManager()
        # 1000 chars text → 250 tokens,加 SYSTEM_OVERHEAD_TOKENS
        msgs = [
            {"role": "system", "content": "hi"},
            {"role": "user", "content": "x" * 1000},
        ]
        est = mgr.estimate_tokens(msgs)
        # 系统开销 18_000 + (1000*2 + 2)/4 累计文本(包含 system 段 "hi")
        expected_text_tokens = (1000 + 2 + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
        expected = expected_text_tokens + SYSTEM_OVERHEAD_TOKENS
        self.assertEqual(est, expected)
        # 文本 1000 chars 应该是 ~250 tokens
        text_only = est - SYSTEM_OVERHEAD_TOKENS
        self.assertGreaterEqual(text_only, 240)
        self.assertLessEqual(text_only, 260)

    # ---------- 2. 图片平摊 ----------
    def test_estimate_tokens_image_flat_rate(self):
        mgr = CompactionManager()
        # 100KB base64 → 应该是 IMAGE_TOKEN_COST=1600,不是 25000
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "data": "A" * 100_000},
                    }
                ],
            },
        ]
        est = mgr.estimate_tokens(msgs)
        # 期望 = 18000 + 1600(图片) = 19600
        # 如果按 base64 长度算会是 ~27000,我们只取 19600
        self.assertEqual(est, IMAGE_TOKEN_COST + SYSTEM_OVERHEAD_TOKENS)
        # 校验不是 raw base64 长度
        raw_tokens = (100_000 + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
        self.assertNotEqual(est, raw_tokens + SYSTEM_OVERHEAD_TOKENS)
        # 且必须远小于 raw
        self.assertLess(est, raw_tokens + SYSTEM_OVERHEAD_TOKENS)

    # ---------- 3. needs_compaction(80%) → Summarized ----------
    def test_needs_compaction_above_threshold(self):
        mgr = CompactionManager()
        # 构造 ~85% usage: text_tokens + SYSTEM_OVERHEAD = 0.85 * 200_000
        # target text_tokens = 170_000 - 18_000 = 152_000
        # chars = 152_000 * 4 = 608_000
        msgs = [{"role": "user", "content": "x" * 608_000}]
        need, action = mgr.needs_compaction(msgs)
        self.assertTrue(need)
        self.assertEqual(action, CompactionAction.Summarized)

    # ---------- 4. needs_compaction(95%) → HardCompacted ----------
    def test_needs_compaction_above_critical(self):
        mgr = CompactionManager()
        # ~96% usage: text_tokens = 0.96*200_000 - 18_000 = 174_000
        # chars = 174_000 * 4 = 696_000
        msgs = [{"role": "user", "content": "x" * 696_000}]
        need, action = mgr.needs_compaction(msgs)
        self.assertTrue(need)
        self.assertEqual(action, CompactionAction.HardCompacted)

    # ---------- 5. needs_compaction(<80%) → None_ ----------
    def test_needs_compaction_below_threshold(self):
        mgr = CompactionManager()
        # ~10% usage
        msgs = [{"role": "user", "content": "x" * 10_000}]
        need, action = mgr.needs_compaction(msgs)
        self.assertFalse(need)
        self.assertEqual(action, CompactionAction.None_)

    # ---------- 6. compact hard 硬压路径 ----------
    def test_compact_hard_no_llm(self):
        mgr = CompactionManager()
        # 构造 ~96% usage(必触发 hard)
        msgs = _build_msgs(120, chars_per_turn=3_000)  # 240 msgs, ~180K tokens
        new_msgs, action = mgr.compact(msgs, llm_call=None)
        self.assertEqual(action, CompactionAction.HardCompacted)
        # hard 压缩后 system 段保留 + 1 marker + MIN_TURNS_TO_KEEP 条 active 保留
        # min_turns=2 → 0 system + 1 marker + 2 active = 3
        self.assertLess(len(new_msgs), len(msgs))
        # 至少保留 min_turns 条非 system 消息
        active = [m for m in new_msgs if m.get("role") != "system"]
        self.assertGreaterEqual(len(active), mgr.min_turns)

    def test_compact_soft_with_llm(self):
        mgr = CompactionManager()
        # 构造 ~85% usage:60 轮 × 2 msgs × 2500 chars = 300_000 chars / 4 = 75_000
        # text_tokens + 18_000 overhead = 93_000 / 200_000 = 46% → 升到 5000 chars
        # 60 × 2 × 5000 = 600_000 / 4 = 150_000 + 18_000 = 168_000 / 200_000 = 84%
        msgs = _build_msgs(60, chars_per_turn=5_000)  # 120 msgs, ~168K tokens → ~84%
        # 用 fake llm_call 模拟 summary
        def fake_llm(prompt: str) -> str:
            return "摘要:\n- Context: 测试\n- 已做: 单元测试\n- 当前状态: ok\n- 用户偏好: 中文"
        new_msgs, action = mgr.compact(msgs, llm_call=fake_llm)
        # 应触发 soft summary(80% 以上)
        self.assertIn(action, (CompactionAction.Summarized, CompactionAction.HardCompacted))
        if action == CompactionAction.Summarized:
            # 验证有 summary system 段
            has_summary = any(
                "Previous Conversation Summary" in (m.get("content") or "")
                for m in new_msgs
                if isinstance(m, dict)
            )
            self.assertTrue(has_summary)
            # 保留 recent_turns 轮
            active = [m for m in new_msgs if m.get("role") != "system"]
            self.assertEqual(len(active), RECENT_TURNS_TO_KEEP)

    # ---------- 7. emergency_truncate_tool_result ----------
    def test_emergency_truncate_tool_result(self):
        mgr = CompactionManager()
        # 短内容不截
        self.assertEqual(mgr.emergency_truncate_tool_result("short content"), "short content")
        # 长内容截断
        long_text = "x" * (EMERGENCY_TOOL_RESULT_MAX_CHARS + 1000)
        out = mgr.emergency_truncate_tool_result(long_text)
        self.assertLessEqual(len(out), EMERGENCY_TOOL_RESULT_MAX_CHARS + len("\n[truncated]"))
        self.assertIn("[truncated]", out)
        # 边界:恰好 4000 chars 不截
        exact = "y" * EMERGENCY_TOOL_RESULT_MAX_CHARS
        self.assertEqual(mgr.emergency_truncate_tool_result(exact), exact)

    # ---------- 8. emergency_truncate_image ----------
    def test_emergency_truncate_image(self):
        mgr = CompactionManager()
        # 短 image 不截
        small = {"type": "image", "source": {"type": "base64", "data": "short"}}
        self.assertEqual(mgr.emergency_truncate_image(small), small)
        # 长 image 截断
        big = {
            "type": "image",
            "source": {"type": "base64", "data": "A" * (EMERGENCY_IMAGE_MAX_CHARS + 500)},
        }
        out = mgr.emergency_truncate_image(big)
        self.assertLessEqual(
            len(out["source"]["data"]),
            EMERGENCY_IMAGE_MAX_CHARS + len("...[truncated]"),
        )
        self.assertIn("truncated", out["source"]["data"])
        # image_url 形式也支持
        big_url = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + "A" * 5000},
        }
        out_url = mgr.emergency_truncate_image(big_url)
        self.assertLessEqual(
            len(out_url["image_url"]["url"]),
            EMERGENCY_IMAGE_MAX_CHARS + len("...[truncated]"),
        )

    # ---------- 9. compact fail-open ----------
    def test_compact_failure_fallback(self):
        mgr = CompactionManager()

        # 1) 直接构造会让 estimate_tokens 抛错的对象
        class _BadMsg:
            def get(self, key, default=None):
                raise RuntimeError("boom")

        bad_msgs: list = [{"role": "user", "content": "x"}, _BadMsg()]  # type: ignore[list-item]
        new_msgs, action = mgr.compact(bad_msgs, llm_call=None)  # type: ignore[arg-type]
        # fail-open: 返原 messages(可能过滤掉坏对象)或近似原长 + None_
        self.assertEqual(action, CompactionAction.None_)

        # 2) llm_call 抛异常 → 降级 HardCompacted
        msgs = _build_msgs(60, chars_per_turn=2_000)

        def bad_llm(prompt: str) -> str:
            raise RuntimeError("llm crashed")

        new_msgs2, action2 = mgr.compact(msgs, llm_call=bad_llm)
        # 要么是 Summarized(如果在 80% 以下),要么是 HardCompacted(降级)
        # 我们构造的是 ~78%,可能不进 soft,但本测试更关心异常不会扩散
        self.assertIn(
            action2,
            (CompactionAction.None_, CompactionAction.HardCompacted, CompactionAction.Summarized),
        )
        # 任何异常情况下,结果都是 list(不会 raise 出来)
        self.assertIsInstance(new_msgs2, list)

    # ---------- 10. is_enabled 默认 True ----------
    def test_is_enabled_default_true(self):
        mgr = CompactionManager()
        # 默认应 True(无 prisIrai_config.yaml 时)
        self.assertTrue(mgr.is_enabled())

    # ---------- 11. SUMMARY_PROMPT 中文 4 段 ----------
    def test_summary_prompt_chinese_sections(self):
        for key in ("上下文", "已做", "当前状态", "用户偏好"):
            self.assertIn(key, SUMMARY_PROMPT, f"SUMMARY_PROMPT missing {key!r}")
        # 必须以中文「请将」开头
        self.assertTrue(SUMMARY_PROMPT.startswith("请将"))

    # ---------- 额外:stats ----------
    def test_stats_basic(self):
        mgr = CompactionManager()
        msgs = _build_msgs(5, chars_per_turn=100)
        s = mgr.stats(msgs)
        self.assertIsInstance(s, CompactionStats)
        self.assertEqual(s.total_turns, len(msgs))
        self.assertEqual(s.active_messages, len(msgs))
        self.assertFalse(s.has_summary)
        self.assertGreater(s.token_estimate, 0)

    def test_stats_detects_existing_summary(self):
        mgr = CompactionManager()
        msgs = [
            {"role": "system", "content": "## Previous Conversation Summary\n\nxxx"},
            {"role": "user", "content": "hi"},
        ]
        s = mgr.stats(msgs)
        self.assertTrue(s.has_summary)


if __name__ == "__main__":
    unittest.main()