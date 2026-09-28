# email_triage — 邮件紧急度筛(M3.69 Phase 1)

> 把已有本地 LoRA 邮件分类模型做成可独立运行的命令行程序。
> Phase 1 单跑可用;Phase 2 才与 PrisirAI 整合。

## 功能

- 扫 **.eml 文件**(本地目录递归) 或 **IMAP 邮箱**(Gmail / Outlook / QQ …)
- 用 `email_conf` adapter(0.6B Qwen3Guard + LoRA)判 **5 类风险**:safe / low / medium / high / critical
- 输出 **text / JSON / Markdown** 三种格式
- `--top N`:只看 critical+high 前 N 条
- `--since 24h / 7d / 30m`:时间窗过滤
- 离线可用:无网络也能跑(eml 模式)

## 安装 / 依赖

无需安装(`email_triage/` 是孤立项目),只需 Python 3.10+。

依赖(`pip install`):
```
torch
transformers
peft
accelerate
```
(这些是 `companion/adapter_registry.py` 推理时需要的;eml 模式下不强制要。)

## 快速开始

### 默认跑(扫 `./inbox` 目录)
```bash
python main.py
```

### 指定 eml 目录
```bash
python main.py --eml-dir "D:/mails/inbox"
```

### IMAP 真连
```bash
export GMAIL_PASSWORD="xxxx-xxxx-xxxx-xxxx"
python main.py \
    --imap imap.gmail.com \
    --user "you@gmail.com" \
    --password-env GMAIL_PASSWORD \
    --since "24h"
```

### 输出 Markdown 报告
```bash
python main.py \
    --eml-dir "D:/mails/inbox" \
    --top 20 \
    --format md \
    --output "daily_triage.md"
```

### JSON 输出(给上层调)
```bash
python main.py --format json --top 10
```

## CLI 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--eml-dir DIR` | `./inbox` | 本地 .eml 目录(递归) |
| `--imap HOST[:PORT]` | - | IMAP 服务器(启用 IMAP 模式) |
| `--user USER` | - | IMAP 用户名 |
| `--password-env ENV` | - | 密码所在环境变量名(**不要传明文**) |
| `--folder FOLDER` | `INBOX` | IMAP 文件夹 |
| `--since WHEN` | `24h` | 时间窗,支持 `24h` / `7d` / `30m` |
| `--format {text,json,md}` | `text` | 输出格式 |
| `--top N` | `20` | critical+high 显示前 N 条;`0` 不截断 |
| `--output / -o FILE` | stdout | 写到文件 |
| `--spec {email,email_conf}` | `email_conf` | 用哪个 adapter |
| `--stats` | off | 末尾打一行 stats |

## 输出示例(text 模式)

```
[email_conf] 扫 24h 邮件: 47 封

  5 类分布:
    safe       31 ( 66.0%)
    low         8 ( 17.0%)
    medium      4 (  8.5%)
    high        3 (  6.4%)   WARNING 立刻看
  critical 1 (  2.1%)   CRITICAL 银行验证码
    parse_fail 0

  待办(critical + high,按时间倒序):

  [CRITICAL] [09-24 12:34] critical   Amazon 验证 <verify@amazon.com>
                          Subject: Your verification code is 123456
  [HIGH]     [09-24 11:23] high       老板会议邀请 <boss@company.com>
                          Subject: Tomorrow's sync - confirm?
  [HIGH]     [09-24 10:15] high       GitHub 安全告警 <noreply@github.com>
                          Subject: New SSH key added to your account
```

## 项目结构

```
email_triage/
├── README.md
├── main.py              # CLI 入口
├── src/
│   ├── __init__.py
│   └── email_triage.py  # 核心库(parse_eml / fetch_imap / classify / format)
└── tests/
    └── test_e2e.py      # 端到端测试(monkeypatch + 真 subprocess)
```

## 测试

```bash
cd C:/Users/Administrator/oi_enhancements/projects/email_triage
python tests/test_e2e.py
```

跑 5 个 case:help / text / json / md / subprocess。

## 安全注意

- **不要** `--password "明文"`(没有这个参数,故意的)
- 必须 `--password-env ENV_VAR` 从环境变量读
- 环境变量在 shell 设置时也注意 shell_history(`bash -c` 或 readline 历史可能泄漏)

## 资产依赖

- `companion/adapter_registry.py` — 已注册 `email` + `email_conf` adapter(M3.48)
- `companion/classify_email.py` — 单邮件推理入口(被 src/email_triage 复用)
- 本项目 **不修改** `companion/` 下任何代码,只 import
- 模型路径:`D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/`
- Adapter 路径:`D:/prisir-train-assets/trained/{email,email_conf}/adapter/`

## Phase 2(暂不做)

- 与 PrisirAI 整合:调 `process_list_impl` 联动 / 推 PERF_BLACKLIST 事件
- IMAP IDLE 实时模式(现在是一次性 fetch)
- 邮件级 thread 聚合