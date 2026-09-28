---
name: prisIr-companion-m352-closed
description: M3.52 perf_guard 增强(S1-S5)闭环 2026-09-23 — process attribution + alert hints + history trend + CPU 亲和性 admin 建议 + e2e 验证
metadata:
  type: project
---

# M3.52 perf_guard 增强闭环(2026-09-23)

## 目标

在 M3.51 perf_conf_v2 闭环后,**把 detection 层的能力落到运维实战**:
- 知道风险归谁(谁在吃 CPU/MEM)
- alert 文案具体可执行(不是空话)
- 识别长期趋势(24h 内 ≥3 次同类)
- admin 启动时给出 CPU 亲和性建议(Process Lasso ProBalance 等价)

## S1-S5 时间线

### S1:process attribution(谁在吃 CPU)
- `perf_collector` 已输出 `proc.top`,`classify_perf._build_text` 读 `sample.proc.top`
- 取 CPU ≥ 5% 的 top 3 → 拼到所有 fallback 文本尾部 `top 进程: chrome(70%), python3(45%), code(20%)`
- 目的:模型分类时能看到 "谁占用资源",区分 high vs medium 时多一个信号

### S2:alert 文案具体化
- `perf_guard.ALERT_HINTS` 字典按 risk 分级:
  - **critical**:禁用 disconnected TAP / 卸 VPN 客户端 / 跑 `!analyze -v` / Driver Verifier exclude
  - **high**:ProBalance / ISLC(standby list 调 1GB)/ 检查 top docker WSL / TAP 禁用
  - **medium**:观察 1-2 分钟 / 检查后台 IO 压力 / VPN 断开测试
- `_hints_for(rec, sample)`:
  - base hints + **TAP 命中检测**(net.nics 里 isup=False 且名带 tap0901/vktap/tapprotonvpn)
  - **M3.52 S4 接入**:high/critical 时调 `process_numa.list_top(threshold=20%)` 自动列 top 3 进程 + 推荐核数 + admin 提示

### S3:history trend(24h 趋势升级)
- 新增 `HISTORY_LOG = perf_history.jsonl`(所有 alert 持久化,7 天滚动)
- `detect_trend(risk, window_h=24, threshold=3)`:
  - 扫 history log,统计同 risk 在 window 小时内出现次数
  - count ≥ threshold → escalate True
- 触发逻辑:`log_alert()` 后跑 detect_trend,若 escalate:
  - 打 `🔥 TREND ESCALATION: {risk} 出现 {count} 次/24h,建议立即处理!`
  - stdout `PERF_TREND <json>` 给前端 ws 抓(toast 升级成"系统故障")
  - rec.trend_escalation = trend

### S4:CPU 亲和性脚本(`process_numa.py`)
- **为什么**:M3.51 S2 Lasso/ISLC 调研后确认 Process Lasso ProBalance 的核心机制 = `SetProcessAffinityMask`
- Windows `SetProcessAffinityMask` 需要 admin,本脚本**只输出建议** + admin 检测
- 关键函数:
  - `_is_admin()`: `ctypes.windll.shell32.IsUserAnAdmin()`
  - `_set_affinity(pid, core_mask)`:admin 才能真设
  - `recommend_for_process(cpu_pct, total_cores=6)`:
    - `≥90%` → `max(1, cores/4)` (25%),例 95% → 1 核 mask=0x1
    - `≥70%` → `max(1, cores/2)` (50%),例 80% → 3 核 mask=0x7
    - `≥40%` → `max(2, int(cores*0.66))` (66%),3 核 mask=0x7
    - else → 全核
  - `list_top(cpu_threshold=5, top_n=5)`:psutil 列 top CPU 进程 + affinity 建议
- CLI:`--list` / `--pid X --cores N` / `--apply --pid X --cores N`(apply 需 admin)
- 测试验证:
  ```
  recommend_for_process(95) → {cores:1, mask:1, reason:...}
  recommend_for_process(80) → {cores:3, mask:7}
  recommend_for_process(55) → {cores:3, mask:7}
  recommend_for_process(30) → {cores:6, mask:63}
  _is_admin() → False(预期,非 admin)
  ```

### S5:aliyun E2E 验证
- 上传 `process_numa.py` + `perf_guard.py` 到 aliyun `/workspace/companion/`
- `run_perf_guard_aliyun.py` 跑三场景:
  1. **正常**(boot 3000s,无 bugcheck):low / keep / conf 0.99 / 12959ms ✓
  2. **高 CPU + TAP**(cpu 92%,3 个 TAP disconnected):high / alert / conf 0.86 + TAP 命中 + ALERT_HINTS[high] ✓
  3. **critical**(boot 18s,bugcheck 5):high / alert / conf 0.94 + TAP + ALERT_HINTS + **process_numa 自动列出 python3 CPU 100% → 2 核 mask 0x3** ✓
- ✅ process_numa 集成生效,aliyun 端能正确列出 top CPU 进程 + 推荐 affinity

## 关键文件改动

| 文件 | 改动 |
|------|--------|
| `companion/process_numa.py` | **新建** ~157 行,CPU affinity 建议 + admin 检测 + CLI |
| `companion/perf_guard.py:_hints_for` | **扩展**:high/critical 时调 process_numa.list_top + admin 提示 |
| `companion/perf_guard.py:detect_trend` | **新建** ~30 行,扫 history 24h 趋势升级 |
| `companion/perf_guard.py:HISTORY_LOG` | 新增持久化路径 `perf_history.jsonl` |
| `companion/classify_perf.py:_build_text` | **S1 扩展** 读 `proc.top` + 拼 fallback 尾部 |

## 关键决策

- **history 7 天滚动 vs 永久**:M3.52 S3 默认扫 24h,文件无限增长,后续考虑加 cleanup(rolling 7d 删除)
- **process_numa threshold 20%**:`_hints_for` 里调 `list_top(threshold=20, top_n=3)` —— 过滤后台 task(模型评估服务/索引),聚焦真负载进程
- **CPU 亲和性只给建议不强制**:`SetProcessAffinityMask` 是单进程设置,需要知道 PID + 进程生命周期,生产场景没人手动跑。建议放告警里让 admin 知情即可
- **trend 升级阈值 3**:≥3 次 / 24h 算系统性问题(避免偶发高频误升级)

## 验证 vs 体验

| 指标 | 期望 | 实测 | 备注 |
|------|------|------|------|
| process_numa 推荐函数 | 全跑 | yes | 95%/80%/55%/30% → 1/3/3/6 核 ✓ |
| is_admin 检测 | bool | False | 非 admin 预期 |
| 三场景 E2E | OK/low + ALERT/high ×2 | ✓ | aliyun T4 验证 |
| process_numa 集成触发 | high/critical 时附 | ✓ | python3 CPU 100% → 2 核 |
| detect_trend 逻辑 | count≥3 → escalate | 单元验证 | 待 24h 长测 |
| ALERT_HINTS 文案可执行 | 是 | ✓ | ProBalance / ISLC / Driver Verifier |

## 不在本次范围

- ❌ 自动加 24h 后清理 history log(后续加 rolling 7d)
- ❌ 前端 toast 集成 PERF_TREND(后端已发 `PERF_TREND <json>` stdout)
- ❌ 替换 Process Lasso / ISLC(perf 是 detection,action 层待 M3.53+)
- ❌ 实际启用 admin 启动 companion(用户当前非 admin)
- ❌ WireGuard 部署替代 VPN(S2 决策层,不在 M3.52 闭环)
- ❌ 扩 perf 数据集到 3000 条 → v3(M3.51 留给 M3.52 的待办,实际暂缓)

## Why & How to apply

**Why**:
- M3.51 闭环只到 "能检测 critical/high",但告警落到运维上没"具体可执行建议",用户得到一个 risk label 还是要自己查 BSOD 故
- M3.52 把 detection 升级成"具体可执行建议"—— TAP 命中 → 禁用 / 高 CPU → ProBalance / 反复出现 → 趋势告警
- CPU 亲和性是 **Process Lasso ProBalance 的简化版**(单进程 affinity),虽然 admin 才能设,但给建议本身对运维有用(知道 top 进程 + 推荐核数)

**How to apply**:
- 类似"检测层 + 告警 + 建议"的组合套路:**ALERT_HINTS 字典 + _hints_for() 根据 sample 字段动态拼接**,比静态字符串有力
- process attribution 进推理文本(M3.52 S1):拼到文本尾部,模型训练时也能学到"X 进程 CPU 70%"→ high 的关联
- history 趋势:**所有 alert 都写一个 log**,detect_trend 复用同一文件,后续可加更多维度(24h/3d/7d)
- CPU affinity **只输出建议不强制**:admin 检测 + 提示,生产场景不要让 perf_guard 自动改 affinity(可能影响业务)
- aliyun 上跑 psutil 没问题(已验证),Windows + Linux 通用

## 关联

- [[prisIr-companion-m351-closed]] — M3.51 perf_conf_v2 闭环(detection 层基础)
- [[prisIr-companion-m351-s1-ndis-evidence]] — BSOD 根因(tap0901.sys 9.0.0.9)
- [[prisIr-companion-m351-s2-lasso-islac-report]] — Lasso/ISLC 调研(process_numa 理论来源)
- [[prisIr-companion-m350-intents-closed]] — M3.50 intents 闭环(本地兜底模式参考)
- [[prisIr-companion-m349-l-closed]] — M3.49 L1-L7 流水(单行 ChatML 推理)
- [[prisIr-companion-m3451-confidence-teacher]] — confidence teacher 模式(adapter 校准)