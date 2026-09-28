---
name: prisIr-companion-m356-routing-admin
description: M3.56 PrisirAI 任务路由整合方案 + admin 启动建议 2026-09-23 — 三层路由(意图/任务/平台)拆分 code_call vs code_qa;PrisirAI 默认非 admin + --admin 显式提权(渐进式安全)
metadata:
  type: project
---

# M3.56 PrisirAI 任务路由整合方案 + admin 启动建议(2026-09-23)

## 用户问题

> 任务复杂度和 code call 鉴别 vs code 知识问答,它们似乎都和 PrisirAI 的任务路由有关,需要做整体考虑修改吗?
> 还有顺便讲一下,你建议 PrisirAI 常规以管理员启动吗?

**用户回:**"正是因为之前做了类似能力,但不确定现在新模型如何协作整体发挥作用,希望你能整体考虑给出合理整合方案而不是我选。"

## 1. 现状盘点(三套分类并存,语义重叠)

| 层级 | 模块 | 分类 | 用途 | 时延 |
|------|------|------|------|------|
| **意图层**(用户说啥) | `companion_jev.ask_intent` / 本地 `intents` adapter | **chat/code/search/tool_call/roleplay** | 路由决策、UI 标签、报告 | Jev 1100ms / 本地 < 100ms |
| **任务层**(AI 怎么干) | `fastlane/providers/llm_prisir.classify_task` | **code/creative/long/fast/general** | 选平台/模型 | 同请求一起,无单独延迟 |
| **平台层**(用谁执行) | `dynamic_router.route_task` | **BAILIAN/SILICONFLOW/STEPFUN/ARK/...** | 跨厂商 fallback | 失败时 +5-30s/平台 |

**问题**:`classify_task` 把"写个 Python 函数"和"什么是装饰器"都归 `code`,但路由策略应不同:
- **code_call**(有 ``` 代码块)→ 强代码模型 + 必要时 OI code interpreter
- **code_qa**(聊概念/原理)→ 解释能力强模型(Anthropic / qwen3-max),不用 code interpreter

## 2. 整合方案 — 三层路由 + 拆 code_call/code_qa

```
用户消息
   ↓
[意图层] ask_intent → chat | code | search | tool_call | roleplay  (companion_jev.py)
   ↓
[任务层] classify_task → long | fast | code_call | code_qa | creative | general  (M3.57 改造)
   ↓
[平台层] route_task → BAILIAN / SILICONFLOW / STEPFUN / ARK / 自定义  (dynamic_router / fastlane)
```

### 改造点

| 文件 | 改动 |
|------|--------|
| `fastlane/providers/llm_prisir.py:classify_task` | **拆 code → code_call / code_qa**:code_call = 含 ``` 或 def/class/import 等代码块;code_qa = 聊概念/原理/为什么 |
| `fastlane/providers/llm_prisir.py:_TASK_PREFERENCE` | 加 code_call / code_qa 两条,各自平台序不同 |
| `companion/companion_jev.py:INTENT_QUESTIONS` | 不动(语义层跟任务层解耦),只是前端在 code 意图下补"任务类型"标签 |
| `companion/data_prep_intents.py` | 不动(意图训练集,跟任务无关) |
| `dynamic_router/__init__.py` | 不动(平台层跟任务层解耦) |
| **新加** `companion/classify_task_local.py` | **本地 0.6B 兜底**(跟 intents adapter 类似),M3.58 用 `classify_intents` 模式实现 task 路由本地 fallback |

### 为什么不直接合并成一层

- **意图层**面向"用户想干啥"(产品逻辑决定 UI 标签、路由决策)
- **任务层**面向"AI 该用啥方式干"(模型选择)
- 同一意图可能有不同任务类型:`code 意图` + `code_qa 任务` vs `code 意图` + `code_call 任务` → 不同平台,不同 UI 提示
- 合并会丢失这层正交性,Fastlane + companion 双层耦合,反而更难迭代

### code_call vs code_qa 区分规则(M3.57 改造详情)

```python
# fastlane/providers/llm_prisir.py 改造
_CALL_HINTS = re.compile(
    r"(```|def |class |import |function|写.*代码|帮我写|写个|实现|debug|报错|bug|编译|脚本)", re.I)
_QA_HINTS = re.compile(
    r"(什么是|怎么理解|原理|区别|为什么|解释|讲讲|介绍|how does|what is|why)", re.I)

def classify_task(text: str) -> str:
    t = text or ""
    if len(t) > _LONG_HINT:
        return "long"
    if _CALL_HINTS.search(t) and ("```" in t or len(t) < 200):  # 短+代码块 → code_call
        return "code_call"
    if _QA_HINTS.search(t):
        # 聊 code 概念 → code_qa;聊其他 → general(不动)
        if _CODE_HINTS.search(t):
            return "code_qa"
        return "general"
    if _CODE_HINTS.search(t):
        return "code_call"  # 默认兜底
    if _FAST_HINTS.search(t) and len(t) < 200:
        return "fast"
    return "general"
```

### 平台偏好(M3.57 改造详情)

```python
_TASK_PREFERENCE: Dict[str, List[str]] = {
    "code_call": ["openai", "anthropic", "BAILIAN", "SILICONFLOW"],   # 强代码模型
    "code_qa":   ["anthropic", "BAILIAN", "openai", "SILICONFLOW"],   # 解释能力强
    "creative":  ["anthropic", "openai", "BAILIAN"],
    "general":   ["anthropic", "openai", "BAILIAN"],
    "fast":      ["SILICONFLOW", "BAILIAN", "openai"],                # 免费快速
    "long":      ["anthropic", "openai", "BAILIAN"],                   # 长上下文
}
```

## 3. admin 启动建议

| 方案 | 优势 | 劣势 |
|------|------|------|
| **常规 admin 启动** | watchdog kill_mode 真生效 / ProBalance 真降权 / IFEO 真拦截 | 安全风险 — 任何 Python 代码都能调 `process_kill_impl` / 改服务 StartupType;一旦 OI chat 误调,可能误杀关键进程 |
| **常规非 admin** | 安全 — kill / Service mask / IFEO 都被 Windows 拒绝 | watchdog kill_mode 永远空跑(白做了),Service 拦截不可用 |
| **默认非 admin + `--admin` 显式提权**(推荐) | 显式优于隐式 — 用户主动选择何时提权;companion 启动时检测是否 admin 并在 UI 显示;watchdog kill_mode 在非 admin 时仍能记录意图(dry_run=True),提权后无缝接管 | 多一个启动参数 |

### 具体路径

```python
# companion launcher 加 --admin 检测(M3.59)
def _is_admin() -> bool:
    return ctypes.windll.shell32.IsUserAnAdmin()

# 启动时:
if not _is_admin():
    print("⚠️ 非 admin — watchdog kill_mode 只记录不执行,ProBalance / IFEO 需 admin")
    # watchdog 自动 dry_run=True
else:
    print("✅ admin — watchdog kill_mode 真生效")
```

### 实际收益

- **常规非 admin**:chat / 意图 / 日志 / 邮件 / 调度都正常,90% 用户场景
- **`--admin` 启动**(用户主动):
  - watchdog kill_mode 真生效(disconnected TAP 出现即杀)
  - ProBalance 真降权(高 CPU 时)
  - IFEO debugger 拦截启动(M3.56+ 留)
  - Service StartupType=Disabled(M3.56+ 留)
- **watchdog 自动适配**:`dry_run` 自动跟随 admin 状态切

## 4. 后续节奏(M3.56 路线图)

| 阶段 | 工作量 | 收益 |
|------|------|------|
| **M3.57** 拆 `classify_task` code_call/code_qa | 1.5h | 模型路由更准,代码任务落到强模型 |
| **M3.58** `classify_task_local.py` 本地 0.6B 兜底 | 2h | 离线可用,Jev/OpenRouter 失败仍路由 |
| **M3.59** `companion launcher --admin` 渐进 | 1h | 安全 + 真生效(用户主动提权) |
| **M3.60+** IFEO 启动拦截 / Service mask | 1.5h | 真启动拦截(需 admin) |

**整体方案完整,不急着动代码,先 M3.56 路线图决策,然后分批**。

## Why & How to apply

**Why**:
- 三套分类(意图/任务/平台)各有用途,合并会丢正交性 — `code 意图` + `code_call` 是常见组合,但 `code 意图` + `code_qa` 也常见
- `code_call vs code_qa` 拆分直接关系模型选择 — code_call 落到 code interpreter / qwen3-coder-plus,code_qa 落到 qwen3-max / Anthropic 解释型
- admin 启动建议按"显式优于隐式" — 用户主动提权比"默认全开"安全,Python/Node 子进程调 process_kill_impl 是真风险

**How to apply**:
- 类似"分层路由"模式:**意图 → 任务 → 平台** 三层,每层独立 cache / 兜底
- 拆分类的**关键词 regex** 优先 — 简单、可调、零成本,数据驱动放在后续迭代
- admin 启动统一在 launcher 检测一次,模块内部不再各自调 `_is_admin()`
- watchdog / IFEO / Service mask 等"需要 admin" 的工具统一**降级为 dry_run / 记录**,而不是直接抛权限错

## 关联

- [[prisIr-companion-m354-closed]] — M3.54 Companion → PrisirAI 整合(架构基础)
- [[prisIr-companion-m355-closed]] — M3.55 watchdog kill_mode(admin 提权后的实际效果)
- [[prisIr-companion-m353-conflict-analysis]] — 冲突分析(分层决策依据)
- [[prisIr-companion-m350-intents-closed]] — M3.50 intents 闭环(意图层基础)
- `fastlane/providers/llm_prisir.py` — 任务层分类(M3.57 改造目标)
- `dynamic_router/__init__.py` — 平台层(7 平台 fallback)
- `companion/companion_jev.py:INTENT_QUESTIONS` — 意图层(5 类)