# PrisirAI 主对话接视频能力 — P3j T16-A 文档

2026-09-26 ship。**T16-A 最小接入** — 让主对话 LLM 能在回复里写 `[[EXEC: ...]]` 标记,后处理扫到后走对应能力(L0 直接执行,L1+ 推确认请求给前端弹卡)。

## 问题

之前主对话 `handle_msg → real_llm_stream → stream_chat` 只调用 LLM,**不知道视频能力存在**。用户问「帮我做个 PrisirAI 介绍视频」,LLM 只能返回普通文字答复,没法触发 `video.create`。

## 设计

### 1. EXEC 标记协议

LLM 在 ai_text 里嵌入结构化指令:

```
好的,我来帮你做视频。
[[EXEC: video.create topic="PrisirAI" script="介绍 PrisirAI 的核心能力" aspect_ratio="9:16"]]
```

后处理扫到 → 调 `endpoints._REGISTRY` 走对应 handler。

**为什么用标记而非结构化 tool_call**:LLM 输出 JSON tool_call 受限于 LLM SDK 协议 + 需要 LLM 训练时见过该 schema。用文本标记 **跟 LLM 主对话的 markdown 风格自然融合**,只要 prompt 里说明格式即可学会,无 schema 限制。

### 2. 风险门

| risk | 行为 |
|------|------|
| L0 | 后处理直接调 endpoint,推 `capability_exec_result(ok, result)` |
| L1 / L2 / L3 | 推 `capability_confirm_request` 给前端,前端弹确认卡,用户点确认后才真发 |

**为什么 L0 不需确认**:`video.info`、`video.analyze`、`publish.status`、`web.search`、`web.fetch` 都是只读 + 不外发,无副作用。

### 3. 模块

[prisir_work/agent_main_chat_hook.py](../prisir_work/agent_main_chat_hook.py)

```
parse_exec_markers(text) → [ExecMarker]   # 抠 [[EXEC: ...]] 块
scan_and_exec(ai_text) → [ws event]         # 主入口,WS 推事件 list
build_confirm_request(marker) → event       # L1+ 事件构造
build_exec_result(cap, ok, result, error)   # 真发结果事件
```

**关键设计**:
- **降级而非崩溃** — 标记解析失败 / capability 不存在 / endpoint 错都推 `ok=False + error:...`,不抛栈
- **真发走 endpoints._REGISTRY** — 跟 `agent_natural_video.execute` 同一执行路径,不绕过红线
- **`fill_defaults` 兜底** — LLM 漏字段时用 sensible defaults 补全
- **confirm_callback 显式语义** — 函数返回 True 才发(L0/L1+ 都尊重,作为通用用户授权机制)

### 4. 主对话接入

`companion/prisIragent-companion-web.py` 在 `ai_done` 推完后调用:

```python
from prisir_work import agent_main_chat_hook as _hook
hook_events = _hook.scan_and_exec(ai_text)
for ev in hook_events:
    await sess.ws.send_json(ev)
```

主对话流程**不被 hook 失败阻断** — hook 自身 try/except 包裹,异常只 log,不影响 ai_done 已发的事件。

### 5. WS 事件形态

`capability_exec_result`:
```json
{
  "type": "capability_exec_result",
  "capability": "video.info",
  "ok": true,
  "result": {"ok": true, "artifact": {...}, "preview": "..."},
  "error": ""
}
```

`capability_confirm_request`(T16-C 在前端弹卡):
```json
{
  "type": "capability_confirm_request",
  "capability": "video.create",
  "args": {"topic": "X", "script": "Y"},
  "risk": "L2",
  "title": "一句话做视频...",
  "confirm": "L2 一键出片:...",
  "raw": "video.create topic=\"X\" script=\"Y\""
}
```

## 关键文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `prisir_work/agent_main_chat_hook.py` | 230 | EXEC 标记解析 + 风险门 + 事件构造 |
| `companion/prisIragent-companion-web.py` | +20 | ai_done 后调 scan_and_exec |
| `tests/test_agent_video.py` | +13 cases | parse + scan + 风险门 + event shape |
| `verify_wechat_publisher.py` | +1 check | T16-A 全链路自检 |

## 测试

- `tests/test_agent_video.py`:**100/100 passed**(原 87 + T16-A 13)
- `verify_wechat_publisher.py`:**23/23 passed**

## 边界 / 易踩坑

1. **重复 capture group** — `("([^"\\]|\\.)*")` 内层捕获的是 **最后 1 字符**(regex `*` 重复 capture 语义)。改用 group(2) 手动 strip 引号
2. **confirm_callback 优先级** — 用户传入 callback 时即使 L0 也走 callback(用户显式 veto > L0 自动跑);不传 callback 才走 risk-based 分流
3. **ai_text 空字符串** — `parse_exec_markers("") → []`,直接短路
4. **hook 模块 import 失败** — 主对话 try/except 包裹,失败只 log,不影响主流程
5. **真发走 endpoints._REGISTRY** — 不重新实现一遍,免得绕过 token / 权限闸 / port_registry 红线
6. **多 marker 同轮触发** — LLM 一轮可输出多个 EXEC,逐个跑(都进 `events` list 一并发)

## 下一步

- [x] **T16-B**:`intent_summary()` 注入 system prompt,告诉 LLM「有哪些 capability + 何时输出 EXEC」
- [x] **T16-C**:L1/L2/L3 前端确认卡组件(借鉴 jev_confirm 已 ship 的弹卡 UI)
- [x] **T16-D**:节点渲染 + ESC 关闭 + artifact.path 透传 + 跳 💬 一句话 链接

## 全栈 ship 总结

| 子任务 | 入口 | 完成情况 |
|--------|------|----------|
| T16-A | `prisir_work/agent_main_chat_hook.py` + `real_llm_stream` 后处理 | ✅ |
| T16-B | `build_messages` 注入 `intent_summary()` + EXEC 协议提示 | ✅ |
| T16-C | `index.html` + `app.js` + `guohua-theme.css` 确认卡组件 + 后端 capability_confirm WS handler | ✅ |
| T16-D | `renderCapExecResult` 富节点 + ESC 键关闭 + `prisIrai:cap-exec-link` 自定义事件 | ✅ |

## 测试 & verify 终态

- `tests/test_agent_video.py`:**111/111 passed**(原 87 + T16-A 13 + T16-B 2 + T16-C 4 + T16-D 5)
- `verify_wechat_publisher.py`:**26/26 passed**(原 22 + T16-A/B/C/D 各 1)

## 关联

- [[prisir-agent-natural-video]] — P3j T14 自然语言入口
- [[prisir-agent-video-followups]] — P3j T15 4 子任务
- [[prisir-video-creation]] — P3j T11 9 creator
- [[prisir-video-extensions]] — P3j T12/T13 +3 creator + YouTube