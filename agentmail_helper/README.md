# AgentMail + Parallel Helper

零依赖(纯 stdlib `urllib`),prisiragent 的邮箱 + 网页搜索基础设施。

## 安装
无需 pip。两个 env var 已在用户环境配好:
- `AGENTMAIL_API_KEY`
- `PARALLEL_API_KEY`

## 用法

### Python API
```python
from helper import AgentMail, Parallel

am = AgentMail()
am.send("x@duck.com", "hi", "body", html="<p>body</p>")
am.send_chinese("x@duck.com", "English subject", "中文内容")  # 自动转图片
am.list_inboxes()
am.list_messages(limit=10)

p = Parallel()
results = p.search("Claude Opus 5 vs Sonnet 5")
```

### CLI
```bash
python helper.py inboxes
python helper.py send "x@duck.com" "subject" "body"
python helper.py send-zh "x@duck.com" "English subj" "中文内容"  # 图片方式发中文
python helper.py render-zh "中文内容"   # 渲染到 test_output.png 看效果
python helper.py search "your query"
```

## 关键坑(踩过了)
1. AgentMail version = **`v0`**(不是 v1)
2. 发件 `to` 字段是 **字符串**`"to":"user@x.com"`,不是数组
3. 中文 `display_name` 乱码——只用英文或留空
4. Parallel 必填字段叫 **`objective`**(不是 `input`)
5. 控制台禁止用 `@agentmail.to` 邮箱注册账号(那是给 agent 用的)

## 与 prisiragent 对接点
- 主 inbox:`prisiragent@agentmail.to`
- MCP server:`agentmail-mcp`(GitHub `agentmail-to/agentmail-mcp`)可装,让 Claude 直接发件
- 订阅工作流:Jack 转发通讯到 `prisiragent@agentmail.to` → 定时拉取 → 分类进 Obsidian