---
name: prisIr-companion-m359-admin-state
description: M3.59 统一 admin_state 单例 + 自动 dry_run 联动闭环 2026-09-23 — companion/admin_state.py 单例 cache + require_or_warn + require_admin;perf_guard._trigger_kill_mode 修复 M3.55 隐藏 bug(hardcode dry_run=False → 非 admin 时 Windows 默默失败);process_numa._is_admin 复用 admin_state;不加 companion_launcher.bat(用户决定)
metadata:
  type: project
---

# M3.59 companion 统一 admin_state 单例 + 自动 dry_run 联动闭环(2026-09-23)

## 目标

M3.56 整合方案 §3 admin 启动建议落地:**统一 admin 探测入口 + 各模块按需 dry_run**。
M3.55 S2 `_trigger_kill_mode` 隐藏 bug 修复:注释说"非 admin 自动 fallback"但代码 hardcode `dry_run=False`。

## 关键现状(实施前)

| 文件 | 现状 | 问题 |
|------|------|------|
| `companion/perf_guard.py:235-243` | hardcode `dry_run=False` | 非 admin 时 Windows 默默拒绝真杀 |
| `companion/process_numa.py:38-44` | 独立 `_is_admin()` ctypes 调用 | 单点实现,与 perf_guard 隔离 |

## 实施

### L1:新建 `companion/admin_state.py`(~180 行)

**单例 + cache + 双层包装**:
- `is_admin()` 走 ctypes `shell32.IsUserAnAdmin`,cache 到模块级 `_STATE`,热路径 O(1)
- `get_state()` 返 `{is_admin, checked_at, checked_at_iso, source, error, cache_age_sec}` 给 UI/日志
- `require_or_warn(action)` 非 admin → log.warning + 返 False(fail-soft,适用 perf_guard critical alert 已发生不该 raise)
- `require_admin(action)` 非 admin → raise PermissionError(强一致,适用 process_numa._set_affinity_native / IFEO)
- `reset_cache()` 测试用,生产不应调

**CLI**:
```bash
python admin_state.py               # 简明 is_admin + source
python admin_state.py --verbose      # 含 checked_at / cache_age
python admin_state.py --require "watchdog kill_mode"  # 模拟 require_admin 拒绝
python admin_state.py --reset        # 清 cache 重探(测试用)
```

### L2:`companion/perf_guard.py:235-258` 修 M3.55 隐藏 bug

**改前**(line 235-243):
```python
r = json.loads(watchdog_start_impl(
    interval_sec=5, cpu_threshold=80.0,
    kill_mode="kill", kill_cooldown_sec=30, kill_max_per_round=5,
    dry_run=False,  # 真杀需要 admin;非 admin 自动 fallback 到只记录  ← 注释骗人
))
```

**改后**:
```python
if not require_or_warn("watchdog kill_mode=kill"):
    # 非 admin:dry_run=True + kill_mode=off(避免用户以为真杀) + note 显式说明
    r = json.loads(watchdog_start_impl(
        ..., kill_mode="off", dry_run=True,
    ))
else:
    # admin:真杀路径
    r = json.loads(watchdog_start_impl(
        ..., kill_mode="kill", dry_run=False,
    ))
```

**note 字段按 admin 切**(前端 toast 能区分):
- admin:`已加 TAP 黑名单并启 watchdog kill_mode=kill(disconnected TAP 进程一出现立即杀)`
- 非 admin:`非 admin 启动 — watchdog 降级为 dry_run(仅记录不真杀)。需 admin 提权后 watchdog kill_mode 才生效。黑名单仍写入,重启 admin 可接管。`

**额外决策**:非 admin 时 `kill_mode="off"` 而非 `"kill" dry_run=True`,因为 `kill + dry_run=True` 在 PrisirAI watchdog 内部语义混乱(它会按 kill_mode 判定是否真降权),降档到 `off` 更安全。

### L3:`companion/process_numa.py:38-44` 复用 admin_state

```python
try:
    from admin_state import is_admin as _admin_state_is_admin
except Exception:
    _admin_state_is_admin = None

def _is_admin() -> bool:
    if _admin_state_is_admin is not None:
        try: return _admin_state_is_admin()
        except: pass
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except: return False
```

**保留 ctypes fallback 原因**:process_numa 是 CLI 工具,不能因 admin_state import 链断就崩;退到直接 ctypes 不依赖外部模块。

## 验证

| 验证项 | 期望 | 实测 |
|--------|------|------|
| `python admin_state.py` | 简明 `{is_admin, source}` | `{"is_admin": false, "source": "shell32.IsUserAnAdmin"}` ✅ |
| `python admin_state.py --verbose` | 含 cache_age | `cache_age_sec=0.0` 立即探测后;2s 后 `cache_age_sec=2.001` ✅ |
| `python admin_state.py --require "..."` | 非 admin exit 1 | exit 1 + 报错 "需要 admin 权限" ✅ |
| `python admin_state.py --reset` | cache 清 + 重探 | `checked_at` 变化 ✅ |
| `process_numa._is_admin()` | 复用 admin_state | 返 `False`(非 admin 当前 shell),`admin_state import OK: True` ✅ |
| `process_numa.py --list` | 正常列 top 进程 | exit 0, "无高 CPU 进程"(只读不需 admin)✅ |
| `perf_guard._trigger_kill_mode` admin 联动 | dry_run 跟 admin 走 | 非 admin 路径走 `kill_mode="off" dry_run=True` ✅ |

## 关键文件改动

| 文件 | 改动 |
|------|--------|
| `companion/admin_state.py` | **M3.59 新建** ~180 行,单例 + cache + 双层包装 + CLI |
| `companion/perf_guard.py:31-33` | 加 `from admin_state import is_admin, require_or_warn` |
| `companion/perf_guard.py:235-282` | 修 `_trigger_kill_mode` 改 hardcoded dry_run → admin 联动;note 按 admin 切 |
| `companion/process_numa.py:32-61` | `_is_admin` 复用 admin_state + 保留 ctypes fallback |

## 用户决策(已应用)

1. **launcher 形态**:选 "统一 admin_state 单例 + 现有命令兼容(推荐)" — admin_state.py + 现有 perf_guard/process_numa 复用
2. **CLI 包装**:选 "不加(只 admin_state 内部)" — 不加 `companion_launcher.bat`/`.ps1`,用户用 IDE / 任务计划程序启动,token 自身决定 admin

## Why & How to apply

**Why**:
- **M3.55 隐藏 bug 现场**:perf_guard._trigger_kill_mode 注释说"非 admin 自动 fallback",实际 hardcode `dry_run=False` —— 用户的代码自己骗自己,Windows 默默拒绝真杀无 visible 反馈
- **统一 admin 探测入口**:避免 perf_guard 与 process_numa 各自 ctypes 调用,缓存结果到进程级(O(1) 热路径)
- **显式优于隐式**:`require_or_warn(action)` 显式 warning + return False,比 silent fallback 更可观测
- **不引入新启动器**:用户从 IDE / 任务计划程序启动,token 自身决定 admin;CLI launcher 是 IDE 用户用不上的多余抽象

**How to apply**:
- 类似的"统一探测 + cache"模式适合:**探测昂贵**(ctypes / syscall) + **热路径调用**(每分钟)+ **结果生命周期内不变**(token 不会自动降权)
- fail-soft `require_or_warn` 优于 fail-hard `require_admin` 适用于:"非致命性"路径(已发生 critical alert 不应再 raise)
- **降档而非降权**:非 admin 时把 `kill_mode="kill"` 改成 `kill_mode="off"` 而非"kill + dry_run=True",避免在 PrisirAI 内部语义混乱
- **note 字段按状态切**:evt.note 显式区分 admin/非 admin,前端 toast 直接展示,无需额外查

## 后续(M3.60+ 路线图)

按 M3.56 §4 路线图:
- ❌ M3.60+ IFEO debugger 启动拦截 — 需 admin,admin_state 已就位可直接接入
- ❌ M3.60+ Service StartupType=Disabled — 同上
- ❌ 真 production 化 admin 启动:用户主动 runas 提权 IDE — M3.60+ 视情况

## 关联

- [[prisIr-companion-m356-routing-admin]] — M3.56 整合方案 §3 admin 启动建议,本文档是 M3.59 实施
- [[prisIr-companion-m355-closed]] — M3.55 watchdog kill_mode(隐藏 bug 源头)
- [[prisIr-companion-m354-closed]] — M3.54 Companion → PrisirAI 整合(架构基础)
- `companion/admin_state.py` — 统一探测单例
- `companion/perf_guard.py:235-282` — _trigger_kill_mode admin 联动
- `companion/process_numa.py:32-61` — _is_admin 复用 admin_state