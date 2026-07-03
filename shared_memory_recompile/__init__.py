"""Letta 风格 memory block + recompile — OI shared_memory 升级

参考 github.com/letta-ai/letta v0.16.7:
  POST /v1/conversations/{id}/recompile
  改完 memory block 后主动重生成 system_prompt

OI shared_memory 当前是一次性 store/recall,改完 memory 后不会触发"重新渲染 system prompt"。
这个增强器加 MemoryBlock.recompile_prompt(),把 memory blocks 当成可模板替换的占位符。

用法:
    from oi_enhancements.shared_memory_recompile import (
        MemoryBlock, build_block, recompile_system_prompt
    )
    mb = MemoryBlock(name='persona', template='你是 {{block_persona}}, 在 {{block_env}} 工作')
    mb.edit('persona', 'OI agent')
    mb.edit('env', 'Windows 11')
    system_prompt = mb.recompile_prompt()
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

# 让 oi_enhancements/shared_memory 包可 import
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# 用 importlib 加载 oi_enhancements/shared_memory(避免 vendor 同名冲突)
def _load_oi_sm():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "oi_enh_shared_memory",
        _HERE / "shared_memory" / "__init__.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sm = _load_oi_sm()


# ============================================================
# MemoryBlock — Letta 风格的 block 容器
# ============================================================

class MemoryBlock:
    """一个可编辑、可 recompile 的 memory block

    Attributes:
        name: block 名(对应 system prompt 里的占位符前缀 {{block_<name>}})
        template: 含 {{block_*}} 占位符的字符串(用户友好的写法,内部转 format)
        store: key → value 字典(用户编辑的值)
    """

    def __init__(self, name: str, template: str):
        self.name = name
        self.template = template
        self.store: dict[str, str] = {}
        self.dirty: set[str] = set()  # 记录哪些 key 被改过(可触发 recompile)

    def edit(self, key: str, value: str) -> "MemoryBlock":
        """改一个 block 的值,标记 dirty"""
        self.store[key] = value
        self.dirty.add(key)
        return self

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.store.get(key, default)

    # {{block_xxx}} 占位符;xxx 是 store 的 key
    _PLACEHOLDER_RE = re.compile(r"\{\{\s*block_([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

    def recompile_prompt(self, extra_blocks: Optional[dict[str, str]] = None) -> str:
        """重渲染 template:用 store + extra_blocks 替换 {{block_*}} 占位符

        2026-07-03 重写:不再走 str.format(它要求转义所有字面 { }),
        改为一次 regex 替换直接渲染 —— 模板里的 JSON/代码示例等字面
        大括号天然安全。

        Returns:
            替换后的完整 system prompt 字符串

        Raises:
            KeyError: template 里有占位符但没提供值
        """
        # 合并:store 优先,extra_blocks 补缺
        merged = dict(extra_blocks or {})
        for k, v in self.store.items():
            merged[k] = v

        def _sub(m: "re.Match[str]") -> str:
            key = m.group(1)
            if key not in merged:
                raise KeyError(f"MemoryBlock '{self.name}' missing value for '{key}'")
            return str(merged[key])

        return self._PLACEHOLDER_RE.sub(_sub, self.template)

    def is_clean(self) -> bool:
        """没有未 recompile 的改动"""
        return len(self.dirty) == 0

    def mark_clean(self) -> None:
        """recompile 后清 dirty 标记"""
        self.dirty.clear()


# ============================================================
# OI 专用 block 工厂
# ============================================================

PERSONA_TEMPLATE = """你是 {{block_persona}},用户的 AI 编程助手。

# 当前环境
- 操作系统:{{block_os}}
- 项目根:{{block_project_root}}
- Python:{{block_python_version}}
- 工作模式:Windows GUI 自动化 + 代码执行

# 关键事实
{{block_facts}}

# 历史偏好
{{block_preferences}}

# 当前任务上下文
{{block_task}}
""".strip()

# 模板里的真实换行符也要保留(让 recompile 后仍可读)
# 实际 format 不会影响换行,只是占位符替换


def build_default_block(
    persona: str = "OI agent",
    os: str = "Windows 11",
    project_root: str = "",
    python_version: str = "",
    facts: str = "(暂无)",
    preferences: str = "(暂无)",
    task: str = "(等待用户输入)",
) -> MemoryBlock:
    """构造 OI agent 默认的 system prompt memory block"""
    mb = MemoryBlock(name="oi_persona", template=PERSONA_TEMPLATE)
    mb.edit("persona", persona)
    mb.edit("os", os)
    mb.edit("project_root", project_root)
    mb.edit("python_version", python_version)
    mb.edit("facts", facts)
    mb.edit("preferences", preferences)
    mb.edit("task", task)
    return mb


# ============================================================
# shared_memory 集成:从 recall 结果更新 block,再 recompile
# ============================================================

def recall_into_block(
    block: MemoryBlock,
    query: str,
    layers: Optional[list[str]] = None,
    limit: int = 5,
    hub_name: str = "oi_hub",
) -> MemoryBlock:
    """从 shared_memory 检索相关记忆,合并到 block 的 facts / preferences / task

    Returns:
        更新后的 MemoryBlock(支持链式调用)
    """
    r = _sm.retrieve(query=query, layers=layers, hub_name=hub_name)
    hits = r.get("hits", [])
    if not hits:
        return block

    # 分类:根据 layer 分到对应 field
    facts = []
    prefs = []
    tasks = []
    for h in hits:
        layer = h.get("metadata", {}).get("layer", h.get("type", ""))
        title = h.get("title", "")
        content = h.get("content", "")[:200]
        line = f"- [{title}] {content}"
        if layer == "L0":
            prefs.append(line)
        elif layer == "L1":
            facts.append(line)
        elif layer in ("L2", "L3"):
            tasks.append(line)
        else:
            facts.append(line)

    # 追加(不覆盖)
    existing = block.get("facts", "")
    if facts:
        new_facts = (existing + "\n" if existing and existing != "(暂无)" else "") + "\n".join(facts)
        block.edit("facts", new_facts.strip())

    existing = block.get("preferences", "")
    if prefs:
        new_prefs = (existing + "\n" if existing and existing != "(暂无)" else "") + "\n".join(prefs)
        block.edit("preferences", new_prefs.strip())

    existing = block.get("task", "")
    if tasks:
        new_tasks = (existing + "\n" if existing and existing != "(等待用户输入)" else "") + "\n".join(tasks)
        block.edit("task", new_tasks.strip())

    return block


def recompile_system_prompt(block: MemoryBlock, query: str = "") -> str:
    """一站式:recall → 更新 block → recompile system prompt

    用法:oi agent 启动时调一次,拿到完整 system prompt
    """
    if query:
        recall_into_block(block, query=query)
    return block.recompile_prompt()


# ============================================================
# Demo / 测试
# ============================================================

if __name__ == "__main__":
    import json

    print("=== MemoryBlock 基础 ===")
    mb = MemoryBlock(
        name="test",
        template="Hi I'm {{block_name}}, I work on {{block_project}}"
    )
    mb.edit("name", "OI")
    mb.edit("project", "team-web")
    print(f"  render: {mb.recompile_prompt()}")
    print(f"  is_clean (no recompile yet): {mb.is_clean()}")
    mb.mark_clean()
    print(f"  is_clean after mark_clean: {mb.is_clean()}")
    mb.edit("name", "OI-v2")
    print(f"  re-edit → dirty: {not mb.is_clean()}")

    print()
    print("=== build_default_block ===")
    import platform
    block = build_default_block(
        persona="OI 编程助手",
        os=f"{platform.system()} {platform.release()}",
        project_root="C:/Users/Administrator/demos/team-web",
        python_version=platform.python_version(),
        facts="- [project:team-web] FastAPI 多 agent 协作面板\n- [framework] Peekaboo-W 复用",
        preferences="- 用户偏好中文\n- 笔记习惯用 Obsidian",
        task="- 当前在调研 OI agent 评测",
    )
    print(block.recompile_prompt()[:600])

    print()
    print("=== 从 shared_memory 召回 + recompile ===")
    # 先存一些记忆
    _sm.store("L1", "project:team-web", "FastAPI 多 agent 协作面板", tags=["project"], hub_name="oi_test")
    _sm.store("L0", "user:zrkwedii9", "用户偏好中文,直接不啰嗦", tags=["preference"], hub_name="oi_test")
    _sm.store("L2", "task:panel-bug", "切 team 时 panel 丢失", tags=["bug"], hub_name="oi_test")

    block = build_default_block()
    prompt = recompile_system_prompt(block, query="team-web panel bug")
    print(prompt[:600])