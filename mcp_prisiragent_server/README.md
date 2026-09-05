# OIagent MCP Server v0.1

> 走方向 2 决策做的最小 MCP 暴露层。不重写 OI,不重写 cursor-harness,只把它们当成可调用的 MCP 工具。

## 一句话

**MCP server 把 cursor-harness adapter 暴露成 4 个 stdio 工具,让 Claude Code / Cursor / 任何 MCP 客户端能跨进程调 OI 做审查/调研。**

## 暴露的 4 个 tools

| 名称 | 用途 | 驱动模型 |
|------|------|---------|
| `run_oi_review` | qwen-max + DashScope 跑 harness | 通过 `cursor_harness_adapter.get_harness_for_oi()` |
| `run_claude_review` | Opus 4.8 + Anthropic 跑 harness | 通过 `cursor_harness_adapter.get_harness_for_claude()` |
| `list_models` | 列出 2 个 harness | 静态 JSON |
| `health` | 健康检查(env key + cwd + adapter 路径) | 实时 JSON |

## 启动方式(Claude Code 配 .mcp.json)

```json
{
  "mcpServers": {
    "prisiragent": {
      "command": "python",
      "args": ["-m", "mcp_prisiragent_server"],
      "cwd": "C:/Users/Administrator/oi_enhancements",
      "env": {
        "BAILIAN_API_KEY": "${env:BAILIAN_API_KEY}",
        "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

## 已知坑

1. **长任务同步阻塞** — CursorHarness.run() 是同步阻塞,n 分钟级 task 会把 MCP client 卡死。v0.1 不做分段返回/stream。
2. **cwd 必须存在** — 沙箱目录不在 `cursor_harness_adapter.PathSandbox.allowed_dirs` 会抛错。改 adapter 加目录不算本模块工作。
3. **默认 cwd = D:/cursor-agent-cli/** — 跟 reviews/ 报告目录对齐。如果审查 voice_input_ghostline,需显式传 `cwd: "C:/Users/Administrator/voice_input_ghostline"`。

## 版本

- v0.1 (2026-07-04) — 创建,4 tools 暴露,用户拍板"双 harness 同时暴露"
