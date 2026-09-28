#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_deadlock.py — M3.82 deadlock 4 分类数据集生成(2026-09-25)

目的:
  - deadlock_detect.py 当前 3 个硬规则:stalled / zero_progress / slow
  - 本脚本生成 4 分类训练集(stalled / zero_progress / slow / ok),供 laya 路由 + race_impl 用
  - 5 维特征:round_count, file_ops, elapsed_sec, position, time_since_last_update
  - 标签 = 4 类 deadlock 状态,供 laya.router_questions() / guard_questions() 决策

设计:
  - **真实标注优先**:用 PositionTracker.update() 模拟 200 段 task 轨迹,每段 10-50 轮
  - 段分类规则同 deadlock_detect.py 当前硬规则:
      - stalled:    elapsed_since_last > stall_threshold_sec (default 120)
      - zero_progress: round_count > 3 AND file_ops == 0 AND position in reading/llm_call
      - slow:       round_count > 15 AND elapsed_sec > 300 (但未 stalled)
      - ok:         其它(正常推进)
  - 200 段 × ~10 抽 → ~2000 条 4 分类样本
  - 每条样本 = 序列化"当前位置快照"为 prompt + 4 选 1 label

为什么 laya zero-shot 路径(M3.81 决定):
  - laya 是冻结 ModernBERT 不支持 fine-tune
  - 改用 laya.router_questions() 4 维 → 4 分类 decision 路由

输出:
  data/data_deadlock_train.jsonl (2000 条,带 text/label)
  data/data_deadlock_eval.jsonl  (独立 holdout 200 条,合成但不与 train 重叠)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any


# ---- 4 类 deadlock label ----
LABELS = ("stalled", "zero_progress", "slow", "ok")
# label 编号:laya router_questions 的 choice index 对齐
LABEL_TO_CHOICE = {"stalled": 0, "zero_progress": 1, "slow": 2, "ok": 3}

# ---- 阈值(对齐 deadlock_detect.py 硬规则)----
STALL_THRESHOLD_SEC = 120
ZERO_PROGRESS_ROUND_MIN = 3
SLOW_ROUND_MIN = 15
SLOW_ELAPSED_SEC = 300

# ---- agent_type 池 ----
AGENT_TYPES = ("dev", "reviewer", "spec-extract", "verifier")
POSITIONS = ("reading", "writing", "editing", "bash", "llm_call", "idle", "done", "failed")


# ============================================================
# 真实轨迹生成(核心:模拟一段 task 推进过程)
# ============================================================

def _label_for_snapshot(round_count: int, file_ops: int, elapsed_sec: float,
                        position: str, time_since_last_update: float) -> str | None:
    """单条快照 → 4 类 label。

    按 deadlock_detect.py 现有 3 条规则的优先顺序判定:
      1. stalled    > STALL_THRESHOLD_SEC 时间无位置变更
      2. zero_progress > 3 轮无 file_ops 且在 reading/llm_call
      3. slow       > 15 轮且 elapsed_sec > 300 但未 stalled
      4. ok         其它
    """
    if time_since_last_update > STALL_THRESHOLD_SEC:
        return "stalled"
    if (round_count > ZERO_PROGRESS_ROUND_MIN
            and file_ops == 0
            and position in ("reading", "llm_call")):
        return "zero_progress"
    if (round_count > SLOW_ROUND_MIN
            and elapsed_sec > SLOW_ELAPSED_SEC
            and time_since_last_update <= STALL_THRESHOLD_SEC):
        return "slow"
    return "ok"


def _simulate_trajectory(label_target: str, rng: random.Random) -> list[dict]:
    """生成一段 task 轨迹(8-20 个快照),目标是 label_target 的样本占多数。

    构造 4 类典型轨迹:
      - stalled:    推进几轮后突然"卡住" → 后续快照 elapsed_since_last 飙到 200+
      - zero_progress: 持续 4-8 轮 reading/llm_call + file_ops=0
      - slow:        持续 16+ 轮但 elapsed_sec > 300(每轮 ~20-30s)
      - ok:          正常推进(每轮 5-15s,file_ops 持续涨)
    """
    snaps: list[dict] = []
    n_snaps = rng.randint(8, 20)
    task_id = rng.randint(1000, 99999)
    agent_type = rng.choice(AGENT_TYPES)
    start_ts = time.time() - rng.uniform(60, 3600)

    if label_target == "stalled":
        # 前 3-5 个快照正常,然后卡住
        n_normal = rng.randint(3, 5)
        for i in range(n_normal):
            snaps.append(_make_snapshot(i, task_id, agent_type, start_ts,
                                        "ok", rng))
        # 后续快照时间不变(模拟卡住),elapsed_since_last 累积到 150-300
        stuck_ts = start_ts + sum(1 for _ in range(n_normal)) * rng.uniform(8, 20)
        for i in range(n_snaps - n_normal):
            snaps.append(_make_snapshot_stuck(i + n_normal, task_id, agent_type,
                                              stuck_ts, rng))

    elif label_target == "zero_progress":
        # 4-8 轮 reading/llm_call, file_ops 一直 0, round_count 持续涨
        n_snaps_actual = rng.randint(4, 8)
        for i in range(n_snaps_actual):
            snaps.append(_make_snapshot(i, task_id, agent_type, start_ts,
                                        "zero_progress", rng))
        # 可能再加 1-2 个 ok 快照增加多样性
        for i in range(n_snaps - n_snaps_actual):
            snaps.append(_make_snapshot(n_snaps_actual + i, task_id, agent_type,
                                        start_ts, "ok", rng))

    elif label_target == "slow":
        # 16-22 轮,每轮 20-30s → elapsed_sec 远超 300
        n_snaps_actual = rng.randint(16, 22)
        # 截断到 n_snaps
        n_snaps_actual = min(n_snaps_actual, n_snaps)
        for i in range(n_snaps_actual):
            snaps.append(_make_snapshot(i, task_id, agent_type, start_ts,
                                        "slow", rng))

    else:  # ok
        for i in range(n_snaps):
            snaps.append(_make_snapshot(i, task_id, agent_type, start_ts,
                                        "ok", rng))

    return snaps


def _make_snapshot(idx: int, task_id: int, agent_type: str,
                   start_ts: float, target_label: str,
                   rng: random.Random) -> dict:
    """生成单条快照(刻意构造目标 label)。"""
    round_count: int
    file_ops: int
    elapsed_sec: float
    position: str
    time_since_last_update: float

    if target_label == "ok":
        round_count = rng.randint(1, 10)
        file_ops = min(round_count, rng.randint(0, round_count))
        elapsed_sec = round_count * rng.uniform(5, 15)
        position = rng.choice(POSITIONS[:5])
        time_since_last_update = rng.uniform(2, 60)
    elif target_label == "zero_progress":
        round_count = rng.randint(4, 12)
        file_ops = 0  # 关键
        elapsed_sec = round_count * rng.uniform(8, 18)
        position = rng.choice(("reading", "llm_call"))  # 关键
        time_since_last_update = rng.uniform(2, 30)
    elif target_label == "slow":
        round_count = rng.randint(16, 25)
        file_ops = round_count // 2
        elapsed_sec = round_count * rng.uniform(20, 30)  # > 300s
        position = rng.choice(POSITIONS[:5])
        time_since_last_update = rng.uniform(2, 60)
    else:  # stalled
        round_count = rng.randint(2, 12)
        file_ops = rng.randint(0, round_count)
        elapsed_sec = round_count * rng.uniform(8, 25)
        position = rng.choice(POSITIONS[:5])
        time_since_last_update = rng.uniform(150, 400)  # > 120s

    last_seen = start_ts + elapsed_sec - time_since_last_update
    return {
        "task_id": task_id,
        "agent_type": agent_type,
        "position": position,
        "round_count": round_count,
        "file_ops": file_ops,
        "elapsed_sec": elapsed_sec,
        "time_since_last_update": round(time_since_last_update, 1),
        "last_seen_offset": round(elapsed_sec - time_since_last_update, 1),
        "target_label": target_label,
    }


def _make_snapshot_stuck(idx: int, task_id: int, agent_type: str,
                         stuck_ts: float, rng: random.Random) -> dict:
    """构造"卡住"快照:time_since_last_update > 120s。"""
    round_count = rng.randint(2, 8)
    file_ops = rng.randint(0, round_count)
    elapsed_sec = rng.uniform(150, 600)
    position = rng.choice(POSITIONS[:5])
    time_since_last_update = rng.uniform(130, 400)
    return {
        "task_id": task_id,
        "agent_type": agent_type,
        "position": position,
        "round_count": round_count,
        "file_ops": file_ops,
        "elapsed_sec": round(elapsed_sec, 1),
        "time_since_last_update": round(time_since_last_update, 1),
        "last_seen_offset": round(elapsed_sec - time_since_last_update, 1),
        "target_label": "stalled",
    }


# ============================================================
# 5 维特征 → prompt 文本
# ============================================================

def _to_prompt(snap: dict) -> str:
    """把快照序列化为 laya 可读的 prompt。

    关键发现(M3.82 bench rev3):laya.triage_questions() 对结构化 agent 字段
    不敏感(urgency 永远 ≈ 0),需要把它转成"用户感知语言"——包含"卡/慢/不动"等
    情绪关键词,frustration 才有 signal。

    模板:position × {reading/writing/llm_call} → 用户感知描述
          time_since_last_update → "等了很久"/"最近还在更新"
          file_ops == 0 → "没写出文件"/"一直在读"
    """
    pos_desc_map = {
        "reading":   "在读文件",
        "writing":   "在写文件",
        "editing":   "在编辑文件",
        "bash":      "在跑命令",
        "llm_call":  "在思考下一步",
        "idle":      "空闲中",
        "done":      "已完成",
        "failed":    "已失败",
    }
    pos_desc = pos_desc_map.get(snap["position"], "在工作中")
    ts = snap["time_since_last_update"]
    if ts > 120:
        wait_desc = f"已经 {ts:.0f} 秒没动静了,完全卡住"
    elif ts > 60:
        wait_desc = f"已经 {ts:.0f} 秒没更新,担心卡住"
    elif ts > 30:
        wait_desc = f"已经 {ts:.0f} 秒没动静"
    else:
        wait_desc = f"最近还在更新({ts:.0f}秒前)"

    file_ops_desc = (
        "一直读文件但没写出任何东西"
        if snap["file_ops"] == 0
        else f"已经写了 {snap['file_ops']} 个文件"
    )

    rounds = snap["round_count"]
    elapsed = snap["elapsed_sec"]

    return (
        f"我的 agent task#{snap['task_id']}({snap['agent_type']}) {pos_desc},"
        f"{file_ops_desc},"
        f"已经跑了 {elapsed:.0f} 秒({rounds} 轮),{wait_desc}。"
    )


# ============================================================
# 主数据生成
# ============================================================

def generate_dataset(n_segments: int = 200, seed: int = 42,
                     holdout: bool = False) -> list[dict]:
    """生成 4 类 deadlock 训练样本。

    每段 trajectory 产生 10 条样本(每个快照 → 1 条)。
    总条数 ≈ n_segments × 10 = 2000。
    """
    rng = random.Random(seed + (1 if holdout else 0))
    # 4 类配比:stalled 25% / zero_progress 30% / slow 15% / ok 30%
    # 注:实际 task 推进中 stalled + zero_progress 比 slow 常见
    weights = {"stalled": 25, "zero_progress": 30, "slow": 15, "ok": 30}

    out: list[dict] = []
    for _ in range(n_segments):
        # 按权重选 target label
        r = rng.uniform(0, sum(weights.values()))
        cum = 0
        target = "ok"
        for lbl, w in weights.items():
            cum += w
            if r <= cum:
                target = lbl
                break

        snaps = _simulate_trajectory(target, rng)
        for snap in snaps:
            # 用 _label_for_snapshot 双校验(防御性:trajectory 已刻意构造,
            # 但混入的 ok 快照在 stalled 段里就该是 ok)
            actual_label = _label_for_snapshot(
                snap["round_count"], snap["file_ops"], snap["elapsed_sec"],
                snap["position"], snap["time_since_last_update"]
            )
            if actual_label is None:
                continue
            out.append({
                "text": _to_prompt(snap),
                "label": actual_label,
                "task_id": snap["task_id"],
                "agent_type": snap["agent_type"],
                "_features": {
                    "round_count": snap["round_count"],
                    "file_ops": snap["file_ops"],
                    "elapsed_sec": round(snap["elapsed_sec"], 1),
                    "position": snap["position"],
                    "time_since_last_update": snap["time_since_last_update"],
                },
                "_source": "trajectory_synth",
                "_holdout": holdout,
            })

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.82 deadlock 4 分类数据生成")
    ap.add_argument("--n-train-segments", type=int, default=200)
    ap.add_argument("--n-eval-segments", type=int, default=20)
    ap.add_argument("--out-train", default="data/data_deadlock_train.jsonl")
    ap.add_argument("--out-eval", default="data/data_deadlock_eval.jsonl")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    here = Path(__file__).parent
    print(f"[data_prep_deadlock] seed={args.seed}")

    train = generate_dataset(args.n_train_segments, seed=args.seed, holdout=False)
    print(f"[data_prep_deadlock] train samples: {len(train)}")

    eval_ = generate_dataset(args.n_eval_segments, seed=args.seed + 100, holdout=True)
    print(f"[data_prep_deadlock] eval samples: {len(eval_)}")

    # 统计
    from collections import Counter
    train_ct = Counter(s["label"] for s in train)
    eval_ct = Counter(s["label"] for s in eval_)
    print(f"[data_prep_deadlock] train 分布: {dict(train_ct)}")
    print(f"[data_prep_deadlock] eval  分布: {dict(eval_ct)}")

    # 写文件
    out_train = here / args.out_train
    out_train.parent.mkdir(parents=True, exist_ok=True)
    with out_train.open("w", encoding="utf-8") as f:
        for s in train:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[data_prep_deadlock] → {out_train} ({len(train)} 条)")

    out_eval = here / args.out_eval
    with out_eval.open("w", encoding="utf-8") as f:
        for s in eval_:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[data_prep_deadlock] → {out_eval} ({len(eval_)} 条)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
