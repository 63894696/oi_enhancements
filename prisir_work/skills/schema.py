"""
prisIr_work/skills/schema.py — Skills 工作台 schema 定义(Phase 1, 2026-09-27)。

定位:工作台核心数据契约。三层结构:

  SkillIndex      — 极简索引,塞 system prompt,常驻(~80-120 字符/skill)
  SkillDescribe   — 完整 schema,LLM tool_use 时按需加载(几百~几千 字符)
  SkillCall       — 一次调用的中间表示(IR),跟 tool_use / EXEC 协议无关

设计要点:
  · **不重复 capability._REGISTRY** — describe_registry() 直接读 capability 字典转 schema,
    skill 是 capability 的超集视图。capability.py / endpoints.py 0 修改。
  · **IR 抽象** — SkillCall 是协议无关中间表示,Anthropic tool_use / OpenAI function_call /
    老 EXEC 标记各自转译到 IR。Phase 2 用得上。
  · **fail-soft** — 任何坏数据返 None 或 [],绝不抛栈。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 1) SkillIndex — system prompt 里的极简索引
# ---------------------------------------------------------------------------

@dataclass
class SkillIndex:
    """一个 skill 的极简索引项(塞 system prompt)。

    字段顺序稳定,确保 JSON 序列化后字符数可预测。
    """
    id: str
    name: str           # 一句话人话(agent 发现时展示)
    emoji: str = ""     # 视觉锚(🎨/🎬/🆓/👤)
    risk: str = "L0"    # L0 / L1 / L2 / L3
    tags: list[str] = field(default_factory=list)
    backend: str = "builtin"  # "builtin" | "extension" | "tool_use"
    # 频次档:hot(常用)/ warm(常规)/ cold(低频但关键,季度年度用)/ archive(已废弃)
    # Phase 8(2026-09-28):用户决策"长尾低频关键技能不能去重",加 tier 字段分层兜底,
    # 未来可按 tier 分层注入,本阶段不动 system prompt。
    tier: str = "warm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "risk": self.risk,
            "tags": list(self.tags),
            "backend": self.backend,
            "tier": self.tier,
        }

    def approx_chars(self) -> int:
        """估算索引项字符数(给 token 经济性测试用)。"""
        return (
            len(self.id) + len(self.name) + len(self.emoji)
            + len(self.risk) + len(self.backend) + 8  # JSON 格式分隔符
            + sum(len(t) + 2 for t in self.tags)
        )


# ---------------------------------------------------------------------------
# 2) SkillDescribe — 完整 schema(Lazy 加载,只在 LLM tool_use 时取)
# ---------------------------------------------------------------------------

@dataclass
class SkillArg:
    """单个入参的 schema(JSON Schema 风格)。"""
    name: str
    type: str = "string"   # "string" / "number" / "boolean" / "object"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }
        if self.default is not None:
            out["default"] = self.default
        if self.enum:
            out["enum"] = list(self.enum)
        return out


@dataclass
class SkillDescribe:
    """一个 skill 的完整描述(LLM 调 tool_use 时拿到这份再决定参数)。"""
    index: SkillIndex
    title: str = ""           # 跟 name 区别:title 是人话,给 LLM 看
    description: str = ""     # 长描述,LLM tool_use 时看到
    args: list[SkillArg] = field(default_factory=list)
    confirm: str = ""         # L1+ 风险确认文案(L0 为空)
    returns: str = ""         # 返回值说明(JSON 简述)
    examples: list[str] = field(default_factory=list)  # 1-2 个使用示例
    # 后端特定
    endpoint: str | None = None   # 内置 skill:endpoint path
    ext_id: str | None = None     # 扩展 skill:ext_id
    method: str = "POST"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.index.id,
            "name": self.index.name,
            "title": self.title or self.index.name,
            "description": self.description,
            "emoji": self.index.emoji,
            "risk": self.index.risk,
            "backend": self.index.backend,
            "tags": list(self.index.tags),
            "args": [a.to_dict() for a in self.args],
            "confirm": self.confirm,
            "returns": self.returns,
            "examples": list(self.examples),
        }
        if self.endpoint:
            out["endpoint"] = self.endpoint
        if self.ext_id:
            out["ext_id"] = self.ext_id
        out["method"] = self.method
        return out


# ---------------------------------------------------------------------------
# 3) SkillCall — 调用的中间表示(IR)
# ---------------------------------------------------------------------------

@dataclass
class SkillCall:
    """一次 skill 调用的协议无关 IR。

    Phase 2 适配器把 tool_use / function_call 转成这个;
    Phase 4 EXEC 兼容也走这个 IR。
    """
    skill_id: str
    args: dict[str, Any] = field(default_factory=dict)
    risk: str = "L0"
    raw: str = ""         # 原始标记文本(EXEC 兼容路径用)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "args": dict(self.args),
            "risk": self.risk,
            "raw": self.raw,
        }


@dataclass
class SkillResult:
    """skill 调用的结果(协议无关 IR)。"""
    skill_id: str
    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    need_confirm: bool = False  # L1+ 走 confirm 卡
    confirm_msg: str = ""       # 确认文案

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "ok": self.ok,
            "payload": dict(self.payload),
            "error": self.error,
            "need_confirm": self.need_confirm,
            "confirm_msg": self.confirm_msg,
        }


__all__ = [
    "SCHEMA_VERSION",
    "SkillIndex",
    "SkillArg",
    "SkillDescribe",
    "SkillCall",
    "SkillResult",
]