# PrisirAI 日历 [prisIr_calendar/] - Phase 1 写入目录与集成方案

> 状态:Phase 1 启动前一次性确认 · 2026-09-19
> 用途:回答 prisIr_calendar 代码写到哪、Tauri 与 Python 边界在哪、要不要改 Cargo.toml
> 前置文档:docs/prisIr-calendar-design.md [库选型]、docs/prisIr-calendar-product-scope.md [产品边界]

---

## 1. 现状盘点 [命令实测,非凭记忆]

### 1.1 prisIragent-tauri/src-tauri/ 完整结构

```
src-tauri/
├── .gitignore
├── build.rs                  [37 字节]
├── Cargo.toml                [1000 字节]
├── Cargo.lock                [146 KB]
├── capabilities/             [Tauri 2.x ACL]
├── frontend/index.html       [422 字节过渡页]
├── gen/                      [tauri-build 生成物]
├── icons/                    [32x32 / 128 / 128@2x / icon.icns / icon.ico]
├── src/main.rs               [7 行]
├── src/lib.rs                [814 行 - 壳本体]
├── src/music.rs              [228 行 - M3.29 music 子进程]
├── target/                   [Rust 编译产物]
└── tauri.conf.json           [1266 字节]
```

**Cargo.toml 关键字段** [prisIragent-tauri/src-tauri/Cargo.toml]:

| 字段 | 值 |
|---|---|
| package.name | prisirai-shell |
| package.version | 2.7.7 |
| lib.name | prisirai_shell_lib |
| lib.crate-type | [staticlib, cdylib, rlib] |
| tauri 版本 | 2.11.3 |
| 关键 plugin | tauri-plugin-log, tauri-plugin-autostart, tauri-plugin-global-shortcut, tauri-plugin-notification, tauri-plugin-process, tauri-plugin-shell |
| Windows-only | windows 0.58, winreg 0.52 |
| **Python / PyO3 依赖** | **零** - Rust 是纯壳 |

tauri.conf.json 关键字段:
- productName: PrisirAI
- identifier: com.prisir.prisirai
- build.frontendDist: ./frontend
- build.devUrl: http://localhost:18802 [开发态指向 Python 后端]
- app.windows[0]: 主窗口,visible=false,后端就绪后 win.eval 跳到 18802
- app.windows[1]: label lyrics-window, M3.29 桌面歌词透明窗
- bundle.targets: nsis

### 1.2 prisiragent_web.py 在哪里被调用?

仓库根 prisiragent_web.py [单文件, 10781 行] 被调用:

| 调用方 | 文件:行 | 行为 |
|---|---|---|
| Tauri Rust 壳 | prisIragent-tauri/src-tauri/src/lib.rs:112-129 | spawn dist/PrisirAI.exe --port 18802 --lan [装包后] 或 python prisiragent_web.py [开发态]; fallback 见 resolve_core_exe line 71-89 |
| Tauri Rust 壳 [开发态分支] | prisIragent-tauri/src-tauri/src/lib.rs:118-129 | 找不到 PrisirAI.exe 时直接 python prisiragent_web.py |
| NSIS 安装脚本 | installer/prisirai.nsi:176 | File ..dist..PrisirAI.exe 把 PyInstaller 打包好的后端 exe 拷到 INSTDIR |
| 手动/测试 | 仓库根直接 python prisiragent_web.py --port 18802 | 单元测试/E2E |

grep prisIragent_tauri / prisIragent-tauri 全仓命中 6 处:
- tests/_test_m329_4_tray.py
- tests/_test_m329_3_lyrics_window.py
- companion/music/port_registry.py [无关]
- tests/_test_m327_dispatch.py
- installer/prisirai.nsi [line 180]
- .gitignore [line 235 注释]

**没有任何 Python 源码直接 import 或引用 Rust crate 名**。Python 端根本不知道 Tauri 的存在。

### 1.3 仓库根 Python 文件与 Tauri Rust 的接口方式

**std::process::Command 子进程 spawn** [line 112-210 in lib.rs]。

1. Tauri 启动时 setup 钩子里 line 612 调用 start_backend,内部 Command::new[&cmd].args[&args].stdin[null].stdout[piped].stderr[piped].spawn[]
2. stdout/stderr 落到 %APPDATA%/prisirai-shell/logs/spawn-stdout.log / spawn-stderr.log
3. Tauri 起后台线程轮询 127.0.0.1:18802 TCP 端口 [line 193-204],最多等 30s
4. 主窗口等 web_ready 后 win.eval + win.show[] + set_focus[] [line 755-771]
5. 退出时 kill_backend → child.kill() + wait()

**没有任何 IPC / FFI / 共享内存 / Unix socket / 命名管道**。Rust 与 Python 之间唯一的耦合 = 进程生命周期 + 端口约定 18802。

Tauri 调用 Python 暴露功能的方式只有两种:
- A. 前端 fetch [浏览器 → Python HTTP],Tauri 自身完全透明
- B. Tauri command + 前端 invoke [浏览器 → Tauri Rust → 由 Rust 决定 spawn / 转发]

invoke_handler 注册了 8 个 command [shell_info, shell_toggle, shell_open_external, start_companion_cmd, start_music_cmd, open_lyrics_cmd, close_lyrics_cmd, music_status_cmd] — **没有任何 invoke_handler 直接调 Python**。

M3.27 companion 派发已经在用纯 HTTP 跨进程: companion 起在 18850, POST http://127.0.0.1:18802/prisiragent/api/external_inject 给主面板 — 这就是现成模式。

### 1.4 installer/prisirai.nsi 打包哪些 Python 模块到 dist/PrisirAI.exe?

installer/prisirai.nsi line 169-228 的 Section:

```
SetOutPath ""
File "..\dist\PrisirAI.exe"                              ← PyInstaller 产物 [主后端]
File "..\prisIragent-tauri\src-tauri\target\release\prisirai-shell.exe"  ← Tauri 壳 [15MB]
SetOutPath ""
File /r "_staging2\assets"                               ← 图标/主题/山水
SetOutPath "\bin"
File /r "_staging\bin\git"
File "_staging\bin\officecli.exe"
SetOutPath ""
File "launcher.bat"
File "PrisirAI.vbs"
File "README.txt"
File "LICENSE.txt"
CreateDirectory "\logs"
```

**关键观察**:
- PrisirAI.exe = PyInstaller 单 exe,已经 frozen 了 prisiragent_web.py 和它 import 的所有 Python 源码 [prisiragent_web.py:50-63 frozen 处理 tiktoken / :38-42 patch 注入]
- installer/prisirai.nsi 不再单独拷任何 .py 文件 [只拷 exe + assets + bin] — 因为 PrisirAI-core.spec datas 已经把 .py 打进了 exe
- prisIragent-tauri/.../prisirai-shell.exe 是另一个独立 exe [壳],装到 INSTDIR 后,壳的 resolve_core_exe 优先找**同级**的 PrisirAI.exe [line 78],找不到才 fallback 到 ..\dist\PrisirAI.exe [开发态]

---

## 2. Phase 1 写入目录决策

### 答案 [必给,不"看情况"]

**prisIr_calendar/ 写到 仓库根/prisIr_calendar/** [相对路径,绝对路径 C:\\Users\\Administrator\\oi_enhancements\\prisIr_calendar\\]。**不写到 prisIragent-tauri/src-tauri/...**。

### 一句话理由

PrisirAI 现有架构是 **Rust 壳 + Python 后端**,Python 端已经按"仓库根单文件 / 多模块"的模式组织 [prisiragent_web.py、companion/prisiragent-companion-web.py、companion/prisiragent-music-web.py 全部挂在仓库根或 companion/ 下],Tauri 的 src-tauri/src/ 是**纯 Rust**,没有任何把 Python 源嵌进去的先例; prisIr_calendar/ 是纯 Python 库,跟着现成的 Python 模块惯例走。

### 与现有结构的对应

| 现成参考 | 类比到 prisIr_calendar |
|---|---|
| companion/prisiragent-companion-web.py [2191 行,单文件 web 后端] | 不类比 - calendar 不是 web 后端 |
| companion/music/ [多文件 Python 包: player / lx_bridge / lyric_provider / agent_cfg / port_registry / lyric_loader] | 直接类比 - prisIr_calendar/ 应该是多文件 Python 包,跟 companion/music/ 同模式 |
| companion/lx_runtime/ [M3.28 jsdom shim 子包] | 同上 |
| prisir_work/、prisir_fcontent/、prisir_findex/ [仓库根 Python 包] | 同上 |

最终结构预览 [供派单参考,不强制]:

```
仓库根/prisIr_calendar/
├── __init__.py
├── store.py            [SQLite WAL + CalendarStore 抽象]
├── ical_codec.py       [icalendar 编解码 + X-PRISIR- 命名空间]
├── recurr.py           [recurring-ical-events 包装]
├── buffer.py           [insert_buffer / scan_chain 自研语义]
├── ledger.py           [dismiss / policy 决策审计]
├── http_api.py         [FastAPI 暴露 - Phase 2 跟主后端对接用]
└── tests/
    ├── test_store.py
    ├── test_buffer.py
    ├── test_dismiss.py
    └── test_ical_export.py
```

**不需要在 Cargo.toml 注册 Rust 依赖** [prisirai-shell 是纯壳,不 import 任何 calendar 库]。

---

## 3. 集成方式 [Phase 1 不实现,Phase 2 要用]

### 推荐方案: FastAPI 子进程 + 动态端口 + HKCU\Software\PrisirAI\calendar_port 注册表 [沿用 M3.29 music 的模式]

### 一句话理由

companion/prisiragent-music-web.py + prisIragent-tauri/src-tauri/src/music.rs 已经把"Python 子进程 + 动态端口 + 注册表 + Tauri spawn + read_*_port 探测"这条路完全跑通了,Phase 1 把 prisIr_calendar/ 写成同样形态的 Python 包,Phase 2 集成只要在 lib.rs 加一个 start_calendar 函数 [仿 start_music],Tauri 自动管生命周期。

### 三种候选 + 评估

| 方案 | 评估 |
|---|---|
| A. 子进程 + FastAPI + 动态端口 [推荐] | OK 跟 music web 同模式, 代码可复用 80% [music.rs:71-175 的 start_music 几乎原样抄]; 端口 0 → 自动分配, 写到 HKCU + _prisir_registry/calendar_port.json; Tauri 端 read_calendar_port 探测; Phase 1 不接 Tauri, Phase 2 加 3 行 Rust 调用 |
| B. 嵌进 prisiragent_web.py 单文件 [进程内 in-process FastAPI] | NO 违背"模块化"原则 [prisiragent_web.py 已经 10781 行]; 单进程重启会让 calendar 数据一起关掉,违反"agent 日程大脑 = 长期记忆"的产品定位 |
| C. PyO3 / rust-cpython FFI | NO 引入 cpython 编译依赖, Python 版本/Rust toolchain 升级时要双向重建; calendar 是纯 IO + JSON, 无性能瓶颈, FFI 得不偿失 |

### Phase 2 集成骨架 [Phase 1 不写,仅作规划参考]

```
[Phase 1 交付]
仓库根/prisIr_calendar/
    FastAPI app: GET /api/health, POST /api/event/add,
                 POST /api/event/dismiss, GET /api/event/list?start=&end=,
                 POST /api/buffer/insert, GET /api/ledger/recent
    启动: python -B -m prisIr_calendar.http_api --port 0
    端口注册: HKCU\Software\PrisirAI\calendar_port
              + _prisir_registry/calendar_port.json fallback

[Phase 2 在 Tauri 侧加 [只动 lib.rs + 1 个新文件]]
prisIragent-tauri/src-tauri/src/calendar.rs  [仿 music.rs, ~80 行]
    - read_calendar_port()              [照抄 music.rs:39-66]
    - start_calendar(state)             [照抄 music.rs:71-175]
    - kill_calendar(state)
    - calendar_status()                 [照抄 music.rs:217-227]
prisIragent-tauri/src-tauri/src/lib.rs:
    - 加 mod calendar
    - 加 Tauri command: start_calendar_cmd / calendar_status_cmd
    - 加托盘菜单项 "启动日历"
    - ExitRequested 时 kill_calendar(&state)
installer/prisirai.nsi:
    - 不需要改 [calendar 是 PyInstaller frozen 进 PrisirAI.exe 的,
      跟 prisiragent_web.py 同模式打进同一个 exe]
prisiragent_web.py [Phase 2 末尾]:
    - 转发到 calendar_port 的 FastAPI,前端无感
    - 或者直接 in-process import [看产品决定]
```

### oiagent-web skill / 现有门面补充

- **web_auto 门面**:任务提到"tools/ 目录有没有线索"— **全仓未发现 tools/ 子目录**,只有 custom-hover-translate/tools/ [无关]和 prisIragent_coworker/ [独立工具集合,与 calendar 无关]
- **现有门面**:
  - prisiragent_web.py:10781 行 是 web 入口 [HTTP, port 18802]
  - companion/prisiragent-companion-web.py:2191 行 是陪聊壳 [HTTP+WS, port 18850]
  - companion/prisiragent-music-web.py 是 music 子服务 [HTTP+WS, 动态端口]
  - **calendar 是第 4 个独立 Python 子服务**,沿用 music 子服务的形态最自然
- **决策**:不在 Phase 1 创建新的"总门面",calendar 自带 FastAPI 就够。

---

## 4. 与 task #13 / #14 的关系 [防止派单打架]

### 当前事实

| 任务 | 当前状态 | 涉及路径 | 是否影响 prisIr_calendar/ 写入路径 |
|---|---|---|---|
| task #13: 改名 + 全量引用同步 | 未在仓库发现明示文档 [.prisir_snapshots/ 仅 0 字节 .lock + 760B .imported_index.json, 无 task 清单] | 全仓 grep prisIragent-tauri [6 处: .gitignore / installer/prisirai.nsi / 3 个 test / companion/music/port_registry.py] + 全仓 grep prisIragent_tauri [0 处] | 可能影响: 若 task #13 要把 prisIragent-tauri/ 改名为 prisIragent_tauri/ [Snake_case → camelCase 统一], 那么 installer/prisirai.nsi:180 路径字符串要同步改 |
| task #14: gitignore Electron 残留 | 已部分落地: .gitignore:235-240 已有注释 + 3 行 ignore 规则, 日期戳 2026-09-19 | dist/oiagent-shell/, dist/oiagent-shell_FROZEN_DELETE_ME/, .tmp_extract/oiagent-shell/, installer/_staging/oiagent-shell*/ | 不影响: calendar 是新代码, 不存在 Electron 残留 |

### 是否需要调整 prisIr_calendar/ 路径?

**不需要**。理由:

1. task #13 若做目录改名:仅改 prisIragent-tauri/ 这一棵目录的拼写 [夹带一些路径字符串]。prisIr_calendar/ 是新目录,跟 prisIragent-tauri/ 平级,**新目录本身在 task #13 改名范围外** [task #13 是"全量引用同步", 不是"全目录改名"]。即便 task #13 改了 prisIragent-tauri/, calendar 路径也不动。
2. task #14 Electron 清理:完全是减法,把已死的目录从 git tracking 移除。calendar 是加法,无冲突。
3. case 一致性:现有 Python 包都是 Snake_case [prisIr_findex/、prisir_work/、companion/music/、companion/lx_runtime/], prisIr_calendar/ 跟它们一致; Rust 端是 Snake_case Cargo crate name [prisirai_shell_lib], 新 Python 包命名不冲突。
4. 没有 task #13/#14 文档可读: .prisir_snapshots/.imported_index.json 760B 大概率不是 task list。**派单时建议先确认 task #13/#14 的实际范围**; 如果 task #13 包含 Python 包命名规范 [比如要求 prisir_xxx 而不是 prisIr_xxx], 那 prisIr_calendar/ 就要改名为 prisir_calendar/ — 但**目前没有任何证据表明 task #13 有这个要求**, 所以保持 prisIr_calendar/。

### 派单保护建议 [给后续 task #13 / #14 owner]

如果 task #13 owner 在执行中发现需要:
- 重命名任何 prisIr* Python 包 → **必须先通知 calendar Phase 1 owner**,不要擅自改 prisIr_calendar/。
- 把 Tauri 壳改名 → prisirai-shell crate name + prisIragent-tauri/ 目录路径可能动, 但 **不影响** calendar。

---

## 5. 一句话总结

**Phase 1 把 prisIr_calendar/ 写到仓库根 prisIr_calendar/ [Snake_case Python 包], 用 FastAPI 自暴露, Phase 2 在 Tauri 加一个 calendar.rs [仿 music.rs ~80 行] 做 spawn 即可, Cargo.toml 不需要任何改动。**

---

## 附录 A:实测命令清单 [可重跑]

```bash
# 1. src-tauri 结构
ls "prisIragent-tauri/src-tauri/src/"
cat "prisIragent-tauri/src-tauri/Cargo.toml"
cat "prisIragent-tauri/src-tauri/tauri.conf.json"

# 2. Python 端谁引用 Tauri
grep -rn "prisiragent-tauri\|\prisIragent_tauri" --include="*.py" --include="*.md" \
    --include="*.json" --include="*.yaml" --include="*.yml"
# 0 hits in .py; 6 hits total

# 3. Python 端怎么被 Tauri 调用
grep -n "prisiragent_web\|\spawn\|\Command::new" "prisIragent-tauri/src-tauri/src/lib.rs"
# line 112-129 spawn backend, line 71-89 resolve_core_exe

# 4. NSIS 打包范围
grep -n "File \|\CreateDirectory" "installer/prisirai.nsi" | head -20

# 5. music 子进程作为 calendar 的范本
head -120 "companion/prisiragent-music-web.py"
head -120 "prisIragent-tauri/src-tauri/src/music.rs"

# 6. 确认 calendar 目录还不存在
find . -maxdepth 3 -type d -name "prisIr_calendar*"
```

## 附录 B:关键文件路径速查

| 关注点 | 路径 |
|---|---|
| Rust 壳本体 | C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\src\\lib.rs |
| Music 子进程 [Rust 侧] | C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\src\\music.rs |
| Cargo manifest | C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\Cargo.toml |
| Tauri 配置 | C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\tauri.conf.json |
| Python 主后端 | C:\\Users\\Administrator\\oi_enhancements\\prisiragent_web.py |
| Companion 陪聊 | C:\\Users\\Administrator\\oi_enhancements\\companion\\prisiragent-companion-web.py |
| Music 子服务 [模板] | C:\\Users\\Administrator\\oi_enhancements\\companion\\prisiragent-music-web.py |
| 端口注册工具 | C:\\Users\\Administrator\\oi_enhancements\\companion\\music\\port_registry.py |
| NSIS 安装脚本 | C:\\Users\\Administrator\\oi_enhancements\\installer\\prisirai.nsi |
| Calendar 设计稿 | C:\\Users\\Administrator\\oi_enhancements\\docs\\prisIr-calendar-design.md |
| Calendar 产品边界 | C:\\Users\\Administrator\\oi_enhancements\\docs\\prisIr-calendar-product-scope.md |
| **Phase 1 写入目录 [本文件决策]** | **C:\\Users\\Administrator\\oi_enhancements\\prisIr_calendar\\ [新, 尚未创建]** |

## 附录 C:Critical Files for Implementation

- C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\src\\lib.rs
- C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\src\\music.rs
- C:\\Users\\Administrator\\oi_enhancements\\prisIragent-tauri\\src-tauri\\Cargo.toml
- C:\\Users\\Administrator\\oi_enhancements\\companion\\prisiragent-music-web.py
- C:\\Users\\Administrator\\oi_enhancements\\docs\\prisIr-calendar-design.md

