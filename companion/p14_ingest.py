# -*- coding: utf-8 -*-
# p14_ingest.py — M3.45 P1-4 阶段成果增量入库核心(2026-09-22)
#
# 目的:
#   - 把 web.py 里 _p14_* 系列函数抽成独立模块,让 Claude Code Stop hook 也能复用
#   - 陪聊入口 + Claude Code 窗口共享 _p14_index.json + sha256 去重 + 段切 + 写盘逻辑
#   - 零外部依赖:只 json / pathlib / hashlib / re / datetime
#   - 异步入口 evaluate_and_ingest(),同步 helper 5 个
#
# 设计:
#   - cfg dict 走 settings(companion_asr_settings.json 的 p14_* / fcontent_root 段)
#   - 失败 fail-open:所有异常被吞,返 empty dict 不阻塞调用方
#   - 段级 sha256 去重:同段不重复入库,跨入口(陪聊+CC)共享索引
#   - 主题策略:auto(取 user_text 前 30 字) / manual(intent+ts) / off(ts)
#   - 同一主题追加同文件,Obsidian 端无需合并
#
# 用法:
#   from p14_ingest import evaluate_and_ingest, load_index, stats
#   res = await evaluate_and_ingest(user_text, assistant_text, cfg, intent="chat")
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("p14_ingest")

# 默认 cfg 字段(可被调用方 cfg 覆盖)
DEFAULTS = {
    "p14_enabled": True,
    "p14_min_value": 2,
    "p14_min_has_prob": 0.5,
    "p14_timeout_sec": 1.2,
    "p14_dir_name": "_incremental",
    "p14_topic_strategy": "auto",
    "fcontent_root": "",
}


# ------------------------------------------------------------
# 同步 helper(无网络,无 async)— 也给非异步场景用
# ------------------------------------------------------------
def safe_filename(text: str, max_len: int = 30) -> str:
    """生成可作文件名的简短主题(中文+字母数字,去特殊字符)。"""
    s = re.sub(r"[^\w一-鿿]+", "_", (text or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:max_len] or "untitled")


def index_path(root: str) -> Path:
    return Path(root) / "_p14_index.json"


def load_index(root: str) -> dict:
    """读 {hash: relative_path} 索引。失败返空 dict。"""
    try:
        p = index_path(root)
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_index(root: str, idx: dict) -> bool:
    """落盘索引,失败返 False。"""
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
        index_path(root).write_text(
            json.dumps(idx, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[p14] save index err: %s", e)
        return False


def extract_segments(user_text: str, assistant_text: str) -> list[str]:
    """从一轮对话抽「可入档的段」。
    - 按中文标点 / 换行切 assistant_text
    - 每段 ≥ 8 字;太短丢弃(琐碎)
    - 最多 5 段(避免一次写太多)
    """
    raw = (assistant_text or "").strip()
    if not raw:
        return []
    parts = re.split(r"[。！？!?;\n]+", raw)
    segs = []
    for p in parts:
        p = p.strip()
        if len(p) >= 8:
            segs.append(p)
        if len(segs) >= 5:
            break
    return segs


def derive_topic(user_text: str, intent: str, strategy: str) -> str:
    """按 cfg 策略生成主题文件名(不含扩展名)。"""
    if strategy == "off":
        return datetime.now().strftime("incr_%Y%m%d_%H%M%S")
    if strategy == "manual":
        ts = datetime.now().strftime("%H%M%S")
        return f"{intent}_{ts}"
    head = (user_text or "").strip()[:20]
    return safe_filename(head, max_len=24) or "incr"


def _strip_for_eval(text: str) -> str:
    """去 Claude Code assistant text 里的 tool_call / 代码块。
    只把 <function_calls>...</function_calls> 和 ``` 代码块剥掉,保留自然语言结论。
    """
    if not text:
        return ""
    # 剥 <function_calls>...</function_calls> 块(可能多段)
    text = re.sub(r"<function_calls>.*?</function_calls>", "", text, flags=re.DOTALL)
    # 剥 ```...``` 围栏
    text = re.sub(r"```[\s\S]*?```", "", text)
    # 剥单行 tool_call 残留(以<开头到行尾)
    text = re.sub(r"<\w+>.*", "", text)
    return text.strip()


# ------------------------------------------------------------
# 异步主入口 — 评估 + 入库
# ------------------------------------------------------------
async def evaluate_and_ingest(
    user_text: str,
    assistant_text: str,
    cfg: dict,
    *,
    intent: str = "unknown",
    history_len: int = 0,
    user_tier: str = "free",
    eval_fn=None,
) -> dict:
    """P1-4 核心:评估 + 入库。

    Args:
        user_text: 用户消息
        assistant_text: LLM 回答(会被自动 strip tool_call/代码块)
        cfg: settings 字段(必须含 fcontent_root + p14_*)
        intent: P0-1 给出的意图(用于 manual 主题策略 + 过滤)
        history_len: 历史条数(可给 Jev 作上下文)
        user_tier: 用户层级
        eval_fn: 默认从 companion_jev.eval_stage_outcome 取,可注入(测试用)

    Returns:
        {
          "triggered": bool,
          "value": "none"|"trivial"|"archivable"|"critical",
          "value_index": 0-3,
          "has_prob": 0-1,
          "added_count": int,
          "skipped_count": int,
          "path": str|None,
          "fallback_used": bool,
          "reason": str,           # "disabled"|"no_fcontent_root"|"no_outcome"|
                                   #   "value_too_low"|"no_segments"|"ok"|"all_duplicates"|
                                   #   "eval_timeout"|"eval_err"|"fail_soft"|"mkdir_fail"|
                                   #   "write_fail"|"intent_skip"
        }
    """
    # cfg 合并默认值
    full_cfg = {**DEFAULTS, **cfg}

    empty = {"triggered": False, "value": "none", "value_index": 0,
             "has_prob": 0.0, "added_count": 0, "skipped_count": 0,
             "path": None, "fallback_used": True, "reason": "skipped"}

    try:
        if not full_cfg.get("p14_enabled", True):
            return {**empty, "reason": "disabled"}

        # intent 过滤:tool_call / roleplay / unknown 跳过
        if intent in ("tool_call", "roleplay", "unknown", ""):
            return {**empty, "reason": "intent_skip"}

        root = (full_cfg.get("fcontent_root") or "").strip()
        if not root:
            return {**empty, "reason": "no_fcontent_root"}

        # Claude Code hook 路径:strip tool_call / 代码块
        eval_text = _strip_for_eval(assistant_text)
        if not user_text or not eval_text:
            return {**empty, "reason": "empty_text"}

        # 评估
        if eval_fn is None:
            from companion_jev import eval_stage_outcome as eval_fn
        timeout_s = float(full_cfg.get("p14_timeout_sec", 1.2))
        try:
            eval_res = await asyncio.wait_for(
                eval_fn(user_text, eval_text,
                        history_len=history_len,
                        user_tier=user_tier,
                        timeout_s=timeout_s),
                # try_jev 内部 primary + fallback 串行,总预算≈timeout_s*2
                timeout=timeout_s * 2 + 0.5,
            )
        except asyncio.TimeoutError:
            return {**empty, "reason": "eval_timeout"}
        except Exception as e:  # noqa: BLE001
            log.warning("[p14] eval err: %s: %s",
                        type(e).__name__, str(e)[:120])
            return {**empty, "reason": "eval_err"}

        if not eval_res:
            return {**empty, "reason": "fail_soft"}

        has_prob = float(eval_res.get("has_probability", 0.0))
        min_has = float(full_cfg.get("p14_min_has_prob", 0.5))
        if has_prob < min_has:
            return {**empty, "has_prob": has_prob,
                    "value": eval_res.get("value_score", "none"),
                    "value_index": eval_res.get("value_index", 0),
                    "reason": "no_outcome"}

        val_idx = int(eval_res.get("value_index", 0))
        min_val = int(full_cfg.get("p14_min_value", 2))
        if val_idx < min_val:
            return {**empty, "has_prob": has_prob,
                    "value": eval_res.get("value_score", "none"),
                    "value_index": val_idx,
                    "reason": "value_too_low"}

        # 触发入库
        segs = extract_segments(user_text, eval_text)
        if not segs:
            return {**empty, "has_prob": has_prob,
                    "value": eval_res.get("value_score"),
                    "value_index": val_idx,
                    "reason": "no_segments"}

        topic = derive_topic(user_text, intent,
                             full_cfg.get("p14_topic_strategy", "auto"))
        idx = load_index(root)
        existing_hashes = set(idx.keys())
        added: list[tuple[str, str]] = []
        skipped = 0
        for seg in segs:
            h = hashlib.sha256(seg.encode("utf-8")).hexdigest()[:16]
            if h in existing_hashes:
                skipped += 1
                continue
            added.append((h, seg))
            existing_hashes.add(h)
        if not added:
            return {"triggered": True,
                    "value": eval_res["value_score"],
                    "value_index": val_idx, "has_prob": has_prob,
                    "added_count": 0, "skipped_count": skipped,
                    "path": None, "fallback_used": False,
                    "reason": "all_duplicates"}

        # 写盘
        dir_name = full_cfg.get("p14_dir_name", "_incremental")
        incr_dir = Path(root) / dir_name
        try:
            incr_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("[p14] mkdir err: %s", e)
            return {**empty, "has_prob": has_prob, "reason": "mkdir_fail"}

        topic_path = incr_dir / f"{topic}.md"
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            existing_md = ""
            if topic_path.exists():
                existing_md = topic_path.read_text(encoding="utf-8")
            header = ""
            if not existing_md:
                header = (f"# 增量入库 — {topic}\n\n"
                          f"_主题生成策略:{full_cfg.get('p14_topic_strategy', 'auto')} "
                          f"/ intent={intent}_\n\n")
            block_lines = [header]
            for h, seg in added:
                block_lines.append(f"## {ts_str} ({h})")
                block_lines.append("")
                block_lines.append(f"> user: {user_text[:120]}")
                block_lines.append("")
                block_lines.append(seg)
                block_lines.append("")
            topic_path.write_text(
                existing_md + "\n".join(block_lines),
                encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("[p14] write md err: %s", e)
            return {**empty, "has_prob": has_prob, "reason": "write_fail"}

        # 更新索引
        for h, _ in added:
            try:
                rel = str(topic_path.relative_to(Path(root)))
            except ValueError:
                rel = str(topic_path)
            idx[h] = rel
        save_index(root, idx)

        return {
            "triggered": True,
            "value": eval_res["value_score"],
            "value_index": val_idx,
            "has_prob": has_prob,
            "added_count": len(added),
            "skipped_count": skipped,
            "path": str(topic_path),
            "fallback_used": False,
            "reason": "ok",
        }
    except Exception as e:  # noqa: BLE001
        log.warning("[p14] outer err: %s: %s",
                    type(e).__name__, str(e)[:120])
        return empty


def stats(root: str, dir_name: str = "_incremental") -> dict:
    """返当前 fcontent_root 的入库统计(段数 + 文件数 + 路径)。"""
    if not root:
        return {"n_segments": 0, "n_files": 0, "root": "", "dir_name": dir_name}
    idx = load_index(root)
    return {"n_segments": len(idx),
            "n_files": len(set(idx.values())),
            "root": root,
            "dir_name": dir_name}