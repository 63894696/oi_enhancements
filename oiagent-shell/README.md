# oiagent-shell — oiagent 本地对话壳(Electron)

不启用浏览器也能和 oiagent 对话。主进程 spawn 并看护 `oiagent_web.py`
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
