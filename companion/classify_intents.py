#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# classify_intents.py — M3.50 聊天意图分类生产入口(2026-09-23)
#
# 目的:
#   - 接 adapter_registry.get_adapter("intents") + intents_conf
#   - 复用 classify_log.py 的 _build_text + _parse_output 模式
#   - 输出 {intent, risk_label, jailbreak_label, risk_conf, jb_conf, action_conf,
#           raw, tokens, fallback_used}
#
# 设计:
#   - 5 类意图:chat/code/search/tool_call/roleplay(对齐 companion_jev.py)
#   - build_text 格式: "用户消息: <text>\n上下文: <history>"
#   - 解析 target:
#       "Safety: Safe\nJailbreak: No\nAction: Chat"
#   - conf 版:
#       "Safety: Safe:0.99\nJailbreak: No:0.99\nAction: Chat:0.95"
#
# 用法:
#   from classify_intents import classify_intents
#   out = classify_intents("帮我打开浏览器", history="用户在调试")
#   # {'intent': 'tool_call', 'risk_label': 'safe', 'jailbreak_label': False,
#   #  'risk_conf': 0.99, 'jb_conf': 0.99, 'action_conf': 0.95,
#   #  'raw': '...', 'tokens': 18}
#
#   CLI:
#     python classify_intents.py --text "讲个笑话"
#     python classify_intents.py --file texts.txt
#     echo "..." | python classify_intents.py --stdin
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from adapter_registry import get_adapter  # noqa: E402

# 5 类意图(对齐 companion_jev.py:115-125)
VALID_INTENTS: set[str] = {"chat", "code", "search", "tool_call", "roleplay"}

# 风险等级映射(action → 风险,所有 intent 都是 safe;后续可扩展 urgent/creative 等)
INTENT_TO_RISK: dict[str, str] = {
    "chat": "safe",
    "code": "safe",
    "search": "safe",
    "tool_call": "low",      # tool_call 涉及动作,标 low 提示用户
    "roleplay": "safe",
}


# ------------------------------------------------------------
# Prompt 段构造(对齐训练数据格式)
# ------------------------------------------------------------
# M3.61 关键修 B:adapter_registry.classify() 已经在外层按 schema 加了 user/assistant 包裹
# (`user\\n{text}\\nassistant\\n`);_build_text 只生成训练时 text 字段值的原文
# (即"用户消息:...\\n上下文:...")。不能在 _build_text 里再套一次 ChatML(双重包裹)。
def _build_text(text: str, history: str = "") -> str:
    """组装训练/推理 prompt 段(text 字段值)。

    与 train_step1.PROMPT_TEMPLATE_SAFETY 对齐(text 是 user 标签内的 body):
    body = "用户消息: {text}\\n上下文: {history}"
    外层 user/assistant 由 adapter_registry.classify() 自动加。
    """
    h = history if history else "(无历史)"
    return f"用户消息: {text}\n上下文: {h}"


# ------------------------------------------------------------
# 输出解析(与 classify_log.py 同模式)
# ------------------------------------------------------------
def _parse_output(raw: str) -> dict:
    """从 raw 抽取 {risk_label, jailbreak_label, intent, conf_*}.

    conf 版 target 格式:`Safety: Safe:0.99\\nJailbreak: No:0.99\\nAction: Chat:0.95`
    base 版 target 格式:`Safety: Safe\\nJailbreak: No\\nAction: Chat`
    """
    out: dict = {
        "risk_label": "unknown",
        "jailbreak_label": False,
        "intent": "unknown",
        "risk_conf": 0.0,
        "jb_conf": 0.0,
        "action_conf": 0.0,
    }

    # 提取 Action 段(intent)
    action = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        # risk 段
        if low.startswith("safety:") or "safety:" in low:
            payload = line.split(":", 1)[1].strip()
            # conf 后缀 :0.99
            if ":" in payload and payload.split(":", 1)[1].strip().replace(".", "").isdigit():
                r, c = payload.split(":", 1)
                out["risk_label"] = r.strip().lower()
                try:
                    out["risk_conf"] = float(c)
                    continue
                    # if have risk continue
                except ValueError:
                    pass
            out["risk_label"] = payload.strip().lower()
        # jailbreak 段
        elif low.startswith("jailbreak:") or "jailbreak:" in low:
            payload = line.split(":", 1)[1].strip()
            if ":" in payload and payload.split(":", 1)[1].strip().replace(".", "").isdigit():
                jb, c = payload.split(":", 1)
                out["jailbreak_label"] = jb.strip().lower() in ("yes", "true", "1")
                try:
                    out["jb_conf"] = float(c)
                    continue
                except ValueError:
                    pass
            out["jailbreak_label"] = payload.strip().lower() in ("yes", "true", "1")
        # action 段
        elif low.startswith("action:") or "action:" in low:
            payload = line.split(":", 1)[1].strip()
            if ":" in payload and payload.split(":", 1)[1].strip().replace(".", "").isdigit():
                a, c = payload.split(":", 1)
                action = a.strip().lower()
                try:
                    out["action_conf"] = float(c)
                    continue
                except ValueError:
                    pass
            action = payload.strip().lower()

    # intent 是 action(chat/code/search/tool_call/roleplay)
    # 防复读:如果 raw 出现多段 Action:...,只取最后一个
    out["intent"] = action if action in VALID_INTENTS else "unknown"
    return out


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def classify_intents(text: str, history: str = "",
                     use_conf: bool = True) -> dict:
    """单条意图分类。

    Args:
        text: 用户消息
        history: 上下文(可空)
        use_conf: True 用 intents_conf(带 confidence),False 用 intents base

    Returns:
        {intent, risk_label, jailbreak_label, risk_conf, jb_conf,
         action_conf, raw, tokens}
    """
    name = "intents_conf" if use_conf else "intents"
    adapter = get_adapter(name)
    prompt_text = _build_text(text, history)
    res = adapter.classify(prompt_text, max_new_tokens=40)
    raw = res["raw"]
    parsed = _parse_output(raw)
    parsed["raw"] = raw
    parsed["tokens"] = res["tokens"]
    return parsed


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.50 意图分类(本地小模型)")
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
        out = classify_intents(args.text, history=args.history,
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
                results.append(classify_intents(line, use_conf=use_conf))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.stdin:
        text = sys.stdin.read().strip()
        out = classify_intents(text, use_conf=use_conf)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())