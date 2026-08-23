; PrisirAI v1.0 NSIS 安装脚本
;
; 目标:把 dist/PrisirAI.exe + dist/assets + oiagent-shell/ 装到目标机,
; 创建桌面 + 开始菜单快捷方式(指向 launcher.bat,图标 oiagent-shell/icon.ico),
; 注册卸载器。
;
; 编译: makensis prisirai.nsi → ../dist/PrisirAI-Setup-1.0.0.exe

Unicode True

; -------- 元信息 --------
!define APP_NAME "PrisirAI"
!define APP_VERSION "1.0.0"
!define APP_PUBLISHER "Prisir"
!define APP_EXE "PrisirAI.exe"
!define INSTALL_DIR "$LOCALAPPDATA\Programs\PrisirAI"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\dist\PrisirAI-Setup-${APP_VERSION}.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallLocation"
RequestExecutionLevel user
ShowInstDetails show
ShowUninstDetails show

; -------- 现代 UI --------
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "..\oiagent-shell\icon.ico"
!define MUI_UNICON "..\oiagent-shell\icon.ico"

!define MUI_WELCOMEPAGE_TITLE "${APP_NAME} 安装向导"
!define MUI_WELCOMEPAGE_TEXT "本向导将引导您安装 ${APP_NAME} ${APP_VERSION}。$\r$\n$\r$\n${APP_NAME} 是 Prisir 出品的本地对话助手。$\r$\n$\r$\n点击下一步继续。"
!define MUI_FINISHPAGE_TITLE "${APP_NAME} 安装完成"
!define MUI_FINISHPAGE_TEXT "${APP_NAME} 已安装到您的电脑。$\r$\n$\r$\n点击「完成」关闭本向导。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\launcher.bat"
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\README.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "显示安装说明"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

; 开发者模式可选组件页(勾选后安装 repo.zip + git-portable)
!define MUI_COMPONENTSPAGE_TEXT_DESC "${APP_NAME} 开发者模式:安装 git-portable 运行时 + 仓库源码 zip,用于本地修改与调试。不勾选不影响正常使用。"
!define MUI_COMPONENTSPAGE_TEXT_SUBTITLE "开发者模式仅供开发者使用,普通用户请勿勾选。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

; -------- 安装 section --------
; v2.0 拆分:核心区必装(RO section,/S 静默只装它),开发者区可选(用户勾选)。
Section "-${APP_NAME} 核心 (必装)"
  SectionIn RO

  ; 后端 exe(直接拷已构建产物)
  SetOutPath "$INSTDIR"
  File "..\dist\PrisirAI.exe"

  ; 资源 — 从 installer/_staging/ 拷,不在 SetOutPath 下创建 dist/ 残留。
  ; (NSIS File /r src 永远以 src 同名子目录形态拷到 SetOutPath;
  ;  若直接从 ..\dist\assets 拷,会和 SetOutPath 解析时创建 $INSTDIR\dist 路径冲突。
  ;  .tmp_stage.py 已把 dist/assets + dist/oiagent-shell 同步到 _staging/。)
  SetOutPath "$INSTDIR"
  File /r "_staging\assets"
  File /r "_staging\oiagent-shell"

  ; 启动器(快捷方式目标)
  SetOutPath "$INSTDIR"
  File "launcher.bat"
  File "README.txt"
  File "LICENSE.txt"

  ; 审计/日志目录(空目录)
  CreateDirectory "$INSTDIR\logs"

  ; 工作目录(用户文档)
  CreateDirectory "$DOCUMENTS\PrisirAI"

  ; 注册表卸载信息
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
  ; 标记:开发者模式是否已安装(决定卸载时是否清 dev/)
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoDev" "1"

  ; 桌面快捷方式
  CreateShortcut "$DESKTOP\Prisir AI.lnk" \
    "$INSTDIR\launcher.bat" "" "$INSTDIR\oiagent-shell\icon.ico" 0

  ; 开始菜单
  CreateDirectory "$SMPROGRAMS\PrisirAI"
  CreateShortcut "$SMPROGRAMS\PrisirAI\Prisir AI.lnk" \
    "$INSTDIR\launcher.bat" "" "$INSTDIR\oiagent-shell\icon.ico" 0

  ; 写卸载器(放在最后,这样 Uninstall.exe 不会与 $INSTDIR 冲突)
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ; 卸载器快捷方式(放到开始菜单,卸载入口)
  CreateShortcut "$SMPROGRAMS\PrisirAI\卸载 ${APP_NAME}.lnk" \
    "$INSTDIR\Uninstall.exe"
SectionEnd

; 开发者模式可选 section(repo.zip + git-portable + dev\ 子目录)
; 卸载时:检注册表 NoDev 标志决定是否删 dev/(默认总是删)。
Section /o "g ${APP_NAME} 开发者模式 (git-portable + 源码)"
  SetOutPath "$INSTDIR\dev"
  ; git-portable 子树 — 用户装包后可加进 PATH 直接用 `git ...`
  File /r "_dev_assets\git-portable"
  ; 仓库源码 zip(约 2 MB,不含 dist/node_modules/.venv)
  File "_dev_assets\repo.zip"
  ; 开发者模式说明
  File "_dev_assets\DEV_README.txt"
  ; 标记开发者模式已装(给卸载器用)
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoDev" "0"
  ; 开发快捷方式:命令行(打开已配好 git PATH 的 cmd)
  CreateShortcut "$SMPROGRAMS\PrisirAI\Prisir AI 开发者终端.lnk" \
    "$INSTDIR\dev\git-portable\git-portable.cmd" "" "$INSTDIR\dev\git-portable\git-portable.cmd" 0
SectionEnd

Section "Uninstall"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  Delete "$DESKTOP\Prisir AI.lnk"
  Delete "$SMPROGRAMS\PrisirAI\Prisir AI.lnk"
  Delete "$SMPROGRAMS\PrisirAI\Prisir AI 开发者终端.lnk"
  Delete "$SMPROGRAMS\PrisirAI\卸载 ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\PrisirAI"
  RMDir /r "$INSTDIR"
SectionEnd
