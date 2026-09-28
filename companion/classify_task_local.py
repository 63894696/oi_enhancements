#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_task_local.py — M3.58 任务分类生产入口(2026-09-23)
#
# 目的:
#   - 接 adapter_registry.get_adapter("task") + task_conf
#   - 复用 classify_intents.py 的 _build_text + _parse_output 模式
#   - 输出 {task_type, risk_label, jailbreak_label, risk_conf, jb_conf, action_conf,
#           raw, tokens, fallback_used}
#   - 与 fastlane/providers/llm_prisir.py:classify_task 输出的 6 类
#     (code_call / code_qa / creative / long / fast / general) 对齐
#
# 设计:
#   - 6 类任务:code_call/code_qa/creative/long/fast/general
#   - 长上下文(long)> 3000 字符在 fastlane/classify_task 里走长度阈值,
#     这里也支持(若本地推断 long → 触发 long 平台序)
#   - parse_output 与 classify_intents 同款(支持 conf 后缀)
#   - 输出 action = task_type,用于平台偏好序 fallback
#
# 用法:
#   from classify_task_local import classify_task_local
#   out = classify_task_local("帮我写个 Python 函数")
#   # {'task_type': 'code_call', 'risk_label': 'safe', ...
#
#   CLI:
#     python classify_task_local.py --text "什么是装饰器?"
#     python classify_task_local.py --file texts.txt
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from adapter_registry import get_adapter  # noqa: E402

# 6 类任务(对齐 fastlane/providers/llm_prisir.py:classify_task)
VALID_TASKS: set[str] = {
    "code_call", "code_qa", "creative", "long", "fast", "general",
}

# 任务 → 风险映射(任务类型本身不影响 risk,统一 safe)
TASK_TO_RISK: dict[str, str] = {t: "safe" for t in VALID_TASKS}


# ------------------------------------------------------------
# Prompt 段构造(对齐训练数据格式)
# ------------------------------------------------------------
def _build_text(text: str, history: str = "") -> str:
    """M3.61 修正:外层 user/assistant 包裹由 adapter_registry.classify() 自动加,这里只生 body。"""
    h = history if history else "(无历史)"
    return f"用户消息: {text}\n上下文: {h}"


# ------------------------------------------------------------
# 输出解析(与 classify_intents.py 同模式)
# ------------------------------------------------------------
def _parse_output(raw: str) -> dict:
    """从 raw 抽取 {risk_label, jailbreak_label, task_type, conf_*}.

    conf 版 target 格式:`Safety: Safe:0.99\\nJailbreak: No:0.99\\nAction: Code_call:0.95`
    base 版 target 格式:`Safety: Safe\\nJailbreak: No\\nAction: Code_call`
    **0.6B 复读问题**:Action 段常出现 `Code_call:Fast_Sort:Low:Medium:No:1`
    (多个 token 拼成一行),parse 时应取**第一个 `:` 之前的 token 作为 task**,
    conf 后缀必须在 token 是数字时才取。

    **复读 vs task 识别**:
    - 单 token 复读 (`Code_call:Foo:Bar`) → 取第一个段
    - task 段后接 conf (`Code_call:0.95`) → 取 task + conf
    - 多段复读 (`Fast:1.00ｏ\nGeneral:Friendly:1.0\n`) → 取第一个出现的合法 task
    """
    import re
    out: dict = {
        "risk_label": "unknown",
        "jailbreak_label": False,
        "task_type": "general",  # 兜底
        "risk_conf": 0.0,
        "jb_conf": 0.0,
        "action_conf": 0.0,
    }

    action = ""
    action_conf = 0.0
    # 整个 raw 按 "Action:" 之后的整体看(复读可能跨行)
    # 但每行还得独立识别 Safety/Jailbreak/Action
    action_block = ""  # Action: 后的所有内容
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        # risk 段
        if low.startswith("safety:") or "safety:" in low:
            payload = line.split(":", 1)[1].strip()
            parts = payload.split(":")
            out["risk_label"] = parts[0].strip().lower()
            if len(parts) >= 2:
                try:
                    out["risk_conf"] = float(parts[1].strip())
                except ValueError:
                    pass
        # jailbreak 段
        elif low.startswith("jailbreak:") or "jailbreak:" in low:
            payload = line.split(":", 1)[1].strip()
            parts = payload.split(":")
            out["jailbreak_label"] = parts[0].strip().lower() in ("yes", "true", "1")
            if len(parts) >= 2:
                try:
                    out["jb_conf"] = float(parts[1].strip())
                except ValueError:
                    pass
        # action 段 — 取 Action: 之后所有内容拼成一段
        elif low.startswith("action:") or "action:" in low:
            action_block += " " + line.split(":", 1)[1].strip()

    if action_block:
        # 复读处理:把整个 action_block 按 : 切,扫描每个 token,
        # **第一个匹配的 VALID_TASKS 词就是 task_type**,后面紧跟数字则是 conf
        tokens = [t.strip() for t in action_block.split(":")]
        for i, tok in enumerate(tokens):
            tok_clean = tok.lower().strip(" ,.;:!?'\"()[]{}|").rstrip("．。,.;:!?")
            # 检查是否在合法 task 列表
            if tok_clean in VALID_TASKS:
                action = tok_clean
                # conf:看下一个 token
                if i + 1 < len(tokens):
                    next_tok = tokens[i + 1].strip()
                    # 数字(包括 1.00/0.99 这种)
                    try:
                        action_conf = float(re.sub(r"[^\d.]", "", next_tok))
                    except ValueError:
                        action_conf = 0.0
                break

    out["action_conf"] = action_conf
    out["task_type"] = action if action in VALID_TASKS else "general"
    return out


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def classify_task_local(text: str, history: str = "",
                        use_conf: bool = True) -> dict:
    """单条任务分类。

    Args:
        text: 用户消息
        history: 上下文(可空)
        use_conf: True 用 task_conf(带 confidence),False 用 task base

    Returns:
        {task_type, risk_label, jailbreak_label, risk_conf, jb_conf,
         action_conf, raw, tokens, fallback_used}
    """
    name = "task_conf" if use_conf else "task"
    adapter = get_adapter(name)
    prompt_text = _build_text(text, history)
    res = adapter.classify(prompt_text, max_new_tokens=40)
    raw = res["raw"]
    parsed = _parse_output(raw)
    parsed["raw"] = raw
    parsed["tokens"] = res["tokens"]
    parsed["fallback_used"] = True  # 本地走的就是 fallback 路径
    # 长度兜底:与 fastlane/providers/llm_prisir.py 一致
    if len(text) > 3000 and parsed["task_type"] != "long":
        parsed["task_type"] = "long"
    return parsed


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.58 任务分类(本地小模型)")
    ap.add_argument("--text", help="单条消息")
    ap.add_argument("--file", help="批量文本(每行一条)")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读一条")
    ap.add_argument("--history", default="",
                    help="上下文(可空)")
    ap.add_argument("--no-conf", action="store_true",
                    help="用 base adapter(不带 confidence)")
    args = ap.parse_args()

    use_conf = not args.no_conf

    if args.text:
        out = classify_task_local(args.text, history=args.history,
                                  use_conf=use_conf)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.file:
        results: list[dict] = []
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                results.append(classify_task_local(line, use_conf=use_conf))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.stdin:
        text = sys.stdin.read().strip()
        out = classify_task_local(text, use_conf=use_conf)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
