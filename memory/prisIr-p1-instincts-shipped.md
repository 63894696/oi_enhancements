---
name: prisIr-p1-instincts-shipped
description: P1-Instincts 置信度累积 + 召回(JSONL 存储 + threshold 0.5 + reinforce ±0.05/0.1)
metadata:
  type: project
---

P1-Instincts(2026-10-02 ship):
- `memory/instincts.py` —— Instinct dataclass + InstinctStore(add/get/reinforce/pin/unpin/recall/forget/stats/list_all/is_enabled/format_for_prompt)
- `memory/tests/test_instincts.py` —— 17/17 PASS
- `memory/oi_memory.py` 加 3 列:applied_count / success_count / last_used_at(ALTER + 探测式回填)
- `companion/prisIragent-companion-web.py:build_messages` 注入段(在 intent_inj 之后、current_user_text 之前)
- JSONL 存储:`~/.prisIrAI/instincts.jsonl`(与 OI Memory SQLite 互补,跨 harness 共享)

置信度累积:
- 起步 0.3
- reinforce(success=True) → confidence += 0.1,封顶 0.95
- reinforce(success=False) → confidence -= 0.05,下限 0.05
- confidence < 0.1 → 自动从库删除
- pin/unpin 钉死防止衰减

召回:
- confidence ≥ 0.5 才注入(threshold)
- token overlap × confidence 加权排序
- top_n=6,format_for_prompt 输出 `[Instincts — 置信度召回]... [End instincts]`

**Why:** 自学习闭环的「小模式」有置信度门槛不再污染 prompt;成熟模式稳定累积,劣质模式自动衰减/删除。
**How to apply:** 用户 30s 无情绪词反馈 → success_count += 1;`/instincts list|forget|pin|unpin|recall` CLI 管理。

边界:
- 不照搬 ECC `instincts_confidence_threshold=0.7`:用户拍板 0.5(与现有自学习兼容)
- 数据走独立 JSONL 不进 OIMemory SQLite(append-only 简单 + 跨 harness 共享)

坑点:
- 子代理 sandbox 隔离:`companion/prisIragent-companion-web.py` 在 audit-2026-09 分支工作树而子代理 worktree 基于 master,Edit 工具拒绝跨分支,需手工应用 patch
- plan 偏差:`_preset_priority_block`/`pitfalls_learner` 段在当前代码 0 命中,build_messages 实际在 line 1095 结束;注入位置是 `intent_inj` append 之后

参考 [[prisIr-p2-rules-shipped]] + [[prisIr-p2-hooks-shipped]]