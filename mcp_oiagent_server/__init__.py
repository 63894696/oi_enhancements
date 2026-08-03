"""mcp_oiagent_server — OI agent 的 MCP 暴露层

走方向 2 决策:不做 OI 重写,只把 cursor-harness 当成 MCP 工具调用入口。

2026-07-04 v0.1 创建
- 暴露 4 个 tool:run_oi_review / run_claude_review / list_models / health
- 默认 stdio transport(Claude Code MCP 标准入站)
- 双 harness:qwen-max(DashScope)+ Opus 4.8(Anthropic)
- cwd 默认 D:/cursor-agent-cli/(reviews/ 报告目录)
- 不重写 OI agent 主进程,不重写 agent_shell GUI,不重写 harness 本身

走"只 MCP 身份验证 + 不动功能"姿势(用户 2026-07-04 拍板,走方向 2 决策)
"""
