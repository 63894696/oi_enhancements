"""OI Agent Shell — 三理念统一交互层(S1–S3)

启动:
  python -m agent_shell

配置:
  ~/.oi_agent/shell.yaml

功能:
- 浮动球:可拖动、状态色 + 脉冲动画、单击详情 / 双击 presence / 右键打断
- 系统托盘:切换 base / fastlane / ghostline
- PTT → WebSocket 流式 ASR
- 语音 → DynamicRouter 管线(S3)
- OI 钩子: agent_shell.hooks.AgentStateHooks

不含 IM 集成(匿名 IM 为独立项目)。
"""
from .app import AgentShellApp
from .config import ensure_config, load_config, set_active_profile
from .hooks import AgentStateHooks

__all__ = [
    "AgentShellApp",
    "AgentStateHooks",
    "ensure_config",
    "load_config",
    "set_active_profile",
]
