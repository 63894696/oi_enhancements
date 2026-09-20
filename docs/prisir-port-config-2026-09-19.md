# PrisirAI 端口配置设计(2026-09-19)

## 背景

PrisirAI 现在有 4 个进程级联:

| 模块 | 端口来源 | 现状 |
|---|---|---|
| PrisirAI 对话主面板 (`prisIragent_web.py`) | env `PRISIRAGENT_WEB_PORT` → `--port` CLI → 默认 `18802` | 写死 |
| 语伴 (`companion/prisiragent-companion-web.py`) | `--port` CLI → 默认 `18850` | 写死 |
| 音乐 web (`companion/prisiragent-music-web.py`) | `bind(0)` 动态 + HKCU 注册表 | 已做对 |
| 日历 (`prisIr_calendar/`) | 无独立端口,内嵌 | — |

**冲突热点:** 主面板 `18802` + 语伴 `18850` 写死,可能跟同机其他软件(Clash Dashboard / VS Code Live Server / NAS 控制台等)撞端口。

## 核心设计决策(2026-09-19 用户拍板)

### 1. 不做 UI
理由:
  - 自动拿空闲端口 → 用户根本无感,不存在手动配端口的诉求
  - Windows 防火墙首次网络访问会弹询问,不需要用户预先知道端口
  - UI 暴露一个用户从来用不上的字段 = 噪声,反而增加培训成本

### 2. 端口存哪里
**统一 HKCU 注册表 + JSON fallback**(沿用 `companion/music/port_registry.py` 既有基建):

| HKCU 路径 | 类型 | 默认 |
|---|---|---|
| `Software\PrisirAI\web_port` | DWORD | `18802`(主面板) |
| `Software\PrisirAI\companion_port` | DWORD | `18850`(语伴) |
| `Software\PrisirAI\music_port` | DWORD | 已存在(音乐 web 动态写) |

平台 fallback:<br>
非 Win / 注册表无权限 → `<workdir>/_prisir_registry/ports.json`(沿用 `port_registry.py` 已有目录)

### 3. 启动优先级

`--port CLI > env (PRISIRAGENT_WEB_PORT) > 用户设置(HKCU/JSON) > 模块默认`

注:env 名字沿用现有的双 fallback `PRISIRAGENT_WEB_PORT` / `OIAGENT_WEB_PORT`(M3.34 commit `9b8799c` 已加)。

### 4. 冲突处理

**自动 fallback + 一次性 toast**:
  - 启动时拿到候选端口 → `bind(0)` 真 listen 试占用 → 成功就用,失败则 OS 已经分配了新端口
  - 启动成功后**写回注册表**(同步),下次 Tauri 壳启动读到新值,直接 spawn 新端口
  - 旧值与新值不同时,UI 顶部 toast 提示「端口 18802 被占,改用 18811」(一次性,不骚扰)

音乐 web 已经是这个流程,只是把机制抽到公共模块。

### 5. Tauri 壳拿到端口的方式

`prisirai-shell.exe` 启动时:
  1. 读 HKCU `Software\PrisirAI\web_port`(默认 `18802`)
  2. spawn `PrisirAI.exe --port <n>`(沿用现有 `--port` CLI 参数,只是从注册表读)
  3. 监听 stdout sentinel `[prisIragent_web] PRISIR_WEB_READY port=<n>`(已存在,2026-09-15 加),拿到真端口(含 fallback 后的)
  4. 启动 WebView 指向 `http://127.0.0.1:<n>/`

## 实现路径(派单用)

### 新建 `port_config.py`(公共模块,所有进程用)

放 `companion/music/port_config.py`(与 `port_registry.py` 同目录):

```python
# port_config.py — PrisirAI 全栈端口配置(2026-09-19)
# 统一 HKCU 注册表 + JSON fallback,跟 port_registry.py 一样三通道:
#   1) HKCU\Software\PrisirAI\<name>_port (主)
#   2) <workdir>/_prisir_registry/ports.json (Win 兜底)
#   3) 模块默认(代码内置)

DEFAULT_WEB_PORT = 18802       # 主面板
DEFAULT_COMPANION_PORT = 18850 # 语伴

def read_port(name: str, default: int) -> int:
    """读用户配置的端口;HKCU → JSON fallback → 默认。"""
def write_port(name: str, port: int) -> bool:
    """写用户配置的端口(HKCU + JSON 双写)。"""
def pick_free_port(prefer: int) -> int:
    """优先用 prefer;若占用则 bind(0) 拿空闲端口。"""
def resolve_start_port(name: str, env_value: str | None, cli_value: int | None, default: int) -> int:
    """合并 CLI > env > 用户设置 > 默认。"""
def notify_port_changed(name: str, old: int, new: int) -> None:
    """写回注册表 + stderr 日志('port 18802 changed to 18811 due to conflict')。
    UI 端由前端 fetch /api/port_status 时拿到 new 与 old 差值,弹一次性 toast。"""
```

### 改三个 boot 点

| 文件 | 改动 |
|---|---|
| `prisIragent_web.py:10917 main()` | 用 `resolve_start_port('web', env.PRISIRAGENT_WEB_PORT, args.port, DEFAULT_WEB_PORT)`,启动后 `srv.server_address[1]` 真端口 → `write_port('web', real_port)` |
| `companion/prisiragent-companion-web.py:2399 main()` | 同上,`name='companion'`,env `PRISIRAGENT_COMPANION_PORT`,default `DEFAULT_COMPANION_PORT` |
| `companion/prisiragent-music-web.py` | 已用 `port_registry.py`,改成调 `port_config.write_port('music', real_port)`(沿用 `port_registry.write_music_port` 逻辑,只换 key 名让所有端口走同一个文件) |
| `prisIragent_web.py` 新增 `/api/port_status` | 返 `{web: {configured: 18802, actual: 18811, changed: true}, companion: {...}}`,前端用 `changed=true` 弹一次性 toast |

### 改 Tauri 壳(`prisIragent-tauri/`)

`prisiragent-tauri/src-tauri/src/main.rs`(或对应 boot 文件):
  - 启动时调 `read_hkcu("web_port", 18802)` 拿候选端口
  - spawn `PrisirAI.exe --port <n>`
  - 监听 stdout sentinel,拿到真端口(可能因冲突 fallback 过)
  - WebView 加载 `http://127.0.0.1:<real_port>/`

### 不做的事
  - ❌ 设置面板 UI(用户拍板不做)
  - ❌ 新建独立配置文件(沿用既有 `_prisir_registry/` 目录)
  - ❌ 改启动脚本 / NSIS 装包参数(用户改端口根本不需要装包步骤)

## 兼容性

| 旧行为 | 新行为 |
|---|---|
| env `PRISIRAGENT_WEB_PORT=18802` | 优先级最高,直接用 |
| `--port 18803` | 最高,直接用 |
| 都没设 → 用注册表 → 用 18802 | 同左 |
| 主面板启动后端口变化 | 写回注册表 + UI toast |
| 音乐 web 旧注册表 `music_port` | 迁移:启动时把它读出来,写到新的 `ports.json`,然后删旧 key |

## E2E 测试

`tests/_verify_port_config.py`:
  1. `read_port('web', 18802)` → 默认 18802
  2. `write_port('web', 19999)` → 再读 = 19999
  3. `resolve_start_port('web', None, None, 18802)` = 19999
  4. `resolve_start_port('web', '18803', None, 18802)` = 18803(env 优先)
  5. `resolve_start_port('web', None, 18804, 18802)` = 18804(CLI 优先)
  6. `pick_free_port(18802)` → 假设 18802 占用,返 18811 或别的空闲
  7. `/api/port_status` 在主面板启动后返 `{web: {configured: 18802, actual: 18811, changed: true}}`

## 不进本次提交的内容

- PyInstaller 重打 + NSIS 装包(等用户拍板「所有开发完成」)
- Tauri 壳 Rust 端改动(等 Rust 编译链就绪,目前只在 Python 端跑通验证)

## 派单建议(下一步)

派单 `#49 → code-implementer`:
  - 写 `port_config.py` + `pick_free_port` + 迁移旧 `music_port` 注册表项
  - 改 `prisIragent_web.py` `main()` 调 `resolve_start_port` + 启动后写回注册表 + `/api/port_status`
  - 改 `companion/prisiragent-companion-web.py` 同款
  - 改 `companion/prisiragent-music-web.py` 改用新统一接口
  - 写 `tests/_verify_port_config.py`

`Tauri 壳 Rust 改动`:派单 `#49-T` 等 Rust toolchain 解锁(PoC 编译链路依赖,后续专门 sprint 做)。

## 关联 commit / doc

- 现状地基:`port_registry.py` (M3.29.1) + env 双 fallback commit `9b8799c`
- 关联思路 B: `621b0b9` (主面板冻结后不含 git/office 字面量;新端口代码纯 stdlib,无新启发式触发词)

---

**用户决策记录(2026-09-19):**
  - ✅ 不做 UI 端口设置面板(理由:自动 fallback + 防火墙弹问 = 用户永远不需要手动配)
  - ✅ 自动冲突 fallback + 一次性 toast
  - ✅ 沿用 HKCU 注册表 + JSON fallback(音乐 web 已有基建)

**下一步:派单 `code-implementer` 实施,或先讨论细节。**