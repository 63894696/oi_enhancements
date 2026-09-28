#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_goal_gate_split.py — M3.83 拆 4 个 head 训练集(2026-09-25)

设计:
  - 4 头 LoRA:reviewer_met / reviewer_blocked / verifier_met / verifier_blocked
  - 每个 head 训练数据 = 其对应 gate 的全样本 (1600/2 = 800 条) + 50% 负样本 (400 条)
  - 即 reviewer_met head:
      reviewer+met (400) + reviewer+blocked (400)
    reviewer_blocked head:
      reviewer+met (400) + reviewer+blocked (400)  -- 同一份数据但侧重 negative
  - **关键**:4 head 实际数据相同,差异只在训练时不同 random seed + 训练目标侧重
  - 推理时:每 head 独立加载,独立 inference,4 个 head 互不影响

为什么这样设计:
  - 4 head 完全独立 adapter → 加载 4 次显存(OOM 风险)
  - 替代:4 head 共用 1 个 base adapter 但不同 prompt prefix → 节省显存但不算"4 头 LoRA"
  - 用户指定 4 头 → 按 4 头训 4 个独立 adapter,实际 inference 时只 load 当前 gate 的 head
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SRC = _HERE / "data_goal_gate.jsonl"


def main():
    if not SRC.exists():
        raise SystemExit(f"找不到 {SRC},先跑 data_prep_goal_gate.py")

    samples = []
    with SRC.open("r", encoding="utf-8") as f:
        for line in f:
            samples.append(json.loads(line))

    print(f"读入 {len(samples)} 条源数据")

    # 按 (gate, action) 分组
    buckets: dict[tuple[str, str], list[dict]] = {
        ("reviewer", "Met"): [],
        ("reviewer", "Blocked"): [],
        ("verifier", "Met"): [],
        ("verifier", "Blocked"): [],
    }
    for s in samples:
        key = (s["_gate_type"], s["_action"])
        buckets[key].append(s)

    for k, v in buckets.items():
        print(f"  {k}: {len(v)} 条")

    # 4 head 训练集:
    # 每个 head 训练数据 = 它对应的 (gate, action) 全部 + 另一半 (gate, !action) 全部
    # 即 reviewer_met head = reviewer+Met(400) + reviewer+Blocked(400) = 800
    # 但每个 head 训练时,target label 改成当前 head 的预期值(强化 head 专用)
    # 实际我们用 _head_name 标识 + 不重写 label,因为模型只看输入 + 输出

    heads = [
        ("reviewer_met", ("reviewer", "Met")),
        ("reviewer_blocked", ("reviewer", "Blocked")),
        ("verifier_met", ("verifier", "Met")),
        ("verifier_blocked", ("verifier", "Blocked")),
    ]

    rng = random.Random(42)

    for head_name, (gate, action) in heads:
        # 正样本 (gate, action) + 负样本 (gate, !action)
        positive = list(buckets[(gate, action)])
        # 负样本 = 同 gate 另一 action
        neg_action = "Blocked" if action == "Met" else "Met"
        negative = list(buckets[(gate, neg_action)])

        # 加 gate_type hint,正样本前缀加 positive mark,负样本加 negative mark
        data = []
        for s in positive:
            obj = {
                "text": f"gate_type: {gate}\nhead_focus: {action}\n{s['text']}",
                "risk_label": s["risk_label"],
                "jailbreak_label": s["jailbreak_label"],
                "_action": s["_action"],  # ground truth
                "_gate_type": gate,
                "_head": head_name,
            }
            data.append(obj)
        for s in negative:
            obj = {
                "text": f"gate_type: {gate}\nhead_focus: {action}\n{s['text']}",
                "risk_label": s["risk_label"],
                "jailbreak_label": s["jailbreak_label"],
                "_action": s["_action"],
                "_gate_type": gate,
                "_head": head_name,
            }
            data.append(obj)

        rng.shuffle(data)

        out = _HERE / f"data_goal_gate_{head_name}.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for obj in data:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

        n_met = sum(1 for s in data if s["_action"] == "Met")
        n_blocked = sum(1 for s in data if s["_action"] == "Blocked")
        print(f"  head={head_name}: {len(data)} 条 (Met={n_met} Blocked={n_blocked}) → {out.name}")


if __name__ == "__main__":
    main()
