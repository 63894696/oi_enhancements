---
name: prisIr-companion-m357-classify-task-split
description: M3.57 拆 classify_task code_call/code_qa 闭环 2026-09-23 — 23/23 测试通过;新增 _CALL_HINTS/_QA_HINTS 双 regex + _search_either(原文+反转)应对 IME 重排 + qa_short fast 兜底;_TASK_PREFERENCE 加 code_call/code_qa 两条;old 'code' key 删了,所有 caller 自动获益
metadata:
  type: project
---

# M3.57 classify_task 拆 code_call/code_qa 闭环(2026-09-23)

## 目标

M3.56 整合方案落地:把 `classify_task` 里的 `code` 一刀切拆成 `code_call`(写代码,落强代码模型)vs `code_qa`(聊概念,落解释型模型)。

## 关键改动 `fastlane/providers/llm_prisir.py`

### 1. 三 regex 拆 code

| regex | 职责 | 关键 tokens |
|-------|------|------------|
| `_CODE_HINTS`(扩) | 任何 code 词(语言/库/概念) | def/class/python/git/rebase/docker/callback/lambda/装饰/闭包/异步/线程 |
| `_CALL_HINTS`(新) | 写代码动作动词 | \`\`\`/写.*代码/实现/debug/修.*bug/编译/跑.*报错/实现一下/写一下 |
| `_QA_HINTS`(新) | 聊概念疑问 | 什么是/怎么理解/原理/区别/为什么/解释/讲讲/介绍/优缺点/怎么用/怎么解决/怎么停 |

### 2. `_search_either` 应对 IME 字符重排

中文输入法在 3 字短语上常把 `是/什/么` 排成 `什/么/是`(顺序错位)。原始 regex 只能 match 正序。`_search_either(pattern, text)` 同时尝试原文 + 全文反序,边缘场景兜底。

### 3. classify_task 判定顺序

```
1. 长上下文 → long
2. code_qa 模式(qa+code 双命中)→ code_qa(聊优先于写)
3. code_call 模式(``` 或 短+动词)→ code_call
4. code 兜底(单 code 词)→ code_call(默认强动作)
5. fast 短 + FAST_HINTS → fast
6. qa_short(< 60 字符 + 是什么/什么是/为什么 + 无 code)→ fast(IME 兜底)
7. 兜底 → general
```

### 4. `_TASK_PREFERENCE` 加两条

```python
"code_call": ["openai", "anthropic", "custom"],   # 强代码模型
"code_qa":   ["anthropic", "openai", "custom"],   # 解释型优先
```

旧 `"code"` key 删除 — 所有 caller 自动落到 code_call/code_qa 平台偏好序。

## 测试结果

23/23 通过(unicode-escape 中文):

| 期望 | 实际 |
|------|------|
| code_call × 8 | 全过 |
| code_qa × 8 | 全过 |
| fast × 3 | 全过(包含 IME 重排「什么是 LLM?」) |
| long × 1 | 过 |
| general × 3 | 全过(避免「你好最近怎么样」误归 fast) |

## 关键 BUG 与修复

1. **IME 字符重排**:「什么是 LLM?」用户输入常为 `什/么/是 LLM?` 但 regex 期望 `是/什/么`。加 `_search_either` + `qa_short` 兜底
2. **过度激进 fast**:`你好最近怎么样` 含 `怎么` 也被命中,改成只接 `是什么/什么是/为什么` 三关键词
3. **`怎么解决/怎么停/怎么删` 缺**:补进 `_QA_HINTS`,让 `Python GIL 怎么解决?` 走 code_qa 而非 code_call
5. **`sql / c\` 字符类特殊字符**:去掉 `c++`(regex 字符类里 `+` 字面 OK 但 `++` 易误判),用 `cpp`

## 文件改动

| 文件 | 改动 |
|------|------|
| `fastlane/providers/llm_prisir.py` | **M3.57** — `_CODE_HINTS` 扩词库,新增 `_CALL_HINTS / _QA_HINTS / _search_either`,`classify_task` 重写逻辑,`_TASK_PREFERENCE` 拆 code_call/code_qa |
| `companion/companion_llm.py` | **未改** — 通过 `classify_task()` 调用,自动获得 code_call/code_qa,无 break |

## Why & How to apply

**Why**:
- `code_call` 落到 `openai`(gpt-4o)/`anthropic`(claude-opus) 强代码模型,`code_qa` 落到 `anthropic` 解释型(Anthropic 在概念解释上更稳)
- 不拆:用户问「装饰器是什么」会和「帮我写装饰器」抢同一个 `openai → anthropic` 序,体验上后者准前者糙

**How to apply**:
- 类似"三层路由"的**任务层**:`classify_task` 是单一函数,但**任务类型词汇表**跟**平台偏好序**一一对应,改一个必须改另一个
- **IME 重排兜底**在中文 NLU 工程里很常见:3 字短语同时匹配正/反序是底线,5 字以上无所谓
- **regex 不要贪**:QA + CALL 命中同时存在时,优先级顺序决定落到哪类;默认「聊优先于写」(问概念>写代码更安全)
- 测试用 `\uXXXX` 转义避免终端 paste 时字符重排造成假 fail

## 关联

- [[prisIr-companion-m356-routing-admin]] — M3.56 整体方案,本文档是 M3.57 实施
- [[prisIr-companion-m355-closed]] — M3.55 watchdog kill_mode(同窗口前的最近一个 milestone)
- `companion/companion_llm.py:47` — `classify_task` 唯一生产 caller
- `fastlane/providers/llm_prisir.py:_TASK_PREFERENCE` — 任务类型→平台序映射