# PrisirAI Agent 视频能力 follow-ups — P3j T15 文档

2026-09-26 ship。**4 个可选 follow-up 全部 ship** — 让 agent 代执行视频能力的全栈闭环:Web UI 一句话入口 / LLM 增强意图抽取 / 多轮对话补 missing / 跨能力编排 workflow。

## 总览

| 子任务 | 入口 | 解决什么 |
|--------|------|----------|
| T15-A | [prisir_work/cli.py](prisir_work/cli.py) + `/api/chat/parse` + `/api/chat/exec` | 用户不用选 Tab,在「💬 一句话」Tab 直接说人话 |
| T15-B | [prisir_work/agent_llm_enhancer.py](prisir_work/agent_llm_enhancer.py) | regex 模式抽不出时,LLM 二次消解 + 抽取复杂 args |
| T15-C | [prisir_work/agent_multi_turn.py](prisir_work/agent_multi_turn.py) | 缺必填参数时自动追问,多轮填完再发 |
| T15-D | [prisir_work/agent_video_workflow.py](prisir_work/agent_video_workflow.py) | 「做视频 + 上传 B站 + 上传 YouTube」一次到位,DAG 编排 |

## T15-A — Web UI 「💬 一句话」输入框

**前端**(index.html)
- 第 9 个 Tab 「💬 一句话」
- textarea + 「🔍 解析意图」按钮 → 调 `/api/chat/parse`
- 命中后展示完整 body + missing 提示 + 「🚀 真发(我已确认)」按钮
- 「🚀 真发」调 `/api/chat/exec`,5 分钟超时

**后端**(`/api/chat/parse` + `/api/chat/exec`)
- `parse`:`parse_intent + fill_defaults`,只读,不真发
- `exec`:拿前端送的 intent → 走 `endpoints._REGISTRY[endpoint].handler(body)`,跟 agent_natural_video.execute 同一执行路径
- missing 非空 → 拒,返 ok=False

## T15-B — LLM 增强意图抽取

```python
# regex 模式 12 条 + capability.search 兜底都没命中 → 调 LLM
from prisir_work.agent_llm_enhancer import enhance_with_llm
r = enhance_with_llm("把那段视频发到 b 站然后再发到 YouTube")
# → IntentResult(capability=..., args={...}, confidence=0.85, error="llm_enhanced")
# LLM 不可用 / 超时 / JSON 解析失败 → None,继续走 not-recognized
```

**Prompt 构造**(`build_llm_prompt`):
- 把 12 个 capability 的 `id + title + keywords[:8]` 渲染成清单
- 输出 JSON Schema 写明 capability 候选集 + args 字段语义
- temperature=0.2(低,稳定抽取)、max_tokens=600、timeout=20s

**LLM 调用**(`_collect_stream_text`):
- 复用 `companion.companion_llm.stream_chat`(已经在用,key 路由 OK)
- 串 token → 完整文本
- 失败/超时 → None

**JSON 解析**(`parse_llm_json_response`):
- 容 markdown ` ```json ... ``` ` 围栏
- 容前缀废话(常见:LLM 加 "Here is the JSON: ..." / 后缀 "OK!")
- 平衡括号扫描(start with `{`,end with matching `}`)
- 失败 → None

**接入**(`parse_intent`):
- regex 未命中 → `capability.search` → `enhance_with_llm` → not-recognized
- 任何一级失败/不可用都走下一级,**绝不替 regex 拍板**

## T15-C — 多轮对话补 missing

```python
from prisir_work.agent_multi_turn import SessionStore, start_session, fill_missing

store = SessionStore()
s = start_session("帮我做个 9:16 短视频,主题 PrisirAI")
# Session(capability='video.create', missing=['script'],
#         missing_questions=['文案/脚本是什么?(或口播稿)'])

s = fill_missing(s.id, "script", "介绍 PrisirAI 的核心能力")
# ready=True, missing=[], args={..., "script": "..."}
```

**Session 数据类**:
- `id`(uuid4) / `original_query` / `capability` / `args` / `missing` /
  `missing_questions` / `history` / `ready`
- `missing_to_questions` 把 `['script']` 翻成 `['文案/脚本是什么?(或口播稿)']`
  (12 个 key 都覆盖:topic/script/text/file/output/input/music/sub/video/title/path)

**降级**:
- `start_session` 解析失败 → Session(capability='', questions=[error msg])
- `fill_missing` 不存在 id → None(不动 ctx)
- `ready=True` 后再 fill → 拒二次填(只加额外 args)
- in-memory 单例,redis/sqlite 留作 P3j T15.x

## T15-D — 跨能力编排 workflow

**DSL**(JSON):
```json
{
  "steps": [
    {"id": "create", "capability": "video.create",
     "args": {"topic": "PrisirAI", "script": "..."}},
    {"id": "burn_sub", "capability": "video.burn",
     "depends_on": ["create"],
     "args": {"input": "$create.result.artifact.path",
              "sub": "C:/sub.srt", "output": "C:/out.mp4"}},
    {"id": "yt", "capability": "youtube.upload",
     "depends_on": ["burn_sub"],
     "args": {"video": "$burn_sub.result.artifact.path",
              "title": "PrisirAI Demo", "privacy": "public"}}
  ],
  "stop_on_error": true
}
```

**$ref 语法**:`$step_id.path.to.value` → 拿前驱 step 的 result 字段。
- 全匹配(`"$a.path"`)→ 直接返回对象
- 部分匹配(`"前缀-$a.path-后缀"`)→ 字符串内替换
- 嵌套 dict / list 递归处理
- 未知 step_id → None,不抛栈

**DAG 调度**(`_topo_sort`):
- Kahn's algorithm:依赖入度 + 子节点列表
- 环检测:`len(out) != len(steps)` → ValueError
- 未知 dep → ValueError

**单步执行**(`_execute_step`):
- 走 `endpoints._REGISTRY[endpoint].handler(resolved_args)` — 跟 agent_natural_video.execute 同一执行路径
- 异常 → `exception:{Type}: {msg}` 错误,不抛栈
- cap 不存在 → `capability_not_found:`
- ep 不存在 → `endpoint_not_found:`

**结果**:
- `WorkflowResult(ok, steps={step_id: {ok, result, args_used}}, failed_step, error)`
- 失败 halt 后续 step,返 `failed_step + error`
- `to_dict()` 序列化给前端

## 关键文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `prisir_work/agent_llm_enhancer.py` | 195 | LLM 增强意图抽取 + JSON Schema prompt + 鲁棒 JSON 解析 |
| `prisir_work/agent_multi_turn.py` | 175 | 多轮对话 SessionStore + missing 翻译问句 |
| `prisir_work/agent_video_workflow.py` | 230 | DAG 工作流 + $ref 解析 + 单步执行 |
| `companion/prisIragent-wechat-publisher.py` | +90 | `/api/chat/parse` + `/api/chat/exec` 路由 |
| `companion/.../static/index.html` | +90 | 「💬 一句话」Tab + JS handler |
| `tests/test_agent_video.py` | +37 cases | 整体测试 |

## 测试

- `tests/test_agent_video.py`:**87/87 passed**(原 50 + T15-A 5 + T15-B 9 + T15-C 10 + T15-D 13)

## 边界 / 易踩坑

1. **LLM enhancer 失败/超时 → 返 None**,不抛栈,parse_intent 走 not-recognized
2. **JSON 解析鲁棒**:容 markdown 围栏 / 前缀 / 后缀 / 嵌套 / 非 JSON
3. **multi_turn 一次 start 后不能改 capability** — 字段再填额外 args 不重算 missing
4. **workflow $ref 找不到 → None** — 不抛栈,args 留 None 让 endpoint 自己校验
5. **DAG 环检测**:`_topo_sort` 不返回部分排序,直接 raise ValueError
6. **capability_not_found vs endpoint_not_found** — 两个独立错误,前者白名单缺,后者实现缺
7. **`/api/chat/exec` 缺 missing 必拒** — 前端 + 后端双重校验,杜绝 LLM/前端漏改

## 下一步(可选)

- [ ] **multi_turn 持久化** — 改 SQLite / Redis,跨进程共享(Web UI ↔ LLM 框架)
- [ ] **LLM enhancer 自动发现** — 12 个 capability 不够时,LLM 提议新 capability 注册到 _REGISTRY
- [ ] **workflow 可视化** — Web UI 加 DAG 编辑器,拖拽 step → 自动生成 DSL
- [ ] **workflow 重试** — 失败 step 自动重试(N 次后 escalate)
- [ ] **multi_turn 多语言** — missing_questions 加英文版,适合 i18n

## 关联

- [[prisir-agent-natural-video]] — P3j T14 自然语言入口(本轮的起点)
- [[prisir-video-creation]] — P3j T11 9 creator
- [[prisir-video-extensions]] — P3j T12/T13 +3 creator + YouTube
- [[prisir-publisher-module]] — P3j T10 多平台发布