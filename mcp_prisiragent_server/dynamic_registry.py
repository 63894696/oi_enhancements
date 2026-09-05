"""Dynamic MCP tool registry — discovers and registers tool modules.

借鉴 openseek MCP 的 dynamic tools/list 模式：
- 每个工具模块导出 `TOOL_DEFS: list[dict]` 和 `HANDLERS: dict[str, callable]`
- server.py 只需 import 此模块，自动发现所有工具
- 新增工具：创建新 .py 文件 + 导出 TOOL_DEFS/HANDLERS，无需改 server.py

参考 openseek 架构：
- MCP Client `tools/list` → 分页返回所有可用工具
- MCP Client `tools/call` → 按 name + arguments 调用
- 这里我们模拟服务端：server.py 的 `list_tools` 动态组装所有工具
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Callable

import mcp.types as types

# ── 工具模块注册表 ──────────────────────────────────────────────
# 格式: { "module_name": { "defs": [...], "handlers": {...} } }
_REGISTRY: dict[str, dict[str, Any]] = {}


def _extract_tool_defs(module_name: str, module_globals: dict) -> list[dict[str, Any]]:
    """从模块 globals 中提取 TOOL_DEFS 和 HANDLERS。

    约定：
    - TOOL_DEFS: list[dict] — 每个 dict 有 "name", "description", "inputSchema"
    - HANDLERS: dict[str, callable] — name → impl 函数
    """
    defs = module_globals.get("TOOL_DEFS", None)
    handlers = module_globals.get("HANDLERS", None)

    if defs is None or handlers is None:
        return []

    result = []
    for d in defs:
        tname = d["name"]
        if tname not in handlers:
            continue

        handler = handlers[tname]
        sig = inspect.signature(handler) if callable(handler) else {}

        # 构建 MCP Tool
        props = d.get("inputSchema", {}).get("properties", {})
        required = d.get("inputSchema", {}).get("required", [])

        # 从 handler 签名补充参数信息
        if sig and isinstance(sig.parameters, dict):
            for pname, param in sig.parameters.items():
                if pname in ("self",):
                    continue
                if pname not in props:
                    props[pname] = {"type": "string", "description": f"Parameter: {pname}"}
                if pname in required:
                    pass  # already required
                elif param.default is inspect.Parameter.empty:
                    if pname not in required:
                        required.append(pname)

        tool = types.Tool(
            name=tname,
            description=d.get("description", ""),
            inputSchema={
                "type": "object",
                "properties": props,
                "required": required,
            },
        )
        result.append({
            "tool": tool,
            "handler": handler,
        })

    return result


def discover_tool_modules(package_path: str | Path) -> dict[str, dict[str, Any]]:
    """扫描 package_path 下的所有 .py 文件，提取工具定义。

    跳过：
    - __init__.py, __main__.py
    - server.py（避免递归）
    - 以 _ 开头的私有模块
    - 导入失败的模块
    """
    if isinstance(package_path, str):
        package_path = Path(package_path)

    results = {}
    for entry in sorted(package_path.iterdir()):
        if not entry.is_file():
            continue
        if not entry.suffix == ".py":
            continue
        name = entry.stem
        # 跳过特殊文件
        if name in ("__init__", "__main__", "server"):
            continue
        if name.startswith("_"):
            continue
        # Skip non-tool modules (migration scripts, legacy code, etc.)
        if name.startswith("v0.") or name in ("high_level_memory", "mandol_comparison_chart", "embedding_utils"):
            continue

        try:
            # 动态导入模块 — 用 exec 而非 importlib 来避免 globals 重建问题
            module_code = entry.read_text(encoding="utf-8")
            module_globals = {
                "__name__": name,  # Use actual module name to avoid __main__ CLI execution
                "__file__": str(entry),
                "__package__": "",
                "__builtins__": __builtins__,
                # Register in sys.modules for dataclass compatibility
                # (dataclass introspects sys.modules[__name__] at class creation time)
                "sys": sys,
                "json": __import__("json"),
                "os": __import__("os"),
                "Path": __import__("pathlib").Path,
                "importlib": __import__("importlib"),
                "logging": __import__("logging"),
                "time": __import__("time"),
                "glob": __import__("glob"),
                "re": __import__("re"),
                "typing": __import__("typing"),
                "enum": __import__("enum"),
                "dataclasses": __import__("dataclasses"),
                "collections": __import__("collections"),
                "asyncio": __import__("asyncio"),
                "traceback": __import__("traceback"),
                "mcp": __import__("mcp"),
                "types": __import__("mcp.types"),
                "hashlib": __import__("hashlib"),
                "difflib": __import__("difflib"),
                "networkx": __import__("networkx"),
            }
            # Register in sys.modules for dataclass compatibility
            sys.modules[name] = type('DynamicModule', (), module_globals)()
            exec(compile(module_code, str(entry), "exec"), module_globals)

            # Extract TOOL_DEFS and HANDLERS from globals
            defs = module_globals.get("TOOL_DEFS", None)
            handlers = module_globals.get("HANDLERS", None)
            if defs is None or handlers is None:
                continue

            extracted = _extract_tool_defs(name, module_globals)
            if extracted:
                results[name] = {
                    "defs": extracted,
                    "module": type('DynamicModule', (), module_globals)(),
                }
        except Exception as e:
            # 导入失败不影响其他模块
            print(f"[dynamic_registry] Failed to load {name}: {e}", flush=True)

    return results


def register_all(package_path: str | Path = None) -> list[types.Tool]:
    """注册所有工具模块，返回 types.Tool 列表。

    如果 package_path 为 None，自动从本文件所在目录扫描。
    """
    if package_path is None:
        package_path = Path(__file__).parent

    global _REGISTRY
    _REGISTRY = discover_tool_modules(package_path)

    tools = []
    for mod_name, mod_data in _REGISTRY.items():
        for item in mod_data["defs"]:
            tools.append(item["tool"])

    return tools


def get_handler(tool_name: str) -> Callable | None:
    """根据工具名查找对应的 handler 函数。"""
    for mod_name, mod_data in _REGISTRY.items():
        for item in mod_data["defs"]:
            if item["tool"].name == tool_name:
                return item["handler"]
    return None


def list_registered_tools() -> list[str]:
    """列出所有已注册的工具名。"""
    names = []
    for mod_name, mod_data in _REGISTRY.items():
        for item in mod_data["defs"]:
            names.append(item["tool"].name)
    return names


def get_registry_summary() -> dict[str, Any]:
    """返回注册表摘要（用于 health check）。"""
    return {
        "modules": list(_REGISTRY.keys()),
        "tool_count": sum(len(md["defs"]) for md in _REGISTRY.values()),
        "tools": list_registered_tools(),
    }
