# P2.5+18 装包后 UI 验收清单(2026-09-22)

## 静态扫覆盖(10/10 绿)

`tests/_test_p2b18_installer_ui_audit.py` 覆盖装包路径完整性 + 装包脚本配置正确性。

| # | 验收项 | 结果 |
|---|--------|------|
| ① | PyInstaller 主壳 `PrisirAI.exe` 存在且 > 100MB | ✓ 396.7MB |
| ② | PyInstaller music 子 exe `PrisirAI-music-web.exe` > 50MB | ✓ 93.5MB |
| ③ | Tauri shell `prisirai-shell.exe` > 10MB | ✓ 14.1MB |
| ④ | NSIS installer 拷贝 Tauri + music + assets + bin 4 大产物 | ✓ |
| ⑤ | `prisIrai_config.yaml` ports/brand/forum 三段 + 端口默认对齐(18802/18803) | ✓ |
| ⑥ | Tauri tray 菜单 7 项齐全(打开/陪聊/音乐/📅日历/歌词/自启/退出) | ✓ |
| ⑦ | Tauri calendar 路由走 `/prisiragent/calendar` + 独立端口 18803 + module + tray 调起 | ✓ |
| ⑧ | Electron main.js dev 模式子窗口 + tray 3 组 submenu(P2.5+16/17 配套) | ✓ |
| ⑨ | NSIS 创建桌面 + 开始菜单快捷方式(指向 Tauri exe) | ✓ |
| ⑩ | NSIS Uninstall 注册表(InstallLocation/DisplayVersion/Publisher/DisplayName) | ✓ |

## 装包路径拓扑

```
NSIS installer (installer/prisirai.nsi)
  ├─ src/  $INSTDIR\ (装包根)
  │   ├─ prisirai-shell.exe     (Tauri 主壳, 14MB)
  │   ├─ PrisirAI.exe            (PyInstaller 后端, 397MB, 含 litellm+rapidocr)
  │   ├─ PrisirAI-music-web.exe  (PyInstaller 音乐子进程, 94MB)
  │   ├─ PrisirVcsTool.exe       (VCS 子 exe)
  │   ├─ bin/
  │   │   ├─ git/                (便携 git)
  │   │   └─ officecli.exe       (Office 自动化)
  │   ├─ assets/                 (图标/主题/山水背景)
  │   └─ prisIrai_config.yaml    (端口/品牌/论坛 URL 三段)
  └─ 桌面/开始菜单快捷方式 → prisirai-shell.exe
```

## 用户侧 UX 验证清单(浏览器手工跑)

装包后用户首次启动 = 双击桌面 `PrisirAI` 快捷方式,自动拉起 Tauri shell + 后端 Python + music 子 exe + (可选)calendar 子 exe。

### 1. 主壳启动 + 托盘
- [ ] 桌面双击 `PrisirAI` → Tauri 窗口弹出,加载 WebView
- [ ] 任务栏右下角托盘图标出现(🔥 PrisirAI 火苗图标)
- [ ] 右键托盘 = 7 项菜单(打开 PrisirAI / 启动陪聊 / 启动音乐播放器 / 📅 打开日历 / 桌面歌词 / 开机自启 / 退出)

### 2. 顶栏按钮(主窗口内)
- [ ] 顶栏出现 🔀 工作流 / 📂 项目 / 🧩 扩展 等按钮(跟随 product 配置)
- [ ] 点 🔀 工作流 → 全屏 wfmodal(任务列表 + DAG 画布 + 运行历史)
- [ ] 点 📂 项目 → projmodal 项目列表

### 3. 子窗口(托盘菜单触发)
- [ ] 托盘点「启动陪聊」→ 弹独立子窗(语伴,920×680,跟主窗分开)
- [ ] 托盘点「启动音乐播放器」→ 弹独立子窗(音乐,880×620)
- [ ] 托盘点「📅 打开日历」→ 启动 calendar 子进程 + 弹独立子窗(960×720)
- [ ] 关子窗后再次点托盘 → 同 label 子窗秒级重用(close 仅 hide, 不真销毁)
- [ ] 托盘点「桌面歌词」→ 启动桌面歌词浮窗
- [ ] 托盘点「开机自启」checkbox 勾上 → 重启后托盘自动弹出

### 4. 日历子进程独立端口
- [ ] 日历 URL `http://127.0.0.1:18803/prisiragent/calendar`(不走主 web 18802)
- [ ] 主 web 跟日历 web 是同进程双端口 listen(prisIragent_web.py + CalendarHandler)
- [ ] 任一端口冲突 → 自动 fallback 到主端口 + 不崩

### 5. PyInstaller 后端启动
- [ ] 启动后 ~3s 内 `http://127.0.0.1:18802/prisiragent/` 可访问
- [ ] 健康检查 `http://127.0.0.1:18802/prisiragent/api/port_status` 返 `{web.enabled:true, calendar.enabled:true}`
- [ ] 后端 log 落 `~/.prisir/logs/` 路径可查

### 6. 卸载流程
- [ ] 控制面板 → PrisirAI → 卸载 → NSIS uninstaller 弹窗
- [ ] 卸载完,`$INSTDIR` 文件夹清空(可选,看 NSIS 是否包含 DeleteSection)
- [ ] 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\PrisirAI` 键删除
- [ ] 桌面 + 开始菜单快捷方式删除

## 已知差异(装包后 vs 开发模式)

| 维度 | 开发模式(`node main.js`)| 装包后(NSIS) |
|------|---------------------|--------------|
| 主壳 | Electron BrowserWindow | Tauri WebView(更轻) |
| tray 菜单 | 3 组 submenu(P2.5+17) | flat 7 项(Tauri 2.x 无 SubmenuBuilder)|
| 子窗口 | Electron 4 BrowserWindow 实例 | Tauri 在主 WebView 内开新窗口(需 tauri.conf.json 配置)|
| 日历路由 | 同进程双端口 listen | 同进程双端口 listen(P2.5+14)|
| PyInstaller 后端 | `python prisIragent_web.py` | `PrisirAI.exe` frozen |
| 日志路径 | `./logs/` | `~/.prisir/logs/` |
| config.yaml | 仓库根 | `$INSTDIR\prisIrai_config.yaml` |

> tray 形态差异是 Tauri 2.x 限制(无 SubmenuBuilder),P2.5+17 的 3 组 submenu 是 Electron 壳专属 UX。装包后用户看到的是 Tauri flat 7 项,功能等价(打开/陪聊/音乐/日历/歌词/自启/退出)。

## 风险点

1. **NSIS 体积**:PyInstaller 后端 397MB + music 94MB + Tauri 14MB → 装包 ~600MB,首次下/装时间长
2. **资源路径**:装包后 `prisIrai_config.yaml` 在 `$INSTDIR\`,开发模式在仓库根,二者读路径统一走 `config_loader.py`
3. **Windows Defender 误杀**:`PrisirAI.exe` 是 PyInstaller frozen,部分杀软会拦 — 已 ship 主流版本 SHA 白名单(留作后续)
4. **子窗口多开**:Tauri 2.x 多窗口需要 tauri.conf.json `windows[]` 配置 + 代码调 `WebviewWindow.new`,Electron 壳的 4 子窗模型在 Tauri 侧需重映射

## 不做(留给后续)

- ❌ NSIS 装包体积优化(UPX 压缩 + lazy load)
- ❌ 子 exe SHA256 manifest + 自动校验
- ❌ Tauri 子窗口重构(P2.5+17 子窗模型迁移)
- ❌ Windows Defender 误杀白名单申请
- ❌ 安装包静默升级(目前是手动卸载+重装)
- ❌ 多语言 NSIS 文案(目前 zh-CN 单语言)
- ❌ 自动重装检测(同版本重装不覆盖)

## 解锁子任务

- P2.5+19(下一阶段):Electron 壳 vs Tauri 壳统一化(用 Tauri 2.x 的 `WebviewWindow.new` 替代 Electron 4 BrowserWindow 模型)