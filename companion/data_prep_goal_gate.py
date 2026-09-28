#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_goal_gate.py — M3.83 goal_state check_*_* 4 头 LoRA 数据合成(2026-09-25)

目的:
  - 历史数据:C:/temp/h1b_review_*.md + orch_verify_*.md **0 份**(已确认)
  - LLM 合成:每类 400 条目标 (reviewer-met / reviewer-blocked / verifier-met / verifier-blocked)
  - 共 ~1600 条(目标 1500)
  - 训练后产出 4 个独立 LoRA adapter(每个 schema="goal_gate" 不同 output class)

设计:
  - 复用 train_step1.py 已有 schema 机制:扩展 schema="goal_gate_reviewer_met" 等
  - 但 train_step1.py choices 是写死的 enum,这里改成 **统一 schema="goal_gate"** + 用 risk_label 区分类别
  - 风险等级映射 4 类:
      reviewer_met        → Safety: Low     (PASS 类)
      reviewer_blocked    → Safety: High    (FAIL 类 - 缺字段)
      verifier_met        → Safety: Low
      verifier_blocked    → Safety: High
  - 但这样 reviewer 和 verifier 不可分,4 头就没意义
  - **改用 _action 字段做 4 头区分**:
      reviewer_met      → Action: Met
      reviewer_blocked  → Action: Blocked
      verifier_met      → Action: Met
      verifier_blocked  → Action: Blocked
  - **关键决策**:不扩 train_step1 schema choices,而是用 **schema="goal_gate"** +
    `obj["_gate_type"]` 选 risk_label / 用 obj["_action"] 区分 4 类
  - 但 train_step1 不知道目标 4 分类 → 改 train_step1 schema 加 "goal_gate"

合成规则:
  - text 模板 = 一段 markdown 报告内容 (reviewer/verifier 风格)
  - 类标:Met (通过) / Blocked (缺字段/过短/无报告)
  - 加 paraphrase 变体让模型学习 invariant features(关键短语模式)

prompt 风格:
  - "任务:goal_state check_reviewer_gate / check_verifier_gate"
  - 输入:报告全文 + task_id(可选)
  - 输出:Safety + Action

数据增强:
  - 每类 ~400 条
  - 50% 短模板(< 600 chars,典型 blocked)
  - 50% 完整长模板(> 600 chars,典型 met) + 注入字段 + verdict
  - paraphrase 同义改写:报告 → review / assessment / evaluation;verdict → PASS / FAIL / approve / reject
  - 英文 + 中文混合 70/30
"""
from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(42)

_HERE = Path(__file__).resolve().parent
OUT = _HERE / "data_goal_gate.jsonl"


# ─── Reviewer 模板 ────────────────────────────────────────────────

REVIEWER_HEADERS = [
    "# H1b Review Report",
    "# H1b Code Review",
    "# Reviewer Assessment",
    "# 评审报告",
    "# 代码审查记录",
    "# H1b Review Notes",
    "# Reviewer Verdict",
]

REVIEWER_INTROS = [
    "Reviewed the implementation against the specification.",
    "本报告对实现的 spec drift 进行审查。",
    "Below is my code review of the submitted changes.",
    "Code-level review of the diff against agreed spec.",
    "对提交的实现做代码级审查,以下是发现。",
    "Examining the changes line by line for spec adherence.",
]

REVIEWER_DRIFT_LINES = [
    "spec_drift_rate: 0.02 (low)",
    "spec_drift_rate: 0.08 (acceptable)",
    "spec_drift_rate: 0.15 (moderate)",
    "spec_drift_rate: 0.04 (acceptable)",
    "spec_drift_rate: 0.01 (excellent)",
    "spec_drift率: 0.05 (低)",
    "spec drift: 3.2% (low)",
    "spec drift: 7.1% (acceptable)",
    "drift_rate: 0.062",
    "drift rate: 0.04",
]

REVIEWER_VERDICTS = [
    "verdict: approve",
    "verdict: pass",
    "verdict: APPROVE",
    "verdict: PASS",
    "verdict: LGTM",
    "verdict: Accept",
    "verdict: ship-it",
    "verdict: 批准",
    "verdict: 通过",
    "Verdict: approve (ship)",
]

REVIEWER_DETAIL_TEMPLATES = [
    """Findings:
- Function naming consistent with spec
- Error handling matches §3.2 of design doc
- No off-spec dependencies added
- Test coverage 87% (target 80%)
- CI green on all jobs

Notes: implementation aligns well with the agreed spec. Minor
naming convention drift in helper module, but documented in the
README. Recommend approving after a quick second look at the
naming. Overall, low drift and ready to merge.""",

    """详细发现:
- 函数命名规范
- 错误处理对齐 §3.2
- 无额外依赖
- 测试覆盖 87% (目标 80%)
- CI 全绿

建议:批准合并。整体低 drift,符合规范。""",

    """Findings:
- spec_drift: low (3.2%)
- API contracts match docstring
- Migration script idempotent (verified)
- Backward compat preserved for v2 callers
- One stale comment in module header (cosmetic)

Overall: low drift, approve.""",

    """Findings:
- 3 minor issues, none blocking
- docs sync'd
- perf impact negligible
- security scan clean

Verdict: PASS""",

    """Findings:
- Off-by-one bug fixed in PR #482
- Race in retry logic mitigated with backoff
- Memory leak in cache layer patched
- All reviewer comments addressed

verdict: approve (ship)
""",

    """Findings:
- Spec alignment verified
- Edge cases covered (null/empty/long input)
- Logging structured JSON compatible
- Metric exports Prometheus format

verdict: pass""",

    """## Items Reviewed

1. public API surface — matches spec §2
2. error types — aligned with spec §4
3. config schema — extends spec §5 by one optional field
4. migration guide — present and accurate

## Concerns
None blocking.

## Summary
Low drift, ready to ship.""",

    """## Review Notes

Reviewed 24 files, 1372 lines added, 213 lines removed.

- Linter: clean
- Formatter: applied
- Type check: clean
- Unit tests: 142 added, all passing
- Integration: green

verdict: LGTM (merge)""",

    """## 审查项

- API 接口对齐规范
- 错误处理规范
- 配置 schema 兼容
- 迁移指南完整

verdict: 批准""",

    """## Items
1. Coverage 91%
2. CI green
3. Lint clean
4. Docs updated

verdict: approve""",
]

# Blocked 模板(缺 spec_drift_rate / verdict / 太短)
REVIEWER_BLOCKED_TEMPLATES = [
    """Quick look at the diff.

Looks reasonable. Approving.
""",

    """## Review
代码大致没问题,合并吧。""",

    """Reviewed.

Looks good.
""",

    """## Findings
No issues found. Merge it.

(30 chars total — too brief)""",

    """# Review

我审过了,没问题。

verdict: approve""",   # 缺 spec_drift_rate

    """# H1b Review

spec_drift_rate: 0.05

(spec_drift 有了但没 verdict)""",

    """# H1b Review

verdict: pass

(verdict 有了但没 spec_drift)""",

    """# Quick Review

代码改了三个文件,合并。
""",

    """# Reviewer Note

整个 PR 就改了几行,没大问题。

verdict: LGTM""",  # 缺 spec_drift

    """# Review

代码看了,改动不大。

LGTM""",  # 短且模糊
]


# ─── Verifier 模板 ────────────────────────────────────────────────

VERIFIER_HEADERS = [
    "# H8 Verifier Report",
    "# Build Verification",
    "# H8 Build Status",
    "# 验证报告",
    "# Verifier Assessment",
    "# Build Verification Notes",
]

VERIFIER_INTROS = [
    "Ran the full build pipeline against the merged commit.",
    "本报告对合并后 commit 跑完整 build 流程。",
    "Executed build + tests + smoke verification.",
    "Below is the verification result for the build.",
    "执行 build + 测试 + 烟雾验证。",
    "Build pipeline output and verdict below.",
]

VERIFIER_BUILD_STATUS = [
    "Build Status: PASS",
    "Build Status: SUCCESS",
    "Build Status: green",
    "Build Status: passed",
    "Build Status: 通过",
    "Build Status: 成功",
]

VERIFIER_VERDICTS = [
    "verdict: pass",
    "verdict: approve",
    "verdict: PASS",
    "verdict: APPROVE",
    "verdict: 通过",
    "verdict: 批准",
]

VERIFIER_DETAIL_TEMPLATES = [
    """## Build Pipeline
1. compile: 4m 12s — success
2. unit tests: 312/312 passed
3. integration: 18/18 suites green
4. e2e smoke: 7/7 scenarios pass
5. packaging: artifacts uploaded

## Artifacts
- dist/v2.4.1.tar.gz (4.2 MB)
- dist/v2.4.1.tar.gz.sig

## Smoke
Health endpoint OK. Telemetry ingested.

verdict: pass""",

    """## Build 流水
1. compile: 4m 12s — 成功
2. 单测: 312/312 通过
3. 集成: 18/18 green
4. e2e: 7/7 场景通过
5. 打包: 产物已上传

verdict: 通过""",

    """## Build
- Compile: success (3m 47s)
- Tests: 412 passed, 0 failed, 2 skipped
- Coverage: 89% (target 80%)
- Static analysis: 0 errors, 3 warnings (cosmetic)
- Security scan: clean

Build Status: PASS
verdict: approve""",

    """## Build Verification
1. Build pipeline: success
2. All tests: green
3. Smoke tests: pass
4. Coverage: 87%

verdict: pass""",

    """## Build Results
- Compile: success
- Test suite: 287 passed
- Integration: green
- Smoke: pass
- Coverage: 91%
- Lint: clean

Build Status: SUCCESS
verdict: approve""",

    """## Pipeline Steps
- lint: clean
- type-check: clean
- unit: 156/156 passed
- integration: 22/22 passed
- e2e: 8/8 passed
- bundle: success

Build Status: PASS
verdict: pass""",

    """## 验证结果
- 编译: 成功
- 单元测试: 156/156 通过
- 集成: 22/22 通过
- e2e: 8/8 通过
- 覆盖率: 91%

verdict: 通过""",

    """## Build Steps
1. compile: success
2. test: green
3. e2e: pass
4. deploy-staging: success

Build Status: PASS
verdict: pass""",

    """## Verifier Report

Build pipeline executed successfully. All stages green.

- compile: success
- unit: 156/156 passed
- integration: green
- smoke: pass

Build Status: PASS
verdict: approve""",

    """## 验证报告

执行完整 build,全部阶段通过。

- 编译: 成功
- 单测: 156/156 通过
- 集成: 通过
- 烟雾: 通过

verdict: 通过""",
]

VERIFIER_BLOCKED_TEMPLATES = [
    """Quick run. Build seems fine.
""",

    """## Build
跑过了,绿。

verdict: pass""",   # 缺 "Build Status"

    """## Build Status

Build looks good.""",  # 缺具体 status + verdict

    """## Verifier

Build done. Looks ok.
""",

    """# H8 Verify

跑完了,没问题。
""",  # 短

    """Build run.

verdict: pass""",  # 太短

    """## Verification

执行完成,绿了。
""",

    """# H8 Build Report

Build Status: PASS

(no verdict)""",  # 缺 verdict

    """## Verifier

Build Status: PASS

Looks good.""",  # 缺 verdict

    """# Verify

成功。
""",
]


# ─── Synthesis 函数 ────────────────────────────────────────────────

def _make_reviewer_met():
    """Generate one reviewer-met sample (报告完整 + 字段齐全 + 长)."""
    parts = []
    parts.append(random.choice(REVIEWER_HEADERS))
    parts.append("")
    parts.append(random.choice(REVIEWER_INTROS))
    parts.append("")
    # 1-3 drift 引用
    for _ in range(random.randint(1, 3)):
        parts.append(random.choice(REVIEWER_DRIFT_LINES))
    parts.append("")
    # 1-2 detail templates
    for _ in range(random.randint(1, 2)):
        parts.append(random.choice(REVIEWER_DETAIL_TEMPLATES))
    parts.append("")
    # 1-3 verdicts
    for _ in range(random.randint(1, 3)):
        parts.append(random.choice(REVIEWER_VERDICTS))
    text = "\n".join(parts)
    # pad 到 ≥ 600 chars
    while len(text) < 700:
        text += "\n" + random.choice(REVIEWER_DETAIL_TEMPLATES)
    return text


def _make_reviewer_blocked():
    """Generate one reviewer-blocked sample (缺 spec_drift_rate 或 verdict 或 太短)."""
    tmpl = random.choice(REVIEWER_BLOCKED_TEMPLATES)
    # 部分模板可能 ≥ 600 chars 是 50% 概率,这里都强制 < 700
    return tmpl


def _make_verifier_met():
    parts = []
    parts.append(random.choice(VERIFIER_HEADERS))
    parts.append("")
    parts.append(random.choice(VERIFIER_INTROS))
    parts.append("")
    # 1-3 build status
    for _ in range(random.randint(1, 3)):
        parts.append(random.choice(VERIFIER_BUILD_STATUS))
    parts.append("")
    for _ in range(random.randint(1, 2)):
        parts.append(random.choice(VERIFIER_DETAIL_TEMPLATES))
    parts.append("")
    for _ in range(random.randint(1, 3)):
        parts.append(random.choice(VERIFIER_VERDICTS))
    text = "\n".join(parts)
    while len(text) < 700:
        text += "\n" + random.choice(VERIFIER_DETAIL_TEMPLATES)
    return text


def _make_verifier_blocked():
    return random.choice(VERIFIER_BLOCKED_TEMPLATES)


# ─── 主流程 ─────────────────────────────────────────────────────

def main():
    n_per_class = 400  # 4 × 400 = 1600 总目标

    samples: list[dict] = []

    for _ in range(n_per_class):
        samples.append({
            "text": _make_reviewer_met(),
            "risk_label": "low",
            "jailbreak_label": False,
            "_action": "Met",
            "_gate_type": "reviewer",
        })

    for _ in range(n_per_class):
        samples.append({
            "text": _make_reviewer_blocked(),
            "risk_label": "high",
            "jailbreak_label": False,
            "_action": "Blocked",
            "_gate_type": "reviewer",
        })

    for _ in range(n_per_class):
        samples.append({
            "text": _make_verifier_met(),
            "risk_label": "low",
            "jailbreak_label": False,
            "_action": "Met",
            "_gate_type": "verifier",
        })

    for _ in range(n_per_class):
        samples.append({
            "text": _make_verifier_blocked(),
            "risk_label": "high",
            "jailbreak_label": False,
            "_action": "Blocked",
            "_gate_type": "verifier",
        })

    # shuffle
    random.shuffle(samples)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # stats
    by_class: dict[str, int] = {}
    by_gate: dict[str, int] = {}
    for s in samples:
        k = f"{s['_gate_type']}_{s['_action']}"
        by_class[k] = by_class.get(k, 0) + 1
        by_gate[s["_gate_type"]] = by_gate.get(s["_gate_type"], 0) + 1

    # length stats
    lens = [len(s["text"]) for s in samples]
    met_lens = [len(s["text"]) for s in samples if s["_action"] == "Met"]
    blocked_lens = [len(s["text"]) for s in samples if s["_action"] == "Blocked"]

    print(f"OK 写出 {OUT} ({len(samples)} 条)")
    print(f"   按类: {by_class}")
    print(f"   按 gate: {by_gate}")
    print(f"   长度: min={min(lens)} max={max(lens)} avg={sum(lens)//len(lens)}")
    print(f"   Met 长度: min={min(met_lens)} max={max(met_lens)} avg={sum(met_lens)//len(met_lens)}")
    print(f"   Blocked 长度: min={min(blocked_lens)} max={max(blocked_lens)} avg={sum(blocked_lens)//len(blocked_lens)}")
    print()
    print(f"注意:这是 LLM 合成数据集(M3.83 数据不足)。")
    print(f"     真实部署时应从 C:/temp/h1b_review_*.md + orch_verify_*.md 收集。")
    print(f"     部署后在线收集 reviewer/verifier 真实报告,迭代训练。")


if __name__ == "__main__":
    main()
