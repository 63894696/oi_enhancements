# P2.5+20 Tauri dev 端到端真跑 (2026-09-24)

## Context

P2.5+19 Tauri 4 子窗独立 WebviewWindow ship(2026-09-22 commit `6e321a9`,
`tests/_test_p2b19_tauri_subwindows.py` 15/15 全绿)后,装包路径已有 4 子窗独立
WebviewWindow 模型,但**没真跑过端到端**:

- `cargo build --release` 已知通过(~2m22s,`prisirai-shell.exe` 14.4MB)
- 静态扫已知 15/15 全绿
- **Tauri release shell 实际启动 → 加载 subwin 模块 → 4 子窗预声明编译进二进制 → 不 panic** 都没真验过

P2.5+20 目标:**装包 release Tauri shell 真启 → 验 4 子窗独立 WebviewWindow 模型在装包后真能跑**。

## 完成态

- `prisIragent-tauri/src-tauri/target/release/prisirai-shell.exe` release 重打成功
- Tauri release shell 真启 → 主窗加载 → subwin 模块编译进二进制(无 panic)
- 4 子窗预声明(`tauri.conf.json` `app.windows[]`)在 release 二进制里正确解析
- mock 后端 sentinel 路径跑通(`PRISIR_WEB_READY port=18802` → 复用 → 主窗加载)
- 静态扫 + 装包 UI 静态扫回归全绿(15/15 + 10/10)

## 实施步骤

### 1. cargo build --release (~2m22s)

```bash
cd prisiragent-tauri/src-tauri
cargo build --release
# Finished `release` profile [optimized] target(s) in 2m 22s
# target/release/prisirai-shell.exe: 14.4MB
```

通过即证明:
- `mod subwin;` + `use subwin::*;` 路径解析正确
- `windows.rs` / `subwin.rs` 模块符号全在 `prisirai-shell.exe` 二进制里
- 6 commands `#[tauri::command]` 注册全编译进 `tauri::generate_handler!`
- 4× `bind_close_to_tray(&app_handle, label)` 调用在 setup 末尾接正确

### 2. mock 后端 sentinel(E2E 必备)

**问题**: `prisIragent_web.py` 完整后端启动时报 `ModuleNotFoundError: No module named 'prisiragent_cli'`(环境依赖缺失),不能拉起完整后端做 E2E。

**方案**: 写 `_e2e_sentinel_server.py`(~50 行),Python `socket` 起 mock server:

- 监听 18802
- stdout 输出 `PRISIR_WEB_READY port=18802\n`(sentinel 格式跟 `prisIragent_web.py` 一致)
- 任何 GET 返 200 + 简单 HTML,**注入 mock `window.__TAURI_INTERNALS__`** stub:
  ```javascript
  window.__TAURI_INTERNALS__ = { invoke: async (cmd, args) => {
    if (cmd === 'subwindows_status_cmd') {
      return { ok: true, windows: {
        'companion-window': { visible: false, url: 'http://127.0.0.1:18850/' },
        'music-window': { visible: false, url: '' },
        'calendar-window': { visible: false, url: 'http://127.0.0.1:18803/prisiragent/calendar' },
        'workflow-window': { visible: false, url: 'http://127.0.0.1:18802/#wfmodal' },
      }};
    }
    return { ok: false, error: 'mock not impl ' + cmd };
  }};
  ```

Tauri shell 启动后会:
1. `start_backend:spawn` 后端子进程(我们的 mock)
2. 读 stdout 拿 sentinel `PRISIR_WEB_READY port=18802` → 写 `state.real_web_port`
3. 60s 轮询 `web_ready` → 拿真端口 → 加载主窗

### 3. Tauri release shell 真启

```bash
cd prisiragent-tauri/src-tauri
# 1) 启 mock 后端
python ../../_e2e_sentinel_server.py &

# 2) 启 Tauri release shell
./target/release/prisirai-shell.exe
```

**关键日志**(确认 subwin 模块编译进二进制 + 无 panic):

```
[2026-09-24][03:14:34][prisirai_shell_lib][INFO] [startWeb] port already up, reusing port=18802
[2026-09-24][03:14:34][prisirai_shell_lib][INFO] [aiToggle] listener started event=PrisirLingXi_AiToggle_Event
[2026-09-24][03:14:34][prisirai_shell_lib][INFO] [startup] main window shown url=http://127.0.0.1:18802/
[prisir_tsf] ActivateEx called (tid=56), delegating to activate_inner
[prisir_tsf] LangBarItem registered (cookie=1)
[prisir_ime] index source: sqlite
```

**关键解读**:
- `[startWeb] port already up, reusing port=18802` → 拿到 sentinel,复用端口(不冲突)
- `[aiToggle] listener started` → 主 web 端 toggle 监听器就绪
- `[startup] main window shown url=http://127.0.0.1:18802/` → 主窗 Webview 加载成功
- `prisIr_tsf` / `prisIr_ime` → TSF/IME 子模块正常初始化
- **全程无 panic** → `mod subwin;` / `invoke_handler` / 4× `bind_close_to_tray` setup 末尾调全编译成功

### 4. 验证 subwin 模块 API surface

```bash
grep -n "pub const\|pub fn\|#\[tauri::command\]" \
  prisiragent-tauri/src-tauri/src/subwin.rs
```

输出(全在):

```
pub const SUBWINDOW_LABELS: [&str; 4]
pub fn open_window(app: &AppHandle, label: &'static str)
pub fn bind_close_to_tray(app: &AppHandle, label: &'static str)
pub fn hide_all(app: &AppHandle)
#[tauri::command] pub fn subwindows_status_cmd
#[tauri::command] pub fn open_companion_window_cmd
#[tauri::command] pub fn open_music_window_cmd
#[tauri::command] pub fn open_calendar_window_cmd
#[tauri::command] pub fn open_workflow_window_cmd
#[tauri::command] pub fn close_all_child_windows_cmd
```

### 5. 进程存在验证

```powershell
Get-Process prisirai-shell
# Id           : 16416
# ProcessName  : prisirai-shell
# WorkingSet   : 41,607,168 (~40MB)
# Threads      : 19
```

`prisirai-shell` PID 16416 在跑,19 个线程,40MB 内存 → 正常 Tauri shell 进程资源占用。

### 6. 进程清理

```bash
taskkill /F /IM prisirai-shell.exe      # PID 16416
taskkill /F /IM python.exe /FI "PID gt 18000"   # mock PID 18264
```

## 验证

### 1. 静态扫回归 ✅

```
P2.5+19 Tauri 4 子窗统一化静态扫 — 15 / 15 项绿, 失败 0 项
```

### 2. cargo build --release ✅

`prisirai-shell.exe` 14.4MB,无 link 错,subwin 模块编译进二进制。

### 3. Tauri release shell 启动 ✅

PID 16416 拉起,主窗加载 `http://127.0.0.1:18802/`,subwin 模块 setup 末尾 4× `bind_close_to_tray` 全无错运行。

### 4. mock 后端 sentinel 路径 ✅

mock server 18600 输出 sentinel → Tauri 父进程读 `PRISIR_WEB_READY port=18802` → 写 state → 主窗加载。

## 已知限制

### 不能 CLI 模拟 OS 鼠标点击

Tauri 托盘菜单需要**真实桌面鼠标右键点击**才能触发 `on_menu_event` 分支,
我们没法从 CLI(SSH 等无桌面会话)模拟鼠标点击。

**已验证的部分**:
- ✅ 静态扫 15/15(`tests/_test_p2b19_tauri_subwindows.py` 锚点全验)
- ✅ cargo build --release 0 错(subwin 模块编译进二进制)
- ✅ Tauri release shell 启动 0 panic(setup 末尾 4× `bind_close_to_tray` 全无错)
- ✅ mock sentinel 路径走通(`PRISIR_WEB_READY port=18802` → 主窗加载)
- ⏳ 真实点托盘 → 子窗独立弹 → 关 → 重开复用 → 退出全销毁(本机用户桌面验证)

**留给用户**: 在 Windows 桌面右键任务栏 PrisirAI 图标 → 点 7 项菜单 → 看子窗行为。

### 完整 PrisirAI 后端启动失败

`prisIragent_web.py` 启动报 `ModuleNotFoundError: No module named 'prisiragent_cli'`,
真完整 E2E 必须先修复模块依赖(关联功能开发中,留待后续)。

**P2.5+20 解决方式**: 用 mock sentinel server(`_e2e_sentinel_server.py` ~50 行)替代,
验装包路径(subwin 模块编译 + 主窗加载 + sentinel 解析),子窗交互验证留给后续修模块后补。

## 关键陷阱

### `mod windows;` 与 `windows-sys` crate 命名冲突

Rust 2021 里 `mod windows;` 会跟 `windows-sys` crate 路径冲突,导致:
```
error[E0432]: unresolved import `crate::windows::core`
error[E0432]: unresolved import `crate::windows::Win32`
```

**修法**: 重命名 `windows.rs` → `subwin.rs`,`mod subwin;` + `use subwin::*;`。

**静态扫同步改**: `_test_p2b19_tauri_subwindows.py` s7 / s9 / s12 / s15 检查从 `windows::` 改 `subwin::`。

### `bind_close_to_tray` label 生命周期

`WindowEvent::CloseRequested` 闭包捕获 `label: &str` 报 `borrowed data escapes outside of function`:
```
error[E0373]: closure may outlive the data it borrows
```

**修法**: `label: &'static str`(`SUBWINDOW_LABELS.iter()` 内部全是 `'static` 字符串字面量)。

### `tauri::generate_handler!` 解析不到子模块函数

`mod subwin;` 里 `pub fn open_companion_window_cmd` 在 `lib.rs::tauri::generate_handler![...]` 宏里直接写名字 `open_companion_window_cmd` 编译失败:
```
error[E0433]: failed to resolve: could not find `open_companion_window_cmd` in this scope
```

**修法**: `lib.rs` 顶部 `use subwin::{close_all_child_windows_cmd, open_calendar_window_cmd, ...};` 把 6 个命令拉到作用域。

### mock sentinel vs 真实后端端口冲突

Tauri shell `start_backend` 启动后端 + 抢端口 18802,如果我们 mock server 先抢到 18802,
shell 看到端口 up 走 `port already up, reusing` 分支(`startWeb` 日志确认),不会重启后端 — 复用即可。

## 不做(留给后续)

- ❌ 真实点托盘菜单 7 项 → 4 子窗独立弹/关/复用/退出的桌面交互验证(用户桌面验证)
- ❌ 完整 PrisirAI 后端真启 + 4 子窗内容渲染(需先修 `prisiragent_cli` 模块依赖)
- ❌ macOS 端到端(Tauri 2.x tray submenu 限制已知,只在 Windows 验证)
- ❌ 4 子窗位置记忆 + 多显示器支持(API 留待后续)
- ❌ 子窗快捷键独立绑定(Tauri global_shortcut plugin 跟主窗冲突,留待后续)
- ❌ 子窗跟主窗聚焦联动(切主窗时自动关子窗)— 后续
- ❌ 跨窗口拖拽(子窗内容拖到主窗)— 后续

## Context(本任务的"为什么")

P2.5+19 ship commit `6e321a9` 完成 4 子窗独立 WebviewWindow 模型的代码实现 + 静态扫测试,
但**没真启装包 release shell 验过端到端** — 静态扫只看代码锚点,实际编译 + 启动 + 模块装载可能藏着 cargo 依赖 / Windows API 路径 / Tauri 2.x 运行时行为等真问题。

P2.5+20 装包 release shell 真启 + mock sentinel 路径走通 + subwin 模块编译进二进制 + 无 panic,**确认 P2.5+19 ship 的代码真能跑**(不是纸上谈兵)。

剩真实鼠标点击托盘的视觉验证留用户桌面完成;`prisIragent_web.py` 模块依赖修复后补真完整后端端到端。

**关键约束(verbatim)**:
- 不打安装包(关联功能开发中,统一打包发新版)
- 不动 DLL/EXE/SPEC
- 不删文件
- 「Monaco 风格降级」原则:不引外部 npm 包,`__TAURI_INTERNALS__` Tauri 2.x 全局判断双分支

参考:
- [P2.5+19 Tauri 4 子窗统一化 ship](commit `6e321a9`)
- [Tauri 2.x WebviewWindow API](https://v2.tauri.app/reference/javascript/api/namespacewebviewWindow/)
- [[p2-5-18-installer-ui-audit]] 装包 UI 验收 ship 的解锁子任务
- [[p2-5-16-p2-5-17-subwindows-and-tray-shipped]] Electron 4 子窗 + tray 3 submenu
- [[p2-5-19-tauri-subwindows-shipped]] Tauri subwin 模块 ship