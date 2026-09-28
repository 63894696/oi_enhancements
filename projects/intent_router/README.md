# intent_router (M3.69)

**M3.69 意图分发器** — 双 spec 联合(intents_conf + task_conf)→ 路由推荐。

## 目的

把 `companion/classify_intents.py` + `companion/classify_task_local.py` 联合,
根据用户输入同时判断:
- **意图**(5 类:chat/code/search/tool_call/roleplay)
- **任务**(6 类:code_call/code_qa/creative/long/fast/general)

然后查路由表,推荐:
- spec(executor 用哪个 adapter / 脚本)
- 执行命令(本期只 print,不真执行)
- 综合置信度(intent_conf × task_conf)

**Phase 1**:单跑可用,纯 CLI
**Phase 2**(后续):与 PrisirAI 整合

## 资产复用(零侵入)

| 资产 | 路径 |
|------|------|
| Adapter 注册表 | `C:/Users/Administrator/oi_enhancements/companion/adapter_registry.py` |
| 基础模型 | `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/` |
| Adapter 路径 | `D:/prisir-train-assets/trained/{intents,intents_conf,task,task_conf}/adapter/` |
| Intents wrapper | `companion/classify_intents.py` |
| Task wrapper | `companion/classify_task_local.py` |

**不修改** `companion/` 下任何代码。只 `import` 两个 wrapper。

## 安装

无依赖安装(Windows 本地):
```bash
# 确认 adapter 已下载
ls D:/prisir-train-assets/trained/intents_conf/adapter/
ls D:/prisir-train-assets/trained/task_conf/adapter/
```

## 用法

```bash
# 1. 单句文本
python main.py --text "清理 D 盘垃圾"

# 2. 从 stdin
echo "查最近 1h 性能" | python main.py

# 3. 交互模式(loop)
python main.py --interactive

# 4. 多候选排序
python main.py --text "我需要查日志,还要看看磁盘" --multi

# 5. dry-run / run 模式(本期都只 print,不真执行)
python main.py --text "rm -rf C:/Windows" --dry-run
python main.py --text "查最近 1h 性能" --run

# 6. JSON / Markdown 输出
python main.py --text "你好" --format json
python main.py --text "你好" --format md

# 7. 查看完整路由表
python main.py --route-map
```

## 输出示例

```
输入: '清理 D 盘垃圾'

[intents_conf] 5 类分布:
  tool_call      0.91   ← 选
  code           0.05
  chat           0.02
  roleplay       0.01
  search         0.01

[task_conf] 6 类分布:
  general        0.84   ← 选
  long           0.05
  fast           0.05
  code_call      0.02
  code_qa        0.03
  creative       0.01

→ 路由: tool_call + general
→ spec: disk_cleanup/tempfile/log/email
→ 置信度: 0.91 × 0.84 = 0.764
→ 执行: python classify_disk_cleanup.py --path <path>
→ 描述: 用户补 spec(dis/temp/log/email)后执行

(延迟 4.82s, 35 tokens)
```

## 路由映射表

| intent | task | spec | executor | needs_user_spec |
|--------|------|------|----------|-----------------|
| `tool_call` | `general` | `disk_cleanup/tempfile/log/email` | `companion_tool_call` | ✓ |
| `tool_call` | `fast` | `perf` | `perf_guard` | |
| `tool_call` | `long` | `perf_long` | `perf_guard_long` | |
| `chat` | `general` | `jev_chat` | `companion_jev` | |
| `chat` | `creative` | `jev_chat_creative` | `companion_jev` | |
| `code` | `code_call` | `ide_exec` | `ide_tool` | |
| `code` | `code_qa` | `rag_search` | `rag` | |
| `search` | `general` | `web_search` | `web_search` | |
| `search` | `fast` | `fast_path` | `fastlane` | |
| `roleplay` | `general` | `jev_roleplay` | `companion_jev` | |
| `*` | `general` | `general_fallback` | `default` | |

## 项目结构

```
intent_router/
├── README.md
├── main.py                  # CLI 入口
├── src/
│   ├── __init__.py
│   └── intent_router.py     # 核心:路由表 + classify_and_route + multi_candidates
└── tests/
    └── test_e2e.py          # 10 case 端到端测试
```

## 测试

```bash
python tests/test_e2e.py
```

报告写入 `tests/report_e2e.json`。

## 已知限制

1. **分布近似**:conf adapter 只输出 1 个 conf 值,其余类按 `(1-conf)/(n-1)` 平摊。
   这与真 softmax dist 有偏差,主要看**主类的绝对置信度**。
   修法:训练时输出 full softmax(M3.70+ 候选)。
2. **不真执行**:`--run` / `--dry-run` 都只 print 路由建议,不调 executor。
   Phase 2 才与 PrisirAI 整合。
3. **0.6B 漂移**:主类偶尔切到相邻类(例如 chat ↔ roleplay),e2e 测试用集合容忍。

## 设计原则

- **零侵入**:不修改 `companion/` 下任何代码
- **离线可用**:无网络也能跑
- **不重训 LoRA**:复用现成 `intents_conf` + `task_conf`
- **不调外部 API**:纯本地 0.6B 双 adapter 推理
- **纯 CLI**:无 Web UI
