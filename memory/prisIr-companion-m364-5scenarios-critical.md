---
name: prisIr-companion-m364-5scenarios-critical
description: M3.64 5 scenario critical 专项 + M3.66 L3 重启 + Obsidian 验证(2026-09-23,本地完成 + aliyun 阻塞)
metadata:
  type: project
---

# M3.64 + M3.66 L3 + Obsidian 验证(2026-09-23)

## 整体状态

| 块 | 计划 | 实际 | 备注 |
|----|------|------|------|
| **A Obsidian** | 修 fcontent_root + index + 实测 | ✅ 全部完成 | 路径从 D:/Temp/p14_test_v3(死) → C:/Users/Administrator/Documents/ObsidianVault(真 vault) |
| **B1 4 个 baseline** | 跑 4 个 data_prep_*.py → 400 条/场景 | ✅ 全部完成 | disk_cleanup=203 / tempfile=290 / email=400 / log=400 |
| **B2 4 个 v3_critical.py** | 写 4 个脚本 + 产出 v3.jsonl + v3_conf.jsonl | ✅ 全部完成 | 4 个 v3.jsonl(280-480 条/场景,critical 23-39%) + 4 个 v3_conf.jsonl |
| **B3 aliyun 训 4 v3_conf** | aliyun T4 训 disk_cleanup/tempfile/email/log_conf_v3 | ❌ **aliyun 阻塞** | 192.220.14.165:49108 实例 `/workspace` 被回收 / 无 nvidia,等用户手动重启 |
| **C 8 head 全量训** | aliyun T4 训 8 个 AgentJev head | ❌ **aliyun 阻塞** | 同上,等 T4 恢复后跑 |

## 做了什么(本地交付)

### 块 A — Obsidian 验证(M3.46 P14 增量入库链路复活)

**问题根因**:
- `companion/companion_asr_settings.json:4` 原值 `fcontent_root = "D:/Temp/p14_test_v3"`
- 该路径 **不存在**(M3.62 期间 aliyun 测试用临时目录,本机未建)
- 之前 2 个老 md(Sep 22) 写在真 vault `~/Documents/ObsidianVault/_incremental/`,但
  `_p14_index.json` 在死路径下 → `p14_ingest.load_index()` 永远返空 dict → 后续段
  被全当"新段"重写,ai_done 自动写盘实际不生效

**修复**:
- 改 `fcontent_root` → `C:/Users/Administrator/Documents/ObsidianVault`(真 Obsidian vault)
- 写 `companion/_rebuild_p14_index.py`(~80 行):扫真 vault `_incremental/*.md`,抽
  `(ts, sha16)` 块 → 写 root `_p14_index.json`
- 跑后 `load_index()` 返 10 entries(2 老 md 各 5 段),`stats()` 确认 10/2
- 写 `companion/_test_obsidian_ingest.py`(~60 行):用 fake_eval(免去 companion_jev
  阻塞)实测 → `evaluate_and_ingest` reason=ok,新增 5 段到 `M3_66_L3_重启_Obsidian.md`,
  index 10 → 15 entries

**E2E 验证**:
```bash
ls ~/Documents/ObsidianVault/_incremental/   # 3 files (2 老 + 1 新)
ls ~/Documents/ObsidianVault/_p14_index.json # 15 entries
```

### 块 B1 — 4 个 scenario baseline 训练集落本地 data/

**关键发现**:data_prep_*.py 都支持 `--output xxx.jsonl`,但**本地 `data/` 从没跑过** — 之前
adapter 全是 aliyun 上现跑现训。**M3.64 必须在本地有 baseline jsonl** 才能叠加 v3_critical。

| 场景 | baseline jsonl | safe/low/medium/high/critical |
|------|---------------|-------------------------------|
| disk_cleanup | 203 条 | 3/80/80/10/**30** (15%) |
| tempfile | 290 条 | 30/80/80/80/**20** (7%) |
| email | 400 条 | 70/98/126/73/**33** (8%) |
| log | 400 条 | 62/95/120/84/**39** (10%) |

critical 占比 7-15% —— **正好命中 v3_critical 增强的目标**(0.6B 模型训不出 abstract critical 模式)。

### 块 B2 — 4 个 data_prep_*_v3_critical.py + 8 个 v3 jsonl

照抄 `data_prep_perf_v3_critical.py` 模板,4 个新脚本各 ~150 行,关键组件:
- `RISK_TO_ACTION` 5 类映射(safe/low → delete/archive/drop,critical → keep/alert)
- `RISK_CONF_RANGE` 5 类 conf 范围(critical 0.90-0.98, high 0.70-0.85 等,同 perf v3)
- `_add_action_and_conf(sample, rng)` helper(补 _action + 派生 _conf)
- `ABSTRACT_CRITICAL_PATTERNS` 列表:每个 scenario 50-80 条场景专属 abstract critical 模板

**scenario-specific abstract critical pattern**:
- **disk_cleanup**:WinSxS Backup 关键 manifest/mum、CatRoot 系统签名、System Restore .sys.backup
- **tempfile**:密钥/证书文件(.pem/.key/.pfx)在常见误报位置(node_modules/dist/build)、dotenv/SSH/GPG 密钥
- **email**:银行/支付/密码重置/账户冻结钓鱼(发件人+主题+正文+链接模板)
- **log**:Windows BugCheck 0x3b/0x124/0x7e + 关键驱动崩溃(ndis/tap0901/tcpip) + 应用层 Traceback

**v3 训练集落盘**:

| 场景 | v3.jsonl | v3_conf.jsonl | critical 占比 |
|------|----------|---------------|---------------|
| disk_cleanup | 283 | 283 | **38.9%** ⚠️ |
| tempfile | 370 | 370 | 27.0% |
| email | 480 | 480 | 23.5% |
| log | 480 | 480 | 24.8% |

**⚠️ disk_cleanup 38.9% 偏高**:可能让模型过拟合 critical。如果 aliyun 训出后 disk_cleanup
high/medium ACC 下降,需 `python data_prep_disk_cleanup_v3_critical.py --n-abstract 40`
重生(降到 ~25%)再训。

## 没做什么(等用户重启 aliyun T4)

### 块 B3 — 4 个 v3_conf adapter 训

阻塞:**aliyun 实例 192.220.14.165:49108 `/workspace` 被回收,无 nvidia-smi**(M3.66 L7-A
策略 A 当时还在用,可能 aliyun 自动回收了临时盘)。

**恢复后执行**:
1. 重启新加坡 region ECS gn5i-c2g1.2xlarge(¥8.5/h),挂 /workspace
2. `rsync -avz -e 'ssh -p 49108' companion/data/data_*_v3_conf.jsonl root@<新IP>:/workspace/companion/data/`
3. 4 次 `python train_step1.py --data data_*_v3_conf.jsonl --schema <scenario> --epochs 4 --base-model Qwen/Qwen3Guard-Gen-0.6B --output outputs/qwen3guard-<scenario>-conf-v3`
4. 4 个 adapter 下载到 `D:/prisir-train-assets/trained/<scenario>_conf_v3/adapter/`
5. bench_*.py 跑 v1 vs v3 对比,预期 critical ACC +20pp

### 块 C — 8 个 AgentJev head 全量训(M3.66 L3 重启)

阻塞同上。

**恢复后执行**:
1. 复用 B2 产出的 5 个 v3.jsonl + 现存的 `data_intents.jsonl` / `data_task.jsonl` /
   `safety` 手造 jsonl → 转 AgentJev schema(`data_prep_classification_head.py` 扩 8 scenario)
2. 8 个 `configs/<scenario>_v3.yaml`(每个 500 steps,LLRD + brier 0.1)
3. 8 × ~10 min 串行训,head-only 9.5MB × 8 下载本地
4. `bench_all_agentjev.py` 跑 8 scenario ACC + 三组对比表

## 关键文件改动清单

### A(Obsidian)
| 文件 | 改动 |
|------|--------|
| `companion/companion_asr_settings.json:4` | **改** fcontent_root 真 vault |
| `companion/_rebuild_p14_index.py` | **新建** ~80 行 |
| `companion/_test_obsidian_ingest.py` | **新建** ~60 行测试 |
| `~/Documents/ObsidianVault/_p14_index.json` | **新建** 10 entries |
| `~/Documents/ObsidianVault/_incremental/M3_66_L3_重启_Obsidian.md` | **新增** 5 段 |

### B(M3.64 本地交付)
| 文件 | 改动 |
|------|--------|
| `companion/data/data_disk_cleanup.jsonl` | **新建** baseline 203 |
| `companion/data/data_tempfile.jsonl` | **新建** baseline 290 |
| `companion/data/data_email.jsonl` | **新建** baseline 400 |
| `companion/data/data_log.jsonl` | **新建** baseline 400 |
| `companion/data_prep_disk_cleanup_v3_critical.py` | **新建** ~150 行 |
| `companion/data_prep_tempfile_v3_critical.py` | **新建** ~150 行 |
| `companion/data_prep_email_v3_critical.py` | **新建** ~150 行(2 次重写 f-string helper) |
| `companion/data_prep_log_v3_critical.py` | **新建** ~150 行 |
| `companion/data/data_*_v3.jsonl` × 4 | **新建** 280-480 条 |
| `companion/data/data_*_v3_conf.jsonl` × 4 | **新建** 280-480 条 |

### B3 / C(待办,等 aliyun)
- 4 个 v3_conf adapter 训 + bench + 部署
- 8 个 AgentJev head 训 + bench + 决策

## 风险与经验

### ✅ 模板复用节省 ~50% 时间
- 4 个新脚本 95% 内容照抄 perf v3,只改 ABSTRACT_CRITICAL_PATTERNS
- "scenario-specific critical pattern"是这次工作的核心 — perf 的 boot_bugcheck/tap0901
  模板可以**直接启发** log(BugCheck + tap0901)、tempfile(.pem 在 dist)、email(钓鱼模板)

### ⚠️ helper 引用顺序坑
- `data_prep_email_v3_critical.py` 第一次跑 `NameError: rng_choice_days` — Python
  解析时第一个 f-string 已调用 helper,但 helper 定义在第二个 pattern 之后
- 修法:把 `_days_str` / `_six_digit` 等 inline helper 移到 ABSTRACT_CRITICAL_PATTERNS
  **之前**

### ⚠️ aliyun /workspace 被回收是常见坑
- M3.45 起一直保留 192.220.14.165:49108 实例,Sep 22 还在用,Sep 23 已空
- 后续每次 session 起手应 `ssh <ip> "ls /workspace 2>&1"` 验证(避免到 B3 才发现)

### ⚠️ disk_cleanup 38.9% 偏高
- n-abstract=80 太激进,WinSxS manifest + Backup.cat 模板 + 还原点 = 多场景堆叠
- 训后必查 high/medium ACC 退化,必要时 `--n-abstract 40` 重生

## 关键参考

- `memory/prisIr-companion-m362-perf-conf-v3-closed.md` — perf v3 闭环模板
- `memory/prisIr-companion-m366-classification-head-strategy-a-closed.md` — L7-A 决策
- `companion/p14_ingest.py:55-57` — `index_path()` schema(root 下 _p14_index.json)
- `companion/companion_asr_settings.json:4` — fcontent_root 当前指向真 vault
- `~/Documents/ObsidianVault/_incremental/` — 真 vault 目标目录
- `companion/data_prep_perf_v3_critical.py` — v3_critical 模板参考