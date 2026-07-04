"""oi_enhancements → cursor-harness 整合层

2026-07-04 v0.0.1

把 D:/cursor-harness/ 的 CursorHarness 模块装到 oi_enhancements,
让 OI agent 启动时能 import 并挂到 system_message。

跑法(给 OI agent):
    from cursor_harness_adapter import get_harness_for_oi
    harness = get_harness_for_oi()
    result = harness.run("修 H-1 DNS 拦截 bug", cwd="C:/Users/Administrator/voice_input_ghostline")

依赖:
- D:/cursor-harness/ 在 PYTHONPATH 或 pip install -e D:/cursor-harness/
- BAILIAN_API_KEY(DashScope qwen-max)在 env
"""
__version__ = "0.0.1"

from .adapter import get_harness_for_oi, get_harness_for_claude

__all__ = ["get_harness_for_oi", "get_harness_for_claude"]
