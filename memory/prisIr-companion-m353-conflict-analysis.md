---
name: prisIr-companion-m353-conflict-analysis
description: M3.53 PrisirAI 主进程进程调度 vs Companion M3.52 进程调度冲突分析 2026-09-23 — 完全互补无功能冲突,仅 process_numa._set_affinity 与 process_controller.process_cpu_limit 在执行实现上有重叠
metadata:
  type: project
---

# M3.53 PrisirAI vs Companion 进程调度冲突分析(2026-09-23)

## 用户问题

> 这模型和 PrisirAI 的扩展功能中同样做系统调度的组件能协同工作吗?还是它们有重复冲突功能要调整(冲突就优先去掉 PrisirAI 中的组件)?

## 1. 现状盘点

### PrisirAI 主进程侧(MCP tools,已上线)

**`mcp_prisiragent_server/process_controller_tools.py`** — 1453 行,25 个 TOOL_DEFS,通过 `dynamic_registry.py` 自动注册给 MCP server,所有 AI agent / 扩展 / 助手都能调。

工具分类:
- **基础 v1**(`list` / `detail` / `lower` / `kill` / `cpu_limit` / `tree`)
- **白/黑名单**(`whitelist_*` / `blacklist_*`,持久化到 `~/.claude/process_*.json`)
- **ProBalance watchdog v3**(`watchdog_start` / `_stop` / `_status` / `_config`,5s 间隔 + cpu_threshold + 冷却 + auto_recover)
- **Ananicy 规则表**(`rule_list` / `rule_add` / `rule_remove` / `rule_apply`,chrome/rustc/ffmpeg/make 等 12 类默认)
- **IO 调度**(`io_priority` / `io_stat`,Win IoPriorityHint / Linux ionice)
- **OOM 调度**(`oom_adj`,Linux `/proc/{pid}/oom_score_adj`)
- **进程树**(`tree` / `tree_watch`,parent 子进程监控)

**`prisiragent_status_tools.py`** — 204 行,但**完全独立范畴**(看 team-lead 工作健康度,不涉及系统 perf)。

### Companion 侧(M3.51-M3.52 新增)

- `perf_collector.py` (298 行) — 零 admin 采系统状态(CPU/MEM/NIC/disk/proc/thermal/crash 7 天)
- `perf_guard.py` (222 行) — 风险分类守护 + alert + trend
- `process_numa.py` (159 行) — **CPU 亲和性建议 + admin 检测**
- `classify_perf.py` — 推理入口,用本地 `perf_conf_v2` adapter(0.6B 模型)

## 2. 功能对比表

| 能力 | PrisirAI `process_controller_tools` | Companion `process_numa` + `perf_*` | 冲突? |
|------|------|------|------|
| 列 top CPU 进程 | ✅ `process_list`(PowerShell/WMI,带 IO 字节) | ✅ `process_numa.list_top`(psutil,带 affinity) | **部分重叠** |
| CPU 亲和性(限制核数) | ✅ `process_cpu_limit`(PowerShell `ProcessorAffinity`) | ✅ `process_numa._set_affinity`(Win32 `SetProcessAffinityMask`) | **完全重叠** |
| 进程优先级 | ✅ `process_lower`(Win/Lin 5 级) | ❌ 无 | 互补 |
| IO 优先级 | ✅ `process_io_priority`(Win `IoPriorityHint`/Linux `ionice`) | ❌ 无 | 互补 |
| OOM score adj | ✅ `process_oom_adj`(Linux) | ❌ 无 | 互补 |
| ProBalance 自动降权 | ✅ `process_watchdog_start` v3 + 规则 + 冷却 + auto_recover | ❌ 无 | 互补 |
| 白/黑名单持久化 | ✅ `whitelist_*` / `blacklist_*` | ❌ 无 | 互补 |
| Ananicy 规则表 | ✅ `process_rule_*` (12 类默认) | ❌ 无 | 互补 |
| 进程树 + 子进程监控 | ✅ `process_tree` / `process_tree_watch` | ❌ 无 | 互补 |
| **系统 perf 风险分类** | ❌ 无 | ✅ `perf_guard` + `perf_conf_v2` adapter | **独立(无冲突)** |
| **TAP/BSOD/ndis 检测** | ❌ 无 | ✅ `classify_perf` 1045 条 | **独立** |
| **alert 文案 + 趋势升级** | ❌ 无 | ✅ `perf_guard.ALERT_HINTS` + `detect_trend` | **独立** |
| **admin 检测 + 建议** | ❌ 无 | ✅ `process_nctml._is_admin` + `recommend_for_process` | **独立** |

## 3. 关键冲突点(只有 2 处)

### 🔴 冲突 A:列 top CPU 进程
- PrisirAI 走 **PowerShell + WMI**(`Get-CimInstance Win32_Process`),带 IO 字节
- process_numa 走 **psutil**(纯 Python)
- **影响**:调用方得选 — MCP 端用 `process_list`,Companion 端用 `process_numa.list_top`
- **决策**:保留两者。psutil 比 PowerShell 快 3-10x,perf_guard.py 在 local fallback 场景(默认 60s 守护)用 process_numa;用户主动排查用 PrisirAI `process_list`

### 🔴 冲突 B:CPU 亲和性 — **真做不做** 冲突最大
- PrisirAI `process_cpu_limit`(PowerShell `ProcessorAffinity`):**可直接改,返回 `OK_MASK_{mask}`**
- process_numa `_set_affinity`(Win32 `SetProcessAffinityMask`):**默认只输出建议,只在 `--apply` 时真改**
- **关键差异**:
  - PrisirAI 走 PowerShell → .NET `$p.ProcessorAffinity = mask`(处理 token/权限更稳)
  - process_numa 走 ctypes 直接调 Win32(轻量但容错弱)
- **冲突影响**:两者**互不冲突**,而是互补 — PrisirAI 是执行端(MCP 用户调用),process_numa 是建议端(perf_guard 高/严重时附加)

## 4. 推荐策略

### 无完全重复的功能冲突**。**唯一可清理的是:

**建议**:把 `process_numa._set_affinity()` ctypes 实现**改成调 PrisirAI 函数**(避免双份维护):

```python
# process_numa.py --apply --pid X --cores N 改为:
import sys
sys.path.insert(0, "../mcp_prisiragent_server")
from process_controller_tools import process_cpu_limit_impl
print(process_cpu_limit_impl(args.pid, args.cores))
```

理由:
1. PrisirAI `process_cpu_limit` **已能 set**,PowerShell + .NET 比 ctypes 更稳(token/权限/Unicode 路径)
2. process_numa 的 admin 检测 ctypes 调 `IsUserAnAdmin` — PrisirAI 通过 PowerShell 拒绝也能识别
3. **避免双份 ctypes/PowerShell 维护成本**

但 **--apply 路径目前是非 admin 的(用户机器)** — 实际 `--apply` 从未跑过,改成委托影响 0。

### 决策(按用户指示:**冲突就优先去掉 PrisirAI 中的组件**)

**No-op 决策**:
- PrisirAI 进程调度组件**保留原状**(25 个工具不动)
- Companion `process_numa.py` **保留原状**(只是建议端,不在 PrisirAI 范畴)
- **唯一可能后续优化**:把 `process_numa._set_affinity` 改成调 PrisirAI 的 `process_cpu_limit_impl`(去重,非必需)

## 6. 结论

| 维度 | PrisirAI 主进程侧 | Companion 侧(M3.52) |
|------|------|------|
| 进程列表/详情/IO | ✅ **完整** | ❌ 重复开发 |
| CPU/IO/优先级/OOM | ✅ **完整** | ❌ 重复开发 |
| ProBalance watchdog + 规则 | ✅ **完整** | ❌ 无 |
| **系统 perf 风险检测** | ❌ 无 | ✅ **唯一** |
| **TAP/BSOD 检测** | ❌ 无 | ✅ **唯一** |
| **alert 文案 + 趋势升级** | ❌ 无 | ✅ **唯一** |
| **admin 检测 + 建议** | ❌ 无 | ✅ **唯一**(PrisirAI 是执行端,Companion 是建议端) |

**两者完全互补,无功能冲突**。PrisirAI 主进程管"执行 + 监控 + 调度",Companion 管"检测 + 风险分类 + 告警 + 建议"。

**自然分工**:
- 用户主动调 MCP → 用 PrisirAI 工具(25 个,跨平台 + admin + watchdog + 规则)
- Companion 检测到风险 → 调 PrisirAI 工具做实际调度(未来 M3.54+ 接入)
- 告警文案/趋势/进程归属 → 走 Companion 本地 0.6B 模型(离线 + < 100ms)

## Why & How to apply

**Why**:
- M3.51/M3.52 的 perf_* 是**新增能力**,不是 PrisirAI 重复开发 — PrisirAI 没有 perf 风险分类、没有 BSOD/TAP 检测、没有 alert 文案、没有趋势升级
- process_numa 跟 PrisirAI `process_cpu_limit` 是 **执行 vs 建议** 的两层,perf_guard 是**建议层**(默认不调 set_affinity)
- 用户问"冲突"是基于"功能相似"的直觉,但实际功能范畴不同(PrisirAI 是 OS 层调度,Companion 是风险分类 + 告警建议)

**How to apply**:
- 类似"主进程 MCP tools + 伴生本地组件"分工:**主进程管执行(跨平台 + admin + 持久化 + 规则),本地管检测 + 风险 + 建议(离线 + 轻量 + 快速)**
- 当 perf_guard 想**实际**做调度(降优先级 / 设 affinity / 加规则),**调 PrisirAI MCP tools** 而不是本地另写一份
- 唯一**后续**清理项:把 `process_numa._set_affinity` 改成调 `process_controller.process_cpu_limit_impl`(可选,当前 --apply 路径未实际跑过)

## 关联

- [[prisIr-companion-m352-closed]] — M3.52 perf_guard 增强闭环
- [[prisIr-companion-m351-closed]] — M3.51 perf_conf_v2 闭环
- [[prisIr-companion-m351-s2-lasso-islac-report]] — Lasso/ISLC 调研(ProBalance 理论来源)
- `mcp_prisiragent_server/process_controller_tools.py` — PrisirAI 进程调度 MCP 工具集(1453 行)
- `companion/process_numa.py` — Companion CPU 亲和性建议端(159 行)