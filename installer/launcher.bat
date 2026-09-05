@echo off
REM PrisirAI v1.0 启动器 — 装包器创建的桌面快捷方式目标。
REM 切到 prisiragent-shell 目录,用内置 Electron 加载 main.js → 弹窗口。
REM 若已运行的 PrisirAI.exe 监听 18802,Electron 检测到端口在用会直接加载,不会重复起后端。
REM 2026-08-24 修:用 start /b 避免 CMD 黑窗闪现(后台启动,不占用当前控制台)。

cd /d "%~dp0prisiragent-shell"
start "" /b "%~dp0prisiragent-shell\node_modules\electron\dist\electron.exe" "%~dp0prisiragent-shell"
