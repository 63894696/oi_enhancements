# commit_check — M3.69 Git pre-commit 风险扫描(本地 AI)

**Phase 1**: 单跑可用 CLI。**Phase 2**: 与 PrisirAI 整合(未做)。

用本地 0.6B Qwen3Guard LoRA 双 spec 联合判,扫 git staged / committed diff,
自动 deny 包含密钥/危险扩展/destructive 操作的 commit。

---

## 资产依赖(只读)

- `companion/adapter_registry.py` — 16 个已训 adapter
- `companion/classify_disk_cleanup.py` — disk_cleanup_conf 入口
- `companion/classify_task_local.py` — task_conf 入口
- `D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/` — base model
- `D:/prisir-train-assets/trained/{disk_cleanup_conf,task_conf}/adapter/` — LoRA

本项目**不修改** companion/ 任何代码。

---

## 安装

无需安装,纯 Python + 已存在 LoRA。环境假设:

- Python 3.10+
- `pip install torch transformers peft` (companion 的依赖)
- 适配器已训好(路径如上)

---

## 用法

```bash
# 默认扫 staged
python main.py --staged

# 扫上次 commit
python main.py --diff HEAD

# 扫任意 commit
python main.py --diff HEAD~3

# 自定义危险扩展名
python main.py --staged --dangerous-ext .pem,.key,.pfx,.csv

# 输出 JSON
python main.py --staged --format json

# 输出 Markdown 报告
python main.py --staged --format md --output report_$(date +%Y%m%d).md
```

---

## Git hook 集成

```bash
# 装到项目 hooks/
cat > .git/hooks/pre-commit <<'EOF'
#!/bin/bash
python /path/to/commit_check/main.py --staged
EOF
chmod +x .git/hooks/pre-commit
```

deny 时退出码 1 → git commit 自动中断。

---

## 决策规则

| 条件 | 决策 | 退出码 |
|------|------|--------|
| 任一 spec 输出 `critical` | `deny` | 1 |
| disk_cleanup `high` + 任意文件被删除 | `deny` | 1 |
| 任一 spec 输出 `medium` | `ask` | 0 |
| disk_cleanup `high`(无删除) | `ask` | 0 |
| 仅 `low` / `safe` | `allow` | 0 |

> `ask` 退出码 0(只在文本提示),不让 git hook 卡住 — 用户自己看报告决定。

---

## 输出示例

```
[diff summary] 变更 12 文件 (+234 -56)

[disk_cleanup_conf] 检查变更路径:
  src/utils.py                                              safe
  src/main.py                                               low
  config/secrets.yml                                        critical  [dangerous]
  scripts/cleanup.sh                                        high     [dangerous]

[task_conf] 分析变更内容:
  → task_type=code_call risk=safe

→ 决策: deny
   - 路径 critical 风险: config/secrets.yml
→ 建议: git reset HEAD config/secrets.yml  # 暂存撤回
# 然后: 加密 / 移到 .gitignore / 用 secret manager 替代
```

---

## 测试

```bash
python tests/test_e2e.py
```

8 个 e2e case,真 subprocess 跑 main.py:

1. `--help` 输出完整
2. 非 git 目录友好降级
3. `.pem` 文件触发 deny
4. 普通代码 allow
5. `--format json` 输出合法 JSON
6. `--format md --output` 写出文件
7. `--diff HEAD` / `HEAD~1` 跑通
8. `--dangerous-ext` 自定义扩展名

---

## 项目结构

```
commit_check/
├── README.md
├── main.py                       # CLI 入口(argparse)
├── src/
│   ├── __init__.py
│   └── commit_check.py           # 核心库:diff 解析 + 双 spec + 决策
└── tests/
    └── test_e2e.py               # 端到端测试(8 case)
```

---

## 不做的事(显式边界)

- ❌ 不写 unit tests(只写 e2e)
- ❌ 不做 Web UI
- ❌ 不调外部 API
- ❌ 不修改 companion/ 任何代码
- ❌ 不重训 LoRA
- ❌ 不与 PrisirAI 整合(Phase 2)
- ❌ 不自动改 git(只 print 建议)
