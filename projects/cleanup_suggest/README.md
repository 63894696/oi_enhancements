# cleanup_suggest (M3.69)

磁盘清理建议器 — 本地 AI (tempfile + disk_cleanup_conf) 双 spec 联合判定,
生成 5 类风险分布,可选自动删除 safe/low(走回收站)。

**Phase 1**: 独立 CLI,单跑可用,无需 PrisirAI。
**Phase 2**: 与 PrisirAI 整合(后续)。

## 功能

- 扫目录 → 递归遍历所有文件
- 双 spec 联合:`tempfile` + `disk_cleanup_conf` 各跑一次,取 **风险更高者**
- 输出 5 类风险分布(safe/low/medium/high/critical)
- 三段式建议:🟢 auto-delete / 🟡 user-confirm / 🔴 skip
- `--auto-apply`:用 **send2trash** 删除(回收站,不永久删)
- `--dry-run`:只跑前 20 个,不出建议
- `--format json|text`:人类可读 / JSON

## 安装

```bash
pip install send2trash   # 已在开发环境装好
# 依赖:Qwen3Guard-Gen-0.6B + 2 个 LoRA adapter(已就位)
#   D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B/
#   D:/prisir-train-assets/trained/disk_cleanup_conf/adapter/
#   D:/prisir-train-assets/trained/tempfile_conf/adapter/
```

无其它外部依赖(`companion/` 在 sys.path 自动加入)。

## 用法

```bash
# 默认扫用户 Temp
python main.py

# 扫指定目录
python main.py --root "D:/downloads"

# 自动应用(删 safe/low),要 --yes 才不卡确认
python main.py --root "D:/Users/Admin/Temp" --auto-apply --yes

# 加 --include-medium 让 medium 也一并删
python main.py --root "D:/Temp" --auto-apply --yes --include-medium

# JSON 输出
python main.py --root "D:/Temp" --format json --output suggestion.json

# dry-run:只跑前 20 个文件,不输出建议
python main.py --root "D:/Temp" --dry-run
python main.py --root "D:/Temp" --dry-run --limit 50
```

## 输出示例

### 文本模式

```
扫描: D:/Users/Administrator/Temp  (1.20 GB / 5234 文件)

[清理分析] tempfile + disk_cleanup_conf 双 spec 判定:

  5 类分布:
    safe       3124 ( 60.0%, 0.300 GB)
    low        1234 ( 24.0%, 0.500 GB)
    medium      654 ( 12.5%, 0.300 GB)  ⚠️
    high        156 (  3.0%, 0.100 GB)  ⚠️
    critical     66 (  1.0%, 0.020 GB)  🔒 保留

  建议:
    🟢 删除 safe + low = 4358 文件,释放 0.80 GB
    🟡 medium 高价值,可清理 0.30 GB(654 文件,需 user 确认)
    🔴 high/critical 跳过 (222 文件,0.120 GB)
```

### JSON 模式

```json
{
  "root": "D:/Temp",
  "scanned_files": 5234,
  "scanned_size_gb": 1.2,
  "parse_fail_count": 12,
  "distribution": {
    "safe": {"count": 3124, "size_gb": 0.3, "pct": 60.0},
    "low": {"count": 1234, "size_gb": 0.5, "pct": 24.0},
    "medium": {"count": 654, "size_gb": 0.3, "pct": 12.5},
    "high": {"count": 156, "size_gb": 0.1, "pct": 3.0},
    "critical": {"count": 66, "size_gb": 0.02, "pct": 1.0}
  },
  "suggestion": {
    "auto_delete_safe_low": {"count": 4358, "size_gb": 0.8},
    "medium_user_confirm": {"count": 654, "size_gb": 0.3},
    "skip_high_critical": {"count": 222, "size_gb": 0.12}
  },
  "applied": false,
  "deleted_files_count": 0,
  "failed_deletes_count": 0,
  "elapsed_sec": 412.7
}
```

## 安全设计

| 场景 | 行为 |
|------|------|
| 不带 `--auto-apply` | 只生成建议,**不删任何文件** |
| `--auto-apply` 不带 `--yes` | 必须 stdin 输入 `y` 才执行 |
| `--auto-apply` 只删 | **safe + low**(默认) |
| `--include-medium` | 二次确认后删 medium |
| `high` / `critical` | **永不删**(无论参数) |
| 删除实现 | `send2trash` → 走系统回收站 |
| 路径不存在 | exit=2 + 中文错误提示 |
| 模型加载失败 | exit=3 + 提示检查 adapter 路径 |

## 项目结构

```
cleanup_suggest/
├── README.md                  本文件
├── main.py                    argparse CLI 入口
├── src/
│   ├── __init__.py
│   └── cleanup_suggest.py     核心:scan + classify + report
└── tests/
    └── test_e2e.py            端到端测试(7 个 case)
```

## 双 spec 联合判定细节

- 每个文件由 **disk_cleanup_conf** 和 **tempfile_conf** 各跑一次
- 两者分别给出 `risk` 等级(safe/low/medium/high/critical)
- 取数值更高的 risk 作为最终 `top_risk`
- 风险高者对应的 confidence / source 标记保留到 FileVerdict

示例:disk_cleanup 给 `low`、tempfile 给 `medium` → 最终 `medium`(tempfile 主导)

## 验收

```bash
cd C:/Users/Administrator/oi_enhancements/projects/cleanup_suggest

python main.py --help                                                # OK
python main.py --root "D:/Temp" --dry-run --limit 50                # OK
python main.py --root "D:/Temp" --format json --output r.json        # OK
python tests/test_e2e.py                                             # 7/7 通过
python main.py --root "D:/Temp" --auto-apply --yes --dry-run       # send2trash 验证
```

## 已知限制

- 速度:每文件 ~2-3s(Qwen3Guard 0.6B 单条推理),5000 文件 ≈ 3-4 小时
- parse_fail:模型偶发复读或不输出 `Safety:`,失败文件标记 `unknown`(默认保留)
- 路径:`--root` 必须本地路径,不支持 UNC 网络盘
- 并发:当前单线程串行,M3.69+ 可考虑批推理