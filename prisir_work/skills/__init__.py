"""
prisIr_work/skills/ — PrisirAI Skills 工作台(Phase 1, 2026-09-27)。

定位:把所有 capability / extension skill / 第三方 tool_use 统一成「工作台」。
     agent 默认极简,按需 describe / execute。

核心入口:
  describe_registry()    — JSON 索引(塞 system prompt)
  describe_skill(id)     — 完整 schema(lazy 加载)
  execute_skill(id, args) — 执行(L1+ 走 confirm 闸门)

Phase 1 ship 边界:
  · builtin skill(读 capability._REGISTRY 路由到 endpoints handler)— 全功能
  · extension skill(_ext_rpc_call 子进程桥)— 全功能
  · tool_use backend — Phase 2 stub,返 not_implemented

不破坏:
  · prisIr_work/capability.py(0 修改)
  · prisIr_work/endpoints.py(0 修改)
  · prisIr_work/agent_main_chat_hook.py(EXEC 标记继续工作)
  · companion/prisIragent-companion-web.py:build_messages(老 5 处 intent_summary 继续工作)
"""
from .loader import (
    describe_registry,
    describe_registry_compact,
    describe_skill,
    execute_skill,
    list_skills,
    search_skills,
    count_skills,
)
from .schema import (
    SCHEMA_VERSION,
    SkillIndex,
    SkillArg,
    SkillDescribe,
    SkillCall,
    SkillResult,
)

__all__ = [
    # 顶层 API
    "describe_registry",
    "describe_registry_compact",
    "describe_skill",
    "execute_skill",
    "list_skills",
    "search_skills",
    "count_skills",
    # schema
    "SCHEMA_VERSION",
    "SkillIndex",
    "SkillArg",
    "SkillDescribe",
    "SkillCall",
    "SkillResult",
]