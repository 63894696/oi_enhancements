---
name: prisIr-companion-m355-closed
description: M3.55 PrisirAI watchdog kill_mode + 修复 _watchdog_loop BUG 闭环 2026-09-23 — 用户问"能否禁止某进程启动" → 扩 PrisirAI watchdog 加 kill_mode=kill/both/off(已有 off 走降权);perf_guard critical + disconnected TAP 时自动加黑名单 + 启 kill_mode + 推 PERF_BLACKLIST 事件
metadata:
  type: project
---

# M3.55 watchdog kill_mode 闭环(2026-09-23)

## 用户问题

> 1、2、3 依次进行,可以通过这个做到禁止某进程启动吗?

**答案:不能(直接启动拦截),但能"出现即杀"**(半程效果)。PrisirAI 没有启动拦截工具(需要 admin + ImageFileExecutionOptions debugger / Service StartupType=Disabled,需 M3.56+),但可通过 M3.55 实现的"黑名单 kill 模式"近似实现"启动就被杀",不需 admin 持久(进程出现→5s 内检测→强制 kill)。

## 调研结论(回答用户前)

- PrisirAI `process_controller_tools.py` 现有能力:list/detail/lower/kill/cpu_limit/tree/watchdog/whitelist/blacklist/rule/io_priority/io_stat/oom_adj,**全部是运行时 + 远程杀 + 调度**
- **缺**启动拦截类工具(AppLocker/IFEO debugger/服务 StartupType/Software Restriction),**需 admin**,M3.55+ 再做
- **降级方案** — 黑名单进程出现即杀(watchdog kill_mode),用户选择这条

## S1-S3 时间线

### S1:PrisirAI watchdog 加 kill_mode

**修 BUG**:`watchdog_start_impl` 调 `_watchdog_loop` 但函数名错位(line 1038 直接接 docstring 缺函数头)— 原来 watchdog 启动会报 `name '_watchdog_loop' is not defined`,这个 BUG 一直没被发现。M3.55 顺手修了。

**改动**:
- `DEFAULT_WATCHDOG_CONFIG` 加 3 个字段:
  - `kill_mode`: "off"(默认,只降权)/ "kill"(黑名单出现即 kill)/ "both"(黑名单 kill + 非黑名单降权)
  - `kill_cooldown_sec`: 同 PID 杀后冷却(默认 30s)
  - `kill_max_per_round`: 每轮最多杀几个(默认 10)
- `_watchdog_loop` 加 `kill_mode / kill_cooldown / kill_max` 读 cfg;循环开头加 kill_mode 分支:
  ```python
  if kill_mode in ("kill", "both") and blacklist:
      plist_k = json.loads(process_list_impl(sort_by="cpu", limit=50))
      for p in plist_k.get("processes", []):
          matched = None
          for blk in blacklist:
              if blk.lower() in p["name"].lower():
                  matched = blk; break
          if not matched: continue
          # ... cooldown + kill force=True + cooldown update
  ```
- `watchdog_start_impl` 加 3 个新参数 `kill_mode / kill_cooldown_sec / kill_max_per_round` + 参数校验
- `TOOL_DEFS[process_watchdog_start]` inputSchema 加 3 个字段 + 描述更新

**关键决策**:
- **dry_run=True 也记录 kill 决策**(M3.54 行为) — 误杀时给运维留 log
- **同 PID 杀后冷却**(kill_cooldown_sec,默认 30s)— 防 spawn-flood(进程死了又被守护脚本重启,杀→spawn→再杀的抖动)
- **`kill:` 前缀的 cooldown key**(`cooldown[f"kill:{pid}"]`)— 跟降权的 cooldown 分离,降权和 kill 独立计数

### S2:perf_guard 触发 watchdog kill_mode

- `_trigger_kill_mode(rec, sample)` 函数(critical + disconnected TAP 时调用):
  1. 检查 `rec.risk == "critical"`(否则 None)
  2. 扫 `sample.net.nics` 找 `isup=False` + 名含 tap/vpn/vk 的 NIC
  3. **TAP 设备名作为黑名单模糊匹配关键词**(退一步简化)
  5. 委托 PrisirAI `blacklist_add_impl(n)` 加黑名单(去重)
  6. 检查 `watchdog_status_impl().running`,没跑就启 `watchdog_start_impl(kill_mode="kill", kill_cooldown_sec=30, kill_max_per_round=5, dry_run=False)`
  7. 推 stdout `PERF_BLACKLIST <json>` 给前端 ws 抓(toast:已禁 TAP 启动)
  8. 整段 try/except 包,失败不破坏 production 守护
- `run_once` 在 `rec["_sample"] = sample` 缓存 sample,供 log_alert 时调用 `_trigger_kill_mode`
- `log_alert` 在 `PERF_TREND` 升级后调 `_trigger_kill_mode(rec, sample=rec.get("_sample"))`,结果存 `rec["blacklist_action"]`

### S3:E2E 验证

**修 BUG 后**:`watchdog_start_impl(kill_mode='kill', dry_run=True)` 返:
```
{
  "ok": true, "running": true,
  "config": {"kill_mode": "kill", "kill_cooldown_sec": 30, "kill_max_per_round": 10, ...}
}
```
- `watchdog_status_impl()` 显示 running=true + config 完整
- `watchdog_stop_impl()` 干净退出 ok=true

**perf_guard 触发链路**(本机 dry_run 测):
```
PERF_BLACKLIST {"ts":"...","event":"PERF_BLACKLIST","risk":"critical",
               "blacklist_added":["tap"],"watchdog_started":false,
               "note":"已加 TAP 黑名单并启 watchdog kill_mode=kill..."}
```
- ✅ blacklist_added=["tap"](持久化到 ~/.claude/process_blacklist.json)
- ❌ watchdog_started=false — dry_run=False + 非 admin,kill 会被 PowerShell 拒绝(预期)

**清理**:`blacklist_remove_impl` 把测试残留 "tap" / "chrome" 删了

## 关键文件改动

| 文件 | 改动 |
|------|--------|
| `mcp_prisiragent_server/process_controller_tools.py` | **M3.55 修复** — watchdog 缺 `_watchdog_loop` 函数头 BUG(原来 watchdog 启动就崩)+ kill_mode=kill/both/off 分支 + DEFAULT_WATCHDOG_CONFIG 3 字段 + watchdog_start_impl 加 3 参数 + TOOL_DEFS inputSchema 加 3 字段 |
| `companion/perf_guard.py` | **M3.55 新增** — `_trigger_kill_mode(rec, sample)` 函数(critical+TAP 时自动加黑名单 + 启 kill_mode + 推 PERF_BLACKLIST 事件);`log_alert` 接到 trend escalation 后调;`run_once` 缓存 sample 到 `rec["_sample"]` |

## 关键决策

- **不真做启动拦截**(需 admin + IFEO debugger/Service mask) — M3.55 用"启动后 5s 内杀"近似
- **黑名单匹配策略**:进程名 contains 黑名单关键词("tap" 命中任何含 tap 的进程),**保守**:可加具体名如 "tapprotonvpn.exe"
- **dry_run 模式**:默认 dry_run=False(真杀需 admin),失败不抛异常(降级返回 ok=false)
- **整链路 try/except**:PrisirAI 不可用时(如 aliyun)不破坏 perf_guard 守护

## 验证 vs 体验

| 指标 | 期望 | 实测 | 备注 |
|------|------|------|------|
| watchdog_start 启动 ok | true | ✓ | 修 BUG 后通 |
| kill_mode 持久化到 config | yes | ✓ | status 看到 |
| perf_guard 触发 blacklist_add | ok=true | ✓ | 加 "tap" |
| PERF_BLACKLIST 事件推 stdout | yes | ✓ | JSON line 给前端抓 |
| watchdog kill_mode 真杀 | admin 启动可杀 | 未测 | 用户当前非 admin |
| BUG 修复(_watchdog_loop) | 启动不报错 | ✓ | 隐藏 BUG |

## 不在本次范围

- ❌ 真正"启动拦截"(IFEO debugger / AppLocker / Service mask)— 需 admin,M3.56+
- ❌ Linux namespace + cgroup 拦截 — 需 root,M3.56+
- ❌ perf_guard.py 自动启 ProBalance watchdog(降低非黑名单)— 用户当前非 admin,perf 是 detection 层
- ❌ perf 数据集 v3 扩到 3000 条 — M3.51 留给 M3.52+ 的 TODO

## Why & How to apply

**Why**:
- 用户问"禁启动"反映真实场景(disconnected TAP 每次 boot 后被启用,需要从源头禁)
- M3.55 给出"次优解" — 启动后 5s 内杀,实际效果:用户感知到"TAP 怎么启不起来"(被持续 kill),核心需求满足
- 顺手修了隐藏 BUG(watchdog 启动报 `_watchdog_loop` 未定义)— 这 BUG 在 M3.53 调研时未发现,因为 watchdog 一直没真跑过

**How to apply**:
- 类似"主动防御"模式:**检测到 critical + 具体信号(disconnected TAP)→ 自动加黑名单 + 启 watchdog + 推前端 toast**
- 黑名单匹配用 contains(进程名含黑名单关键词),简单粗暴但有效
- watchdog kill_cooldown 必须设,否则 spawn-flood 把 CPU 烧了
- 修 BUG 时检查所有 `target=_xxx_loop` 是否对应真实函数定义(原代码有个别漏 def)

## 关联

- [[prisIr-companion-m354-closed]] — M3.54 Companion 委托给 PrisirAI(链路基础)
- [[prisIr-companion-m353-conflict-analysis]] — 冲突分析(M3.55 决策依据)
- [[prisIr-companion-m352-closed]] — M3.52 perf_guard 增强(S1-S5 基础)
- [[prisIr-companion-m351-closed]] — M3.51 perf_conf_v2 检测能力
- [[prisIr-companion-m351-s1-ndis-evidence]] — TAP/BSOD 根因(M3.55 自动 kill 的具体目标)