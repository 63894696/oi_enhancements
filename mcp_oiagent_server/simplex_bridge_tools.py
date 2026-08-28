"""simplex_bridge_tools.py — SimpleX 工具面 → dynamic_registry 桥接层

目的
----
`oi_enhancements/simplex_tools.py` 已实现完整的 SimpleX 工具逻辑
(simplex_create_invitation / simplex_accept_invitation / simplex_send_message 等),
并导出 `_TOOL_IMPLS` / `get_tools()` / `call_tool()`(OpenAI 风格 schema)。

但 `mcp_oiagent_server/dynamic_registry.py` 期望的是 `TOOL_DEFS` + `HANDLERS`
平铺约定(`{name, description, inputSchema}` + `name -> handler`)。

本模块是**纯适配层**:不改 simplex_tools 本体,把它的 OpenAI schema 剥一层、
handler 经 call_tool 分发,转成 dynamic_registry 能发现的形状。

执行环境
--------
本文件被 dynamic_registry 以 **exec 沙箱**方式导入(注入的 globals 有限:
含 sys/Path/json 等,但不含本目录之外的模块)。因此顶部必须先把
oi_enhancements 根目录注入 sys.path,才能 `import simplex_tools`(它会再
`import simplex_runtime` / `policy_engine`)。注入失败 → dynamic_registry 静默
跳过本模块(只 print 不抛),工具丢失。所以本模块的导入健壮性是契约的一部分。
"""

# ── sys.path 注入(照 high_level_memory.py:31 模式)────────────────── #
# 注意:本模块在 exec 沙箱内运行,__file__ 由 dynamic_registry 注入为
# ".../mcp_oiagent_server/simplex_bridge_tools.py",故 parent.parent 即
# oi_enhancements 根目录。
import sys  # noqa: E402  (dynamic_registry 沙箱已注入 sys)
from pathlib import Path  # noqa: E402  (沙箱已注入 Path)

_MCP_SERVER_DIR = Path(__file__).resolve().parent
_OI_ENHANCEMENTS = _MCP_SERVER_DIR.parent
if str(_OI_ENHANCEMENTS) not in sys.path:
    sys.path.insert(0, str(_OI_ENHANCEMENTS))

# ── 导入源工具面 ─────────────────────────────────────────────────── #
from simplex_tools import call_tool, get_tools, TOOL_NAMES  # noqa: E402


# ── 桥接:OpenAI schema → dynamic_registry 平铺格式 ───────────────── #
def _build_tool_defs() -> list[dict]:
    """把 get_tools() 的 OpenAI function-calling schema 剥一层取 function 字段。

    OpenAI:   {"type": "function", "function": {name, description, parameters}}
    registry: {"name", "description", "inputSchema"}  (inputSchema ← parameters)
    """
    defs = []
    for entry in get_tools():
        fn = entry["function"]
        defs.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return defs


def _make_handler(tool_name: str):
    """闭包工厂:默认参数绑定 tool_name,避免晚绑定捕获同一个循环变量。"""
    def _handler(**kwargs):
        return call_tool(tool_name, kwargs)
    _handler.__name__ = tool_name
    _handler.__doc__ = f"桥接 handler → simplex_tools.call_tool('{tool_name}', ...)"
    return _handler


# ── dynamic_registry 约定的导出面 ────────────────────────────────── #
TOOL_DEFS = _build_tool_defs()
HANDLERS = {name: _make_handler(name) for name in TOOL_NAMES}
