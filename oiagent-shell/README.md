# oiagent-shell — OIagent 本地对话壳(Electron)

不启用浏览器也能和 OIagent 对话。主进程 spawn 并看护 `oiagent_web.py`
(127.0.0.1:18802,国画风聊天 UI,SQLite 持久化),壳把它包成桌面窗口。

定位见 `Documents/oiagent-os-integration/prisirwork-foundation-integration-design.md` §5.1(F7)。
与已归档的 `securedm-shell`(Tauri)不同——本壳走 Electron(拍板),复用 oiagent_web.py。

## 跑起来

```bash
cd oiagent-shell
npm install        # 装 electron(首次)
npm start          # 起壳:自动拉起 oiagent_web.py 并开窗
```

要求:本机 `python` 可跑 `oiagent_web.py`(其依赖 fastlane/oiagent_cli 已装好)。
端口被占用时直接复用已在跑的服务,不重复起。

## 功能

- **桌面窗**:加载 `http://127.0.0.1:18802`,纸色背景避免白闪。
- **托盘**:关窗最小化到托盘,点托盘图标呼出/隐藏;右键菜单可退出。
- **全局热键**:默认 `Ctrl+Shift+O`(`OIAGENT_SHELL_HOTKEY` 可改)。
- **开机自启**:托盘菜单勾选(`app.setLoginItemSettings`)。
- **单实例**:重复启动只把已有窗口提到前台。

## 红线(token/权限纪律,与 F5 同源)

- PrisirWork token 只在**主进程**读 0600 配置;经 preload 只把「是否存在」**布尔**
  告知渲染层,**绝不把 token 本体**打进 renderer bundle / 暴露给页面 JS。
- 渲染进程 `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`,
  页面只经白名单 IPC(`oiShell.shellInfo()` / `oiShell.toggle()`)与主进程通信。
- `oiagent_web` 只监听 127.0.0.1;壳加载的也是回环地址。外链一律交系统浏览器,
  壳内不导航出回环。

## 配置(环境变量)

| 变量 | 缺省 | 说明 |
|---|---|---|
| `OIAGENT_WEB_PORT` | `18802` | oiagent_web 端口 |
| `OIAGENT_SHELL_HOTKEY` | `Ctrl+Shift+O` | 全局呼出热键 |
| `OIAGENT_PYTHON` | `python` | 起 web 服务的 Python |
| `PRISIR_WORK_CONFIG` | `~/.prisir/work.json` | token 配置路径(主进程只读) |

## 权限闸(v1.0 起生效)

壳内聊天时,agent 调用 `run_shell / write_file / delete_file` 三个本地工具会在
执行前弹**阻塞式确认卡**:

- **触发场景**:`run_shell` 出 workdir / 命中 destructive 模式(`rm -rf`、`Remove-Item -Recurse`、`format C:`、`dd`)/ `write_file` 出 workdir / `delete_file` 出 workdir。workdir 内安全写直接放行,`read_file` 不过闸。
- **卡片样式**:420px 卡片居中,标题前缀 `⚠️ 权限确认`,风险级按颜色区分:
  - `destructive` 高危删除 — 朱砂红 `#a8332a`(OK 按钮同色)
  - `exec` 执行 — 赭石 `#b07a3f`
  - `write` 写入 — 浅褐 `#c9b274`
  - `read` 读取 — 不过闸
- **倒计时**:右上角显示剩余秒数(120s),到 `0` 显示「已超时」+ 红字,**不影响用户操作**(只展示)。
- **按钮**:左 「拒绝」、右 「允许执行」。Esc / 关闭弹窗等同于拒绝。
- **超时自动拒绝**:120s 内未点 → 后端 `_perm_on_confirm` Event.wait 超时返回 False → agent 收到 `[被用户拒绝] timeout`。
- **审计**:每次决策落 `logs/audit/permission_stream.jsonl`(workdir 相对路径),含 risk_level / allow / requires_approval / reason / ts。审计写入永不抛。
- **`run_agent` 自主 benchmark 路径无闸**:自动评测场景 try/finally 临时关闸,不影响。
- 实现细节见 `handoff/2026-08-22-prisirai-perm-gate-v1-wiring.md`,宪法条款见 `docs/prisir-dev-constitution.md` §3b。

## 分发(NSIS 装包,v1.0 起)

开发机有 Python + Node,直接 `npm start` 即可;但要给用户分发必须用 NSIS 装包器:

```bash
python .tmp_build.py            # PyInstaller 打 dist/PrisirAI.exe(218 MB)
python .tmp_stage.py            # 拷 assets + oiagent-shell 到 installer/_staging/
cd installer && makensis prisirai.nsi
# → ../dist/PrisirAI-Setup-1.0.0.exe(348 MB 自解压安装器)
```

**用户路径**:

1. 双击 `PrisirAI-Setup-1.0.0.exe` → NSIS 向导(中文 MUI2,默认安装到 `$LOCALAPPDATA\Programs\PrisirAI`)。
2. 装完桌面有「Prisir AI」.lnk(图标=对话壳火苗版 `oiagent-shell/icon.ico`)。
3. 双击 → `launcher.bat` → `electron.exe` → 起 PrisirAI.exe 后端 → 弹窗口。
4. 卸载:开始菜单「卸载 PrisirAI」 / 控制面板程序。

**装包器约束**(踩坑沉淀,2026-08-23):

- 排除文件**不能去掉** `v8_context_snapshot.bin` 和 `snapshot_blob.bin`,否则 electron.exe 报 `Error loading V8 startup snapshot file` 直接 rc=1 退出。
- 排除文件**不能去掉** `default_app.asar`,否则 Electron 33+ 启动时静默 rc=1,无 stderr 输出。
- 排除文件**不能去掉** `.gitignore`(看着像可省,实测是 Electron 启动时要扫的资源列表的一部分)。
- 不要直接从 `dist/assets`、`dist/oiagent-shell` 拷(`File /r "..\dist\assets"` + SetOutPath=$INSTDIR 会创建 `$INSTDIR\dist\` 残留),统一从 `installer/_staging/` 拷。
- 用户级安装(`RequestExecutionLevel user` + HKCU)免 UAC,适合个人用户桌面;企业环境改 admin + HKLM。

## 开发者模式(v2.0 起)

装包器勾选「开发者模式」组件后,会在 `$INSTDIR\dev\` 装入:

- `git-portable/` — Git 命令行运行时(~55 MB,无 GUI)
- `repo.zip` — 仓库源码(`git archive HEAD`,~2 MB,排除 `dist/node_modules/.venv`)
- `DEV_README.txt` — 解压/重打装包器操作指引

**托盘菜单自动出现「开发者模式」分组**(普通用户检测不到 `dev/git-portable/` 路径,菜单保持简洁):
- 打开开发者终端 — 启一个已配好 PATH 的 `cmd`,`git --version` 立即可用
- 查看开发者说明 — 系统应用打开 `DEV_README.txt`

**触发条件**(main.js: `devModeAvailable()`):
1. `$INSTDIR\dev\git-portable\` 存在
2. `$INSTDIR\dev\repo.zip` 存在

两条都满足才显示开发者模式菜单项,避免菜单点空跑。

**红线**:
- 开发者模式菜单只在检测到 dev 资源后才出现,不暴露给普通用户。
- 主进程读 `$INSTDIR\dev/` 只判「存不存在」,不暴露任何文件内容到渲染层。
- 启动开发者终端用 `git-portable.cmd` shim,内部把 `bin/ cmd/ mingw64/bin/ usr/bin/` 前置到 PATH,不动系统 PATH。

详见 `installer/prisirai.nsi` 第 [dev section] + `installer/_dev_assets/DEV_README.txt`。

实现细节见 `installer/prisirai.nsi` 头部注释。
