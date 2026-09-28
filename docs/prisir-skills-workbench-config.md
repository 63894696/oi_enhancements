# PrisirAI Skills 工作台 — 配置说明文档

> 适用版本:PrisirAI v2.4.0+(2026-09-28 ship Phase 7 后)
> 关联文档:[shipped 总览](prisir-skills-workbench-shipped.md)、[架构设计 v1](imperative-chasing-iverson.md)
> 关联 memory:[Phase 1](../memory/prisIr-skills-workbench-phase-1.md) ~ [Phase 7](../memory/prisIr-skills-workbench-phase-7.md)

---

## 1. 是什么 / 为什么

Skills 工作台是 PrisirAI 的**工具调用基础设施**。核心思路:

- **极简 agent** + **JSON 索引常驻** + **lazy describe/execute**(只在真要用时加载 skill 完整 schema)
- 让 LLM 看到**所有 69 个 skill**(不隐藏任何,质量优先于成本)
- 同时支持**两套调用协议**:`tool_use`(Anthropic/OpenAI 原生 function-calling)和 `EXEC` 标记(`[[EXEC: skill.id(args)]]` 老协议)
- **两阶段 replan**:L1+ skill 默认先弹规划卡,用户点确认才真发,降低误调成本

详细设计动机见 [架构设计 v1](imperative-chasing-iverson.md);本文件只看**怎么配**。

---

## 2. 配置总览(11 个开关)

PrisirAI 有**两套并行入口**(主面板 + 扩展功能语伴),配置方式**不一样**:

| 入口 | 文件 | 配置方式 |
|------|------|---------|
| **PrisirAI 主面板**(左侧历史对话列表 + 顶栏按钮排) | `prisIragent_web.py` | **环境变量**(env vars) |
| **语伴 companion**(`companion_prisirai/` Electron 壳 + 浏览器窗口) | `companion/prisIragent-companion-web.py` | **源码 dict** |

### 2.1 主面板 — env vars

主面板是 PrisirAI 用户主对话窗口,**默认配置在源码默认值里**(`prisIr_work/skills/integration.py:DEFAULT_*`),用户用环境变量 override:

| 环境变量 | 默认值 | 取值 | 含义 |
|---------|-------|------|------|
| `PRISIRAI_SKILLS_INDEX` | `1` | `0` / `1` | `1` = 把 69 个 skill JSON 索引注入到 system prompt(让 LLM 看得到);`0` = 不注入,LLM 看不到工作台 |
| `PRISIRAI_SKILLS_REPLAN` | `1` | `0` / `1` | `1` = 每次主对话触发后跑 replan LLM 二次调用,弹规划卡;`0` = 跳过 replan,直接进 Phase 1 registry 由主对话 LLM 自己挑 |
| `PRISIRAI_SKILLS_INDEX_ULTRA` | `0` | `0` / `1` | `1` = 用 ultra 紧凑版 JSON 索引(7281c,字段名短化 `v/n/s/i/n/r/t`);`0` = 用 standard 版(7993c,完整字段名) |

**怎么改**(Windows 持久):
```bash
# 临时(当前会话)
set PRISIRAI_SKILLS_REPLAN=0
python prisIragent_web.py

# 永久(用户级)
setx PRISIRAI_SKILLS_REPLAN "0"
# 重启终端后生效
```

**典型场景**:
- 想完全关掉工作台:`PRISIRAI_SKILLS_INDEX=0` + `PRISIRAI_SKILLS_REPLAN=0`(LLM 只能用纯文本对话,不能调工具)
- 想保留索引但跳过规划卡:`PRISIRAI_SKILLS_REPLAN=0`(`PRISIRAI_SKILLS_INDEX=1` 保留,LLM 看到工具,但不弹规划卡)
- 想用 ultra 压缩版省 token:`PRISIRAI_SKILLS_INDEX_ULTRA=1`(`PRISIRAI_SKILLS_INDEX=1` 保留)

### 2.2 语伴 companion — 源码 dict

语伴是 Electron 壳里的次要对话窗口,**配置在源码字典里**(不用 env),改完要重启语伴:

| 配置键 | 默认值 | 类型 | 含义 |
|-------|-------|------|------|
| `skills_index_enabled` | `True` | bool | 是否把 skill 索引注入 system prompt |
| `skills_replan_enabled` | `True` | bool | 是否每次触发跑 replan 弹规划卡 |
| `auto_execute_l1_threshold` | `2` | int | L1+ skill 个数 ≤ 此阈值自动执行不弹卡;> 阈值才弹规划卡让用户确认 |
| `tool_use_enabled` | `True` | bool | 是否使用原生 tool_use 协议;`False` 则 fallback 到 EXEC 标记 |
| `skills_plan_timeout_sec` | `5.0` | float | replan LLM 二次调用超时;超时则跳过 |
| `skills_max_loop_steps` | `8` | int | tool_use loop 步数上限(防死循环) |

**怎么改**:
```python
# companion/prisIragent-companion-web.py:build_messages 附近
SKILLS_CONFIG = {
    "skills_index_enabled": True,
    "skills_replan_enabled": True,
    "auto_execute_l1_threshold": 2,
    "tool_use_enabled": True,
    "skills_plan_timeout_sec": 5.0,
    "skills_max_loop_steps": 8,
}
```

修改后重启语伴:
```bash
# Windows
taskkill /F /IM companion_prisirai.exe
# 重新启动语伴
```

---

## 3. 风险等级 — L0/L1/L2/L3 四档

每个 skill 在 `prisIr_work/capability.py` 注册时声明风险级:

| 风险 | 含义 | 例子 |
|------|------|------|
| **L0** | 只读查询 / 资源卡片 / 安全 | `poster.gen`、`agency.search`、`free_for_dev.search`、`video.info` |
| **L1** | 写文件 / 调外部 API / 创建资源 | `music.compose`(生成音频文件)、`youtube.publish`(上传) |
| **L2** | 发网络请求 / 修改系统配置 / 不可逆 | `git.commit`、`calendar.create` |
| **L3** | 删数据 / 支付 / 关键密钥操作 | (目前无 L3) |

### 3.1 风险门行为矩阵

| 风险 | replan 弹卡? | 自动执行阈值 | 用户取消后行为 |
|------|------------|------------|--------------|
| **L0** | ❌ 不弹 | N/A | N/A(根本没弹) |
| **L1** | ✅ 弹(若 L1 个数 > 阈值) | `auto_execute_l1_threshold=2`(默认):L1 个数 ≤ 2 自动执行不弹;> 2 才弹 | 跳过该 skill,继续主对话 |
| **L2** | ✅ 必弹 | 阈值无效(L2 总弹) | 跳过该 skill,继续主对话 |
| **L3** | ✅ 必弹 + 二次确认 | 阈值无效 | 终止整轮执行 |

### 3.2 auto_execute_l1_threshold=2 的具体含义

用户任务触发 replan 后,LLM 二次调用返回 `[{skill_id, args}, ...]`:
- **L0 调用**:直接执行(返回结果给主对话 LLM 续生成)
- **L1 调用且个数 ≤ 2**:**自动执行,不弹规划卡**(节省一次交互)
- **L1 调用且个数 > 2**:**弹规划卡**,等用户点「执行」才真发
- **L2/L3 调用**:总是弹规划卡
- **L0 + L1 混合**:**只看 L1+ 个数**,L0 不计入阈值

调阈值的 trade-off:
- **调高**(如 → 5):更多 L1 自动执行,减少弹卡次数,用户信任度依赖 LLM 选对 skill
- **调低**(如 → 0):所有 L1+ 都弹卡,绝对安全但 UX 累赘
- **调 0**:实际等同 `replan_disabled`,所有 L1+ 都被规划卡拦下(不推荐,失去 L0 直发优势)

---

## 4. fail-open 降级语义

工作台设计**全程 fail-open**:任何环节异常 → 静默降级,**不打断用户对话**。

### 4.1 降级点矩阵

| 降级点 | 触发条件 | 降级行为 | 用户感知 |
|-------|---------|---------|---------|
| **skills_index 注入** | registry 加载失败 / JSON serialize 失败 | 跳过索引注入,只保留 system prompt 主部分 | 无(LLM 看不到工作台,跟 LLM 自由发挥差不多) |
| **replan LLM 二次调用** | LLM 超时 / 返回非 JSON / parse 失败 | 跳过 replan,进 Phase 1 registry 由主对话 LLM 自己挑 skill | 偶尔无规划卡(LLM 自由发挥) |
| **tool_use adapter 解析** | LLM 响应格式异常 / 协议不识别 | 返回 `calls=[]`,loop 自然结束 | LLM 走纯文本路径 |
| **execute_skill 调用** | skill 不存在 / args 校验失败 / handler 抛异常 | `SkillResult(ok=False, error=...)` 喂回 LLM | LLM 看到错误可重试或换 skill |
| **SkillPlanQueue 推送** | 队列满 32 上限 | 丢弃最老项,推新项(日志记 warn) | 用户偶尔漏看早期规划卡 |
| **polling 端点** | 端点 404 / 超时 | 前端静默重试 900ms 间隔 | 无(主对话正常进行) |

### 4.2 fail-open 的设计取舍

**为什么不 fail-closed?** PrisirAI 主对话是用户**最常用的入口**,工作台只是"加速器"。工作台 fail 中断主对话 = 用户受罚(合理的代价),但工作台 fail 时主对话降级 = 用户不受影响。**用户问题让工作台承担风险,工作台不能让用户承担风险**。

**什么时候需要 fail-closed?** 写 L3 风险操作(如支付、删数据)时,需单独走 `mode=strict` 配置,不在默认 fail-open 范围。

---

## 5. token 经济性

工作台的核心成本 = system prompt 多一段 **JSON 索引**(~8000c),换 LLM 看得到 69 个 skill 的能力。

### 5.1 token 经济性表

| 模式 | 字符数 | token(估 0.7c/token) | 节省比 | 何时用 |
|------|-------|---------------------|--------|--------|
| **无工作台**(老 system 全 capability 描述) | ~12000 | ~17143 | 基准 | 已被工作台取代 |
| **standard 紧凑**(默认) | **7993** | ~11418 | **-38%** | 默认值,完整字段名 LLM 易理解 |
| **ultra 紧凑**(字段名短化) | **7281** | ~10401 | **-43.5%** | 用户主动开 `PRISIRAI_SKILLS_INDEX_ULTRA=1` |
| **禁用索引**(`PRISIRAI_SKILLS_INDEX=0`) | 0 | 0 | -100% | LLM 看不到 skill,退化到纯对话 |

### 5.2 实际节省的拆解

不走工作台时(老方案),每次主对话都付:
- system prompt 永久 ~12000c
- intent_summary 5 处注入 ~1500c / turn
- 长对话 history 再叠

走工作台后:
- system prompt 固定 ~500c(极简 agent 段)
- + 索引 standard ~8000c 或 ultra ~7280c
- + 触发的 skill describe 按需 ~200-500c / turn(L0 直发不触发)
- + 长对话 history 同上

**关键点**:69 项索引虽占 8000c,但 LLM 不需要再学 EXEC 语法(节省 ~500c / turn 的 prompt 教学),且 capability 数量线性扩展时系统 prompt 不再 O(N) 增长(节省结构性成本)。

### 5.3 调档指引

- **普通用户**:默认 standard 就够,无需手动改
- **长对话 + 慢网络**:`PRISIRAI_SKILLS_INDEX_ULTRA=1` 省 ~700c / turn
- **LLM 老弱模型**(Qwen-Turbo 等只支持 EXEC):`PRISIRAI_SKILLS_INDEX=1` 保留(EXEC 仍需要索引)+ `PRISIRAI_SKILLS_REPLAN=0`(避免规划卡超时)
- **想完全关**:两 env 都设 `0`,回到对话 LLM 自由发挥

---

## 6. 灰度切换指引

工作台 ship 1 个季度(到 2026-12-31)内,**两套协议并存**(tool_use + EXEC),用户可按 LLM 能力切换:

### 6.1 协议选择决策树

```
你的主对话 LLM 是什么?
├─ Claude Opus 5 / Sonnet 5 ─→ tool_use(默认开,无需改)
├─ GPT-4o / GPT-5 ──→ tool_use(默认开)
├─ Qwen3-Max / DeepSeek-V3.2 ──→ tool_use(若厂商开了)+ EXEC 兜底(无需改)
├─ Qwen-Turbo / 小模型 ──→ EXEC(自动 fallback 到 EXEC 标记)
└─ 自部署 vLLM ──→ 看模型支持,通常 EXEC
```

**默认行为**:工具支持的 LLM 走 tool_use,不支持的自动降级 EXEC,**无需手动配**。

### 6.2 强制模式(灰度测试用)

```python
# companion/prisIragent-companion-web.py
SKILLS_CONFIG = {
    "tool_use_enabled": True,   # 强制 tool_use
    # "tool_use_enabled": False, # 强制 EXEC(老 LLM 测试用)
}
```

灰度测试建议:
1. **第 1 周**:全量默认,观察 `desc.tool_use` 日志看哪些 LLM 走哪条
2. **第 2-4 周**:对走 EXEC 的 LLM 单独测,确认功能等价
3. **1 季度后**(2026-12-31):如 tool_use 跑稳,可 deprecate EXEC 标记(走日志提醒 + 1 季度宽限期)

### 6.3 关闭工作台(完全退化)

```bash
# 主面板
set PRISIRAI_SKILLS_INDEX=0
set PRISIRAI_SKILLS_REPLAN=0

# 语伴
# companion/prisIragent-companion-web.py
SKILLS_CONFIG = {
    "skills_index_enabled": False,
    "skills_replan_enabled": False,
    "tool_use_enabled": False,
}
```

**关闭后**:LLM 只能纯文本对话,看不到 69 个 skill,等同于 v2.3 之前的能力。

---

## 7. 调试 / 日志

### 7.1 看 LLM 真用了哪些 skill

工作台 ship 时埋了 `desc.tool_use` 日志:

```python
# 启动时打开 debug 日志
import logging
logging.getLogger("desc.tool_use").setLevel(logging.DEBUG)
```

输出形如:
```
2026-09-28 14:23:15 [tool_use] LLM emit tool_call: skill=video.info args={'path': 'x.mp4'}
2026-09-28 14:23:16 [tool_use] execute_skill ok: skill=video.info duration=0.3s
2026-09-28 14:23:17 [tool_use] LLM 续生成(无 tool_call),loop 终止
```

### 7.2 规划卡被压栈?

`SkillPlanQueue` 单例上限 32,满了丢老的:
```
2026-09-28 14:25:33 [SkillPlanQueue] queue full (32), drop oldest eid=xxx
```

### 7.3 replan 频繁失败?

多半是 LLM 二次调用返非 JSON,在日志里搜:
```
2026-09-28 14:30:11 [replan] parse_fail: LLM 返非 JSON, content=...
```

调阈值 `skills_plan_timeout_sec` 到 10.0 看是不是超时,或换更便宜的 LLM 作 replan LLM。

---

## 8. 风险登记

| 风险 | 触发 | 缓解 |
|------|------|------|
| **replan LLM 误判频弹卡** | 默认开 + L1+ 都走 replan | `auto_execute_l1_threshold=2` 自动执行 ≤2 个;用户想更松可调到 5 |
| **tool_use 协议碎片化** | 不同 LLM 厂商协议差异 | 隔离在 `tool_use/` adapter 层,新增协议加 `_parse_xxx.py` |
| **69 项索引 + 长对话 history 超 token 窗口** | 长对话 + 索引都常驻 | 主面板已有 `_apply_compact` 兜底折叠 history;索引满了需手动关 |
| **规划卡 UX 累赘** | 用户每次都点「执行」 | `auto_execute_l1_threshold=2` 让 ≤2 个 L1 自动;阈值可调到 5 |
| **LLM 不支持 tool_use 又走了 tool_use 路径** | 厂商静默降级 | `run_loop` 默认 8 步上限,失败自动降 EXEC |

---

## 9. 关联阅读

- [shipped 总览](prisir-skills-workbench-shipped.md) — 8 阶段 commit hash + 文件清单
- [架构设计 v1](imperative-chasing-iverson.md) — 设计动机 + token 经济性原始测算
- memory:`prisIr-skills-workbench-phase-{1,2,3,3.5,4,5,6,7}.md` — 各阶段 ship 详情