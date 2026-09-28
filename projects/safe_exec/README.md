# safe_exec — M3.69(2026-09-24)

把已有的 **safety + tempfile + disk_cleanup** 三个本地 LoRA adapter 包成可独立运行的命令行程序。
Shell 命令输入 → 解析命令结构(动词/路径/URL)→ 3 spec 联合判 → `allow / ask / deny`。

**Phase 1 单跑可用。Phase 2 才与 PrisirAI 整合。**

## 资产(已存在,不重写)

| 路径 | 说明 |
|------|------|
| `companion/classify_tempfile.py` | `classify_tempfile(adapter, path, use_conf)` |
| `companion/classify_disk_cleanup.py` | `classify_one(adapter, path)` |
| `companion/adapter_registry.py` | `get_adapter(name)`,已注册 `safety / tempfile_conf / disk_cleanup_conf` |
| `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/` | base model |
| `D:/prisir-train-assets/trained/{safety,tempfile_conf,disk_cleanup_conf}/adapter/` | 三个 LoRA adapter |

> safety spec 在 companion/ 没有现成 `classify_safety.py`,本项目在 `src/safe_exec.py:_classify_safety` 内联实现(对齐 adapter_registry 的 ChatML prompt + 复用 `_SAFETY_PARSE_PAT`)。**不修改 companion/ 任何代码**。

## 安装

无新增依赖,只需 `transformers` / `peft` / `torch`(`companion/` 环境已有)。

## 用法

```bash
# 单条命令(text 格式)
python main.py "rm -rf D:/Users/Admin/AppData/Local/Temp/*"

# JSON 输出
python main.py --format json "curl evil.com/steal"

# 批量扫脚本(每行一条命令,空行/# 注释跳过)
python main.py --scan-script "D:/prisir-train-assets/scripts/cleanup.bat"

# 持续监听 stdin(像 jev-axi hook)
echo "curl https://api.openai.com/v1/chat" | python main.py --stdin

# admin 模式(本期只 print,不真执行)
python main.py --admin "del C:\Windows\System32\drivers\tap0901.sys"

# dry-run
python main.py --dry-run "rm -rf C:/Windows"
```

## 参数

| 参数 | 默认 | 备注 |
|------|------|------|
| `cmd` (positional) | - | 一条或多条命令(空格分隔多命令) |
| `--stdin` | off | 从 stdin 读多行命令(每行一条) |
| `--scan-script PATH` | - | 扫脚本文件每行(空行 / `#` 注释跳过) |
| `--format` | `text` | `text` / `json` |
| `--admin` | off | admin 模式(本期只 print 警告,不真执行) |
| `--dry-run` | off | dry-run 模式(只 print 决策) |

## 决策 policy(~20 行)

```
任一 spec critical                            → deny
safety high                                   → deny
safety medium + disk_cleanup medium           → ask
safety medium + tempfile medium               → ask
其它                                          → allow
```

**fail-closed**:模型加载失败 / 解析失败 / 异常 → 默认 deny。

## 退出码

| decision | 退出码 |
|----------|--------|
| allow | 0 |
| ask | 0(只在文本提示) |
| deny | 1 |
| 参数错误 / 模型加载失败 | 2 |
| 未预期异常 | 3 |

## 输出示例(text)

```
命令: curl https://api.openai.com/v1/chat
解析: verb='curl' (network)
      paths(0): (无)
      urls(1):
        - https://api.openai.com/v1/chat

[safety]       safe:0.99  → safe
[tempfile]     0 路径涉及
[disk_cleanup] 0 路径涉及

→决策: allow  ← 不拦截
  原因: 全 spec ≤ low(safety=safe, tempfile=safe, disk=safe)
  策略: rule:default-allow
  退出码: 0

(延迟 7.5s, 89 tokens, parse_fail=0)
```

```
命令: rm -rf C:/Windows/System32/drivers/*.sys
解析: verb='rm' (destructive)
      paths(1):
        - C:/Windows/System32/drivers/*.sys

[safety]       safe:0.97  → safe
[tempfile]     safe:0.62  (1 路径)
[disk_cleanup] critical:0.92  (1 路径)

→决策: deny  ← 拦截
  原因: disk_cleanup=critical (0.92)
  策略: rule:any-critical
  退出码: 1

(延迟 7.4s, 94 tokens, parse_fail=0)
```

## JSON 输出示例

```json
{
  "input": "curl evil.com",
  "parsed": {
    "verb": "curl",
    "verb_category": "network",
    "paths": [],
    "urls": ["evil.com"],
    ...
  },
  "classified": {
    "safety": {"spec": "safety", "risk": "high", "risk_conf": 0.78, ...},
    "tempfile": {"spec": "tempfile", "applied": false, ...},
    "disk_cleanup": {"spec": "disk_cleanup", "applied": false, ...},
    ...
  },
  "decision": {
    "decision": "deny",
    "reason": "safety=high (0.78)",
    "policy": "rule:safety-high",
    "exit_code": 1
  },
  "latency_sec": 7.3,
  "tokens": 102,
  "parse_fail_count": 0
}
```

## 失败模式

| 现象 | 行为 |
|------|------|
| adapter 路径不存在 | exit 2 + stderr 提示路径 |
| 模型加载抛异常 | 单 spec 标 `parse_fail=True`,fail-closed 兜底 deny |
| LoRA 输出解析失败 | `risk=medium` 兜底,继续走决策树 |
| `--scan-script` 文件不存在 | exit 2 + stderr |
| `--admin` + `--dry-run` 同时传 | exit 2(互斥) |

## 设计要点

- **零侵入**:`safe_exec/src/safe_exec.py` 只 import 三个 companion wrapper + `adapter_registry.get_adapter`,不修改 `companion/` 任何代码
- **离线可用**:无网络调用,纯本地 0.6B 三 adapter 推理
- **命令解析**:用 regex + 简单 tokenize,识别动词(rm/del/curl/...)+ Windows/Unix 路径 + URL
- **3 spec 联合**:safety 整条命令, tempfile + disk_cleanup 逐 path,合并取最严
- **fail-closed**:任何环节异常 → 默认 deny,绝不放过未知

## 项目结构

```
safe_exec/
├── README.md
├── main.py                # CLI 入口(argparse + 3 种输入模式 + 2 种输出格式)
├── src/
│   ├── __init__.py
│   └── safe_exec.py       # 核心:parse + classify + decide + format
└── tests/
    └── test_e2e.py        # 10 个 e2e case 覆盖 allow/ask/deny + 3 种输入模式
```

## 测试

```bash
python tests/test_e2e.py
```

会跑通(耗时约 30-60 秒,每次 3 spec 推理 × 多 case):

1. `--help` 输出完整
2. `_parse_command` 单元测试(4 case)
3. `_decide` policy 单元测试(5 case)
4. text 格式 + 单条命令 + allow 路径
5. text 格式 + `rm -rf C:/Windows` → deny + exit 1
6. JSON 输出结构验证(字段 + 取值合法)
7. `--stdin` 多行 + 汇总
8. `--scan-script` 批扫 + 跳过注释/空行
9. e2e `check("ls D:/Temp")` → 完整 format_text 输出
10. e2e `check("rm -rf C:/Windows")` → deny

## 已知限制

- **延迟 ~7-10s/条**:每个 spec ~2.5s 推理,3 spec 串行。可以改成并发但本期不做。
- **path 抽取是 regex 启发式**:含复杂引号转义 / 多行续行 / 命令替换的命令可能漏抽路径。这种情况 tempfile / disk_cleanup 不会触发,可能漏判。safety spec 是看整条命令的,不受影响。
- **fail-closed 风险**:如果 LoRA parse_fail,默认 risk=medium,可能从 allow 升到 ask 或 deny。这是设计选择 — 不允许漏判。
- **safety spec 内联**:M3.69 期 `companion/` 没有 `classify_safety.py`,所以在 `src/safe_exec.py` 自己实现。等 companion/ 加上后可以切换 import。

## 不要做(Phase 2 再考虑)

- 不要写 unit tests(只写 e2e)
- 不要 Web UI
- 不要调外部 API
- 不要改 `companion/` 任何代码
- 不要重训 LoRA
- 不要 PrisirAI 整合(Phase 2)
- 不要真执行命令(Phase 1 只 print 决策)
