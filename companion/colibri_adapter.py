# -*- coding: utf-8 -*-
# companion/colibri_adapter.py — 把 colibri 引擎注册为"伪平台" 注入 LLM 候选序(2026-09-28 ship Phase A)
#
# 定位:
#   - colibri 引擎不需要 API key,但要让 PrisirRouter 走标准 _stream_openai_compat 路径
#   - 设计选择:**不修改 fastlane.providers.llm_prisir**(跨包改风险大,易引发回归)
#   - 在 companion_llm.stream_chat 里 hook:failover_candidates 之后,把 colibri 候选注入
#   - colibri 候选形态:{platform: "local_colibri", cfg: {...}, task_type: ...}
#     cfg 字段对齐 _stream_openai_compat 的入参:base_url / model / api_key / meta
#
# 优先级策略:
#   - 零 key(用户没配任何云端)→ colibri 排第 1
#   - 有 key → colibri 排最后兜底
#   - 用户在 settings 里设了 `colibri_first=True` → 永远排第 1
#
# 何时返 None:
#   - 模型未下(not_downloaded)→ 返 None(上层不注入,避免引导前误用)
#   - 引擎不在 ready → 返 None
#   - binary 找不到 → 返 None
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import colibri_engine as _engine_mod
from . import colibri_state as _state_mod

log = logging.getLogger("prisiragent-companion.colibri.adapter")

# 平台 id:跟 ollama/llama 平级,显式标识"本地"
PLATFORM_ID = "local_colibri"

# 模型名:跟 HF repo 名对齐,便于排错
MODEL_NAME = "olmoe"


def colibri_cfg() -> Optional[Dict[str, Any]]:
    """返一个 colibri 候选 cfg(对齐 _stream_openai_compat 入参),不可用返 None。

    字段:
      base_url: http://127.0.0.1:{port}/v1
      api_key:   "no-auth-needed"(占位,真实请求不会带)
      model:     "olmoe"
      meta:      {proto: "openai", source: "colibri", local: True}
      _colibri_port / _colibri_pid: 给上层健康检查用
    """
    s = _state_mod.load_state()
    if s.state != "ready":
        return None
    # 引擎真活?
    if not s.pid or not _engine_mod._pid_alive(s.pid):
        return None
    return {
        "platform": PLATFORM_ID,
        "cfg": {
            "base_url": f"http://127.0.0.1:{s.port}/v1",
            "api_key": "no-auth-needed",   # colibri 不校验,但 _stream_openai_compat 需要 Bearer
            "model": MODEL_NAME,
            "meta": {"proto": "openai", "source": "colibri", "local": True},
            # 私有 — 仅本仓使用,不进 PrisirKeyStore
            "_colibri_port": s.port,
            "_colibri_pid": s.pid,
        },
        "task_type": "general",  # 占位;上层 failover_candidates 会用真实 task_type 覆盖
    }


def is_colibri_ready() -> bool:
    """轻量检查:colibri 引擎当前是否 ready(给 /api/creds/status 快速判断用)。"""
    cfg = colibri_cfg()
    return cfg is not None


def inject_into_candidates(
    candidates: List[Dict[str, Any]],
    *,
    colibri_first: bool = False,
    task_type: str = "general",
) -> List[Dict[str, Any]]:
    """把 colibri 候选插入 candidates 列表。

    - colibri_first=True 或 候选为空(零 key)→ 排第 1
    - 否则 → 排最后兜底

    返回新列表(不修改原列表)。
    """
    cand = colibri_cfg()
    if cand is None:
        return candidates
    # task_type 用入参对齐,避免上层再覆盖
    cand["task_type"] = task_type
    new_list = list(candidates)
    if colibri_first or not new_list:
        # 头部插入
        new_list.insert(0, cand)
        log.info("注入 colibri 候选到头部(colibri_first=%s,empty=%s)",
                 colibri_first, not candidates)
    else:
        # 兜底到末尾
        new_list.append(cand)
        log.info("注入 colibri 候选到末尾(兜底)")
    return new_list


__all__ = [
    "PLATFORM_ID",
    "MODEL_NAME",
    "colibri_cfg",
    "is_colibri_ready",
    "inject_into_candidates",
]