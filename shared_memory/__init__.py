"""OI shared_memory 增强器 — 包装 Peekaboo-W 的 SharedMemory + AgentMemory

替代 oi_enhancements/memory/oi_memory.py (SQLite 自实现)。
Peekaboo-W 的 SharedMemory 设计更先进:JSON 文件 + 跨 agent 元数据统计 + 自然共享。

给 OI agent 提供:
- store(agent, title, content, mem_type, tags) — 跨 agent 共享存储
- retrieve(query, agents, mem_types, limit) — 跨 agent 关键词检索
- get_by_agent(agent, limit) — 某 agent 的所有记忆
- get_stats() — 元数据统计(每个 agent / 每种 type 的 count)
- AgentMemory wrapper — per-agent 视图(自动带 agent 字段)

源码:`vendor/peekaboo/shared_memory.py`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# 让 SharedMemory 可 import
_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "peekaboo"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _try_import():
    try:
        import importlib.util
        # 显式加载 vendor 路径下的 shared_memory.py,避免和本包名冲突
        spec = importlib.util.spec_from_file_location(
            "peekaboo_shared_memory",
            _VENDOR / "shared_memory.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.SharedMemory, mod.AgentMemory
    except (ImportError, AttributeError, FileNotFoundError) as e:
        print(f"[oi_shared_memory] shared_memory 不可用: {e}")
        return None, None


SharedMemory, AgentMemory = _try_import()


# ============================================================
# 单例 — 跨进程共享 ~/.peekaboo/memory/shared/(Peekaboo-W 默认路径)
# ============================================================

_HUB: dict[str, object] = {}


def _get_hub(hub_name: str = "oi_hub"):
    if SharedMemory is None:
        return None
    if hub_name not in _HUB:
        _HUB[hub_name] = SharedMemory(hub_name=hub_name)
    return _HUB[hub_name]


# ============================================================
# 4 层映射到 mem_type(沿用 Peekaboo-W 的 TYPES:article/research/code/idea/fact/note/summary)
# ============================================================
LAYER_TO_MEM_TYPE = {
    "L0": "fact",          # identity 事实
    "L1": "fact",          # essential 事实
    "L2": "note",          # on-demand 短记
    "L3": "summary",       # deep-search 对话快照
}


def store(
    layer: str,
    title: str,
    content: str,
    agent: str = "oi",
    tags: Optional[list[str]] = None,
    source_url: str = "",
    metadata: Optional[dict] = None,
    hub_name: str = "oi_hub",
) -> dict:
    """OI 风格 store:layer → mem_type 自动映射,agent 字段标识来源"""
    if SharedMemory is None:
        return {"status": "unavailable"}
    mem_type = LAYER_TO_MEM_TYPE.get(layer, "note")
    hub = _get_hub(hub_name)
    if hub is None:
        return {"status": "hub_init_fail"}
    try:
        memory_id = hub.store(
            agent=agent, title=title, content=content,
            mem_type=mem_type, tags=tags, source_url=source_url,
            metadata=metadata or {"layer": layer},
        )
        return {"status": "ok", "id": memory_id, "layer": layer, "mem_type": mem_type, "agent": agent}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def retrieve(
    query: str,
    layers: Optional[list[str]] = None,  # 不传 = 全 4 层
    agents: Optional[list[str]] = None,
    limit: int = 10,
    hub_name: str = "oi_hub",
) -> dict:
    """OI 风格 retrieve:按 query 召回,可选限定 layer 或 agent

    策略:不传 mem_types 给底层 retrieve,让它全搜,然后用 metadata.layer 后过滤
    这样 query 能命中所有 type 的全文(避免 L2 note 因为 type='note' 被过滤掉)
    """
    if SharedMemory is None:
        return {"status": "unavailable", "hits": []}
    hub = _get_hub(hub_name)
    if hub is None:
        return {"status": "hub_init_fail", "hits": []}

    try:
        raw = hub.retrieve(query=query, agents=agents, mem_types=None, limit=limit, full_text=True)
        # 后过滤 layer
        if layers:
            layer_set = set(layers)
            raw = [
                r for r in raw
                if r.get("metadata", {}).get("layer") in layer_set
                or r.get("type") in {LAYER_TO_MEM_TYPE.get(l, l) for l in layers}
            ]
        return {"status": "ok", "count": len(raw), "hits": raw}
    except Exception as e:
        return {"status": "error", "reason": str(e), "hits": []}


def get_by_agent(agent: str = "oi", limit: int = 20, hub_name: str = "oi_hub") -> dict:
    if SharedMemory is None:
        return {"status": "unavailable"}
    hub = _get_hub(hub_name)
    if hub is None:
        return {"status": "hub_init_fail"}
    try:
        items = hub.get_by_agent(agent, limit=limit)
        return {"status": "ok", "count": len(items), "items": items}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def get_stats(hub_name: str = "oi_hub") -> dict:
    if SharedMemory is None:
        return {"status": "unavailable"}
    hub = _get_hub(hub_name)
    if hub is None:
        return {"status": "hub_init_fail"}
    try:
        return {"status": "ok", "stats": hub.get_stats()}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ============================================================
# AgentMemory wrapper — 单 agent 视图(简化常用调用)
# ============================================================

def for_agent(agent: str, hub_name: str = "oi_hub"):
    """返回一个 wrapper,自动带 agent 字段"""
    if AgentMemory is None:
        return None
    return AgentMemory(agent_name=agent)


if __name__ == "__main__":
    import json
    print("=== smoke: store + retrieve ===")
    r1 = store("L0", "user:zrkwedii9", "用户偏好中文,直接不啰嗦", tags=["preference"])
    print(f"store L0: {r1}")
    r2 = store("L1", "project:team-web", "C:/Users/Administrator/demos/team-web", tags=["project"])
    print(f"store L1: {r2}")
    r3 = store("L2", "task:panel-bug", "app.js COLUMN_LAYOUT 硬编码导致 panel 丢失", tags=["bug"])
    print(f"store L2: {r3}")

    print("\n=== retrieve '用户偏好' ===")
    print(json.dumps(retrieve("用户偏好"), ensure_ascii=False, indent=2))

    print("\n=== retrieve 'panel bug' ===")
    print(json.dumps(retrieve("panel bug"), ensure_ascii=False, indent=2))

    print("\n=== get_stats ===")
    print(json.dumps(get_stats(), ensure_ascii=False, indent=2))