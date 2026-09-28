---
name: prisIr-companion-m354-closed
description: M3.54 Companion → PrisirAI 整合闭环 2026-09-23 — process_numa._set_affinity 委托 process_cpu_limit_impl;perf_guard._hints_for high/critical 时委托 process_list_impl(带 IO 字节)+ recommend_for_process 本地算法兜底
metadata:
  type: project
---

# M3.54 Companion → PrisirAI 整合闭环(2026-09-23)

## 目标

M3.53 冲突分析结论:**PrisirAI 主进程侧 + Companion 侧完全互补,无功能冲突**。**用户决定"现在就改(整合)"**,做两件事:
1. `process_numa._set_affinity` 委托给 PrisirAI `process_cpu_limit_impl`(去重 ctypes 维护)
2. `perf_guard._hints_for` high/critical 时调 PrisirAI `process_list_impl`(获取 IO 字节更丰富)+ 本地 `recommend_for_process` 兜底

## S1-S3 时间线

### S1:process_numa 委托 process_cpu_limit_impl

- `process_numa.py` 加 sys.path 指向 `../mcp_prisiragent_server`
- `_set_affinity(pid, core_mask)` → 算 cores = `bin(core_mask).count("1")` → 调 `process_controller_tools.process_cpu_limit_impl(pid, cores)`
- 原 ctypes `_set_affinity_native` 保留为 PrisirAI 失败时的 fallback
- 修了 `_set_affinity` main 函数 exit code:apply 失败返 exit=1(原代码返 0,bug)
- 加 `delegated_to` 字段到推荐 dict,标识整合路径

**测试**:
```
python process_numa.py --apply --pid 999999 --cores 3
→ ❌ PrisirAI 失败: {"ok": false, "pid": 999999, "cores": 3, "platform": "Windows"}
→ exit=1 ✓
```

### S2:perf_guard 委托 process_list_impl

- `perf_guard.py:_hints_for` high/critical 时:
  1. sys.path 注入 `../mcp_prisiragent_server`
  2. 调 PrisirAI `process_list_impl("cpu", 10)` 拉 top 10
  3. 过滤 CPU ≥ 20% 取 top 3
  4. 调本地 `process_numa.recommend_for_process(cpu, total_cores)` 给推荐核数
  5. 显示 `[PrisirAI] name (pid) CPU X% / MEM YMB / IO_R ZMB / IO_W WMB → 建议 N 核 (mask ...)`
  6. 提示可调用的 PrisirAI 工具:`process_cpu_limit` / `process_lower` / `process_io_priority` / `process_blacklist_add`
- **异常处理**:PrisirAI 不可用(aliyun 环境没装 process_controller_tools)时,自动 fallback 到本地 `process_numa.list_top`(psutil),带 `[local-fallback]` 标记

**本机测试**(PrisirAI 拉的真实 top):
```
📊 [PrisirAI] msedgewebview2 (pid 17056) CPU 414% / MEM 24MB / IO_R 800.2MB / IO_W 21.6MB → 建议 1 核 (mask 0x1)
📊 [PrisirAI] claude (pid 20468) CPU 358% / MEM 541MB / IO_R 25.8MB / IO_W 2793.3MB → 建议 1 核
📊 [PrisirAI] claude (pid 19432) CPU 305% / MEM 97MB / IO_R 2761.6MB / IO_W 19.3MB → 建议 1 核
💡 调用 PrisirAI process_cpu_limit 真改 affinity
📦 同样可调:process_lower(降优先级) / process_io_priority(限 IO) / process_blacklist_add
```

### S3:aliyun E2E

- 上传 `process_numa.py` + `perf_guard.py` 到 aliyun `/workspace/companion/`
- `run_perf_guard_aliyun.py` 跑三场景:
  1. 正常 → low/keep ✓(无 alert)
  2. 高 CPU+TAP → high/alert + TAP + ALERT_HINTS[high] + **trend escalation 3 次/24h** ✓
  3. critical(boot 18s+5 bugcheck)→ high/alert + TAP + **local-fallback** 列出 python3 100% → 2 核(因为 aliyun 没装 process_controller_tools)✓
- ✅ 整合链路鲁棒:PrisirAI 不在时本地兜底,不破坏 production 守护

## 关键文件改动

| 文件 | 改动 |
|------|--------|
| `companion/process_numa.py` | **M3.54 整合** — sys.path 注入 mcp_prisiragent_server + `_set_affinity` 委托 PrisirAI + 原 ctypes 保留为 native fallback + 修 exit code bug |
| `companion/perf_guard.py:_hints_for` | **M3.54 整合** — high/critical 时调 PrisirAI `process_list_impl`(带 IO 字节)+ recommend_for_process 推核数 + local-fallback 到 psutil |
| `mcp_prisiragent_server/process_controller_tools.py` | **未改**(PrisirAI 侧已完整) |

## 关键决策

- **保留 ctypes native fallback**:PrisirAI 失败时仍能设 affinity(虽然非 admin 一般都失败,但 Linux 上非 admin 的 `taskset` 能跑,跨平台场景保留)
- **PrisirAI 失败自动 fallback**:不抛异常,本地 psutil 兜底(带 `[local-fallback]` 标记)
- **修 exit code bug**:原 `_set_affinity` main 函数 `_set_affinity` 返 False 时 return 0,改成 return 1,正确表达失败

## 验证 vs 体验

| 指标 | 期望 | 实测 | 备注 |
|------|------|------|------|
| PrisirAI process_cpu_limit 委托 | 调通 | ✓ | PrisirAI 返 ok 时返 True |
| _set_affinity exit code | 失败返 1 | ✓ | exit=1 |
| process_list 真实数据 | 含 IO 字节 | ✓ | msedgewebview2 IO_R 800MB |
| aliyun local-fallback | PrisirAI 不在走 psutil | ✓ | python3 100% → 2 核 |
| 高/严重 hint 增强 | 有 IO + 推荐 + 工具提示 | ✓ | 全部出现 |
| 趋势升级 | ≥ 3 次触发 | ✓ | high 3-4 次/24h 升级 |

## 不在本次范围

- ❌ 改 PrisirAI `process_controller_tools.py` 任何东西(已完整)
- ❌ Companion 调度器调 watchdog(下次 M3.55)
- ❌ process_numa 加 进程规则表(Ananicy 风格在 PrisirAI `process_rule_*` 已完整)
- ❌ 自动用 `process_watchdog_start` 启 ProBalance(用户当前非 admin,perf 是 detection 层)

## Why & How to apply

**Why**:
- M3.53 调研后,PrisirAI 已有 25 个完整进程调度工具 — Companion 再写一份是双份维护成本
- 整合后:Companion 检测到高/严重 → **delegate 真实进程数据给 PrisirAI**(带 IO 字节,比 psutil 表现更丰富)→ 给推荐核数 + 工具提示
- 用户不再需要手动从 perf_guard 切换到 PrisirAI 工具,**hints 直接告诉用户有哪些 PrisirAI 工具可用**
- aliyun 等没有 process_controller 的环境,**自动 fallback 到 psutil**(生产链路不中断)

**How to apply**:
- 类似"主进程 MCP tools + 本地兜底组件"的整合模式:**执行走 PrisirAI,检测走本地,fallback 保留**
- 整合时一定保留 **native fallback**,PrisirAI 不可用时本地仍可用
- sys.path 注入用 `Path(__file__).resolve().parent.parent / "mcp_prisiragent_server"`,**避免相对路径错误**
- 异常处理要打印 `[local-fallback]` 标记,运维一眼看出走了降级路径

## 关联

- [[prisIr-companion-m353-conflict-analysis]] — 冲突分析(决策依据)
- [[prisIr-companion-m352-closed]] — M3.52 perf_guard 增强(S4/S5 原始版本)
- [[prisIr-companion-m351-closed]] — M3.51 perf_conf_v2 检测能力
- `mcp_prisiragent_server/process_controller_tools.py` — PrisirAI 进程调度 25 个工具(被 Companion 调用)
- `companion/process_numa.py` — M3.54 整合后的本地入口
- `companion/perf_guard.py:_hints_for` — M3.54 整合后的 alert 文案生成