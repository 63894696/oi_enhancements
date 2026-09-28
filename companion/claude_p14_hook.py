#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# claude_p14_hook.py — Claude Code Stop hook 调用 P1-4 增量入库(2026-09-22)
#
# 目的:
#   - 每轮 Claude Code assistant 回答结束后,自动评估本轮是否产生阶段成果
#   - 阶段成果段级 sha256 增量入 Obsidian(复用 fcontent_root/_incremental)
#   - 复用 p14_ingest.py 共享模块,与陪聊入口同一索引,跨入口去重
#
# 调用:
#   - stdin: Stop hook JSON {session_id, transcript_path, cwd, last_assistant_message, ...}
#   - 直接 exec 该脚本(无需参数),hook 自动从 stdin 读 JSON
#   - stdout: 可选 context 提示(给 Claude 看的)
#   - exit 0 = 不阻塞(默认);exit 2 = 阻止 stop
#
# 设计:
#   - 同步跑 Jev 评估 + 写入(约 1.2-2.5s)— 用户已停手,Claude Code 给 hook 60s 预算,不阻塞 UI
#   - 失败 fail-open:所有异常被吞,绝不让 hook 阻止 stop
#   - 复用陪聊 cfg 文件 companion_asr_settings.json 找 fcontent_root / p14_*
#   - intent 从 transcript_path 反推:首条 user 消息取前 50 字作 user_text
#
# 用法:
#   在 ~/.claude/settings.json 加:
#     "hooks": {
#       "Stop": [{"hooks": [{"type": "command", "command": "python C:/.../claude_p14_hook.py"}]}]
#     }
#
# 依赖:
#   - companion_jev.py(同目录)— 提供 eval_stage_outcome
#   - p14_ingest.py(同目录)— 提供 evaluate_and_ingest + load/save_index
#   - companion_asr_settings.json — 提供 fcontent_root / p14_*
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

# 让本脚本可被任何 cwd 调起来:加 companion/ 到 sys.path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 配置 log:写到一个固定文件,Claude Code 不显示,只让用户调试用
_LOG_PATH = Path(os.environ.get("PRISIR_HOOK_LOG",
                                _HERE / "claude_p14_hook.log"))
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("claude_p14_hook")


# ------------------------------------------------------------
# cfg 加载:复用陪聊的 companion_asr_settings.json
# ------------------------------------------------------------
_CFG_PATH = Path(os.environ.get(
    "PRISIR_SETTINGS",
    Path.home() / ".local" / "share" / "prisiragent-companion"
    / "companion_asr_settings.json",
))


def load_cfg() -> dict:
    """从陪聊 settings 加载 p14_*/fcontent_root。失败返空 dict。"""
    try:
        if not _CFG_PATH.exists():
            log.warning("settings file not found: %s", _CFG_PATH)
            return {}
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("load_cfg err: %s", e)
        return {}


# ------------------------------------------------------------
# transcript 解析:拿最近一轮 user_text(用于主题生成)
# ------------------------------------------------------------
def _read_first_user_msg(transcript_path: str) -> str:
    """从 jsonl transcript 读首条 user 消息的前 100 字,作 user_text。
    支持两种 schema:
      - 旧:{"role":"user","content":"..."} 或 {"role":"user","content":[{"type":"text","text":"..."}]}
      - 新:{"type":"user","message":{"role":"user","content":[...]}}
    """
    try:
        p = Path(transcript_path)
        if not p.exists():
            return ""
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 取 role / content(两种 schema 都试)
                role = obj.get("role") or obj.get("type") or \
                    (obj.get("message") or {}).get("role")
                if role != "user":
                    continue
                content = obj.get("content")
                if content is None:
                    content = (obj.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if content:
                    return str(content)[:500]
    except Exception as e:  # noqa: BLE001
        log.warning("read transcript err: %s", e)
    return ""


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
async def _run(payload: dict) -> int:
    """异步执行 P1-4 评估 + 入库。返 exit code (默认 0)。"""
    try:
        last_msg = (payload.get("last_assistant_message") or "").strip()
        if not last_msg:
            log.info("empty last_assistant_message, skip")
            return 0

        cfg = load_cfg()
        if not cfg.get("p14_enabled", True):
            log.info("p14 disabled in cfg, skip")
            return 0

        transcript_path = payload.get("transcript_path", "")
        user_text = _read_first_user_msg(transcript_path) or \
            "[claude_code_no_user_text]"

        # 调共享入口
        from p14_ingest import evaluate_and_ingest  # noqa: E402

        # 短文本过滤:assistant < 60 字直接 skip(琐碎回答不入档)
        if len(last_msg) < 60:
            log.info("assistant_text too short (%d chars), skip",
                     len(last_msg))
            return 0

        # Claude Code 没有 P0-1 intent 标签,默认 "chat"(已实现的最常用类)
        # user_tier="cc" 给 Jev 一点上下文(虽然 Jev 不真用,但留 trace)
        res = await evaluate_and_ingest(
            user_text=user_text,
            assistant_text=last_msg,
            cfg=cfg,
            intent="chat",        # 默认 chat,可手动调 cfg
            user_tier="cc",
        )
        log.info("p14 result: %s",
                 json.dumps(res, ensure_ascii=False)[:200])

        # stdout 给 Claude 看(Stop hook 的 stdout 会作为 context 加给 Claude)
        if res.get("triggered") and res.get("added_count", 0) > 0:
            topic = Path(res.get("path", "")).stem if res.get("path") else "?"
            print(f"[P1-4] 已存档 {res['added_count']} 段 → "
                  f"_incremental/{topic}.md")
        return 0
    except Exception as e:  # noqa: BLE001
        log.warning("hook outer err: %s: %s",
                    type(e).__name__, str(e)[:200])
        return 0


def main() -> int:
    try:
        payload_text = sys.stdin.read()
        if not payload_text.strip():
            log.info("empty stdin, skip")
            return 0
        payload = json.loads(payload_text)
    except Exception as e:  # noqa: BLE001
        log.warning("stdin parse err: %s: %s",
                    type(e).__name__, str(e)[:120])
        return 0
    return asyncio.run(_run(payload))


if __name__ == "__main__":
    raise SystemExit(main())