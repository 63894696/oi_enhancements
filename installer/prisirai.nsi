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
!define APP_VERSION "2.6.0"
!define APP_PUBLISHER "Prisir(湃睿思)"
!define APP_EXE "PrisirAI.exe"
; 对外品牌显示名(窗口/对话框用),区别于 APP_NAME(exe/目录/注册表内部标识符,不动)
!define APP_BRAND "Prisir(湃睿思) AI"
; 2026-08-24 改:默认安装目录从 $LOCALAPPDATA\Programs 改成 $PROGRAMFILES64
; (用户反馈:和别的程序默认不一样,应该用 Program Files)
!define INSTALL_DIR "$PROGRAMFILES64\PrisirAI"

Name "${APP_BRAND} ${APP_VERSION}"
OutFile "..\dist\PrisirAI-Setup-${APP_VERSION}.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallLocation"
; 2026-08-24 改:装到 Program Files 需要管理员权限
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show

; -------- 现代 UI --------
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "..\oiagent-shell\icon.ico"
!define MUI_UNICON "..\oiagent-shell\icon.ico"

; MUI 界面文案走 LangString(安装器运行时按 $LANGUAGE 取值);LangString 在 MUI_LANGUAGE 后定义

; 2026-08-25 砍:开发者模式组件页 + repo.zip + git-portable(用户拍板做纯用户安装包)。
;   原因:repo.zip 会带入内部开发资料(如 Prisir 密信威胁模型),不该进给用户的安装包。
;   只做核心用户安装,不再有「开发者模式」可选组件。
; 2026-08-25 多语言:按系统语言自动选安装界面语言(中文系统→中文,其他→英文)。
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; 2026-08-25 多语言:自定义文案按界面语言走 LangString(中文/英文双语);须在 MUI_LANGUAGE 后
LangString WELCOME_TITLE ${LANG_SIMPCHINESE} "${APP_BRAND} 安装向导"
LangString WELCOME_TITLE ${LANG_ENGLISH} "${APP_BRAND} Setup Wizard"
LangString WELCOME_TEXT ${LANG_SIMPCHINESE} "本向导将引导您安装 ${APP_BRAND} ${APP_VERSION}。$\r$\n$\r$\n${APP_BRAND} 是 Prisir(湃睿思) 出品的本地对话助手。$\r$\n$\r$\n点击下一步继续。"
LangString WELCOME_TEXT ${LANG_ENGLISH} "This wizard will guide you through the installation of ${APP_BRAND} ${APP_VERSION}.$\r$\n$\r$\n${APP_BRAND} is a local conversational assistant by Prisir.$\r$\n$\r$\nClick Next to continue."
LangString FINISH_TITLE ${LANG_SIMPCHINESE} "${APP_BRAND} 安装完成"
LangString FINISH_TITLE ${LANG_ENGLISH} "${APP_BRAND} Setup Complete"
LangString FINISH_TEXT ${LANG_SIMPCHINESE} "${APP_BRAND} 已安装到您的电脑。$\r$\n$\r$\n点击「完成」关闭本向导。"
LangString FINISH_TEXT ${LANG_ENGLISH} "${APP_BRAND} has been installed on your computer.$\r$\n$\r$\nClick Finish to close this wizard."
LangString FINISH_RUN_TEXT ${LANG_SIMPCHINESE} "启动 ${APP_BRAND}"
LangString FINISH_RUN_TEXT ${LANG_ENGLISH} "Launch ${APP_BRAND}"
LangString FINISH_README_TEXT ${LANG_SIMPCHINESE} "显示安装说明"
LangString FINISH_README_TEXT ${LANG_ENGLISH} "Show installation notes"
LangString SECTION_CORE ${LANG_SIMPCHINESE} "-${APP_NAME} 核心 (必装)"
LangString SECTION_CORE ${LANG_ENGLISH} "-${APP_NAME} Core (required)"
LangString UNINSTALL_SHORTCUT ${LANG_SIMPCHINESE} "卸载 ${APP_BRAND}"
LangString UNINSTALL_SHORTCUT ${LANG_ENGLISH} "Uninstall ${APP_BRAND}"
LangString UNINST_DEL_DATA ${LANG_SIMPCHINESE} "是否同时删除对话记录和本地数据?$\r$\n$\r$\n选择「是」将删除:$\r$\n  $PROFILE\.local\share\prisir\(对话历史、配置等)$\r$\n$\r$\n选择「否」保留数据,仅删除程序文件。"
LangString UNINST_DEL_DATA ${LANG_ENGLISH} "Delete conversation history and local data as well?$\r$\n$\r$\nChoosing Yes deletes:$\r$\n  $PROFILE\.local\share\prisir\(chat history, config, etc.)$\r$\n$\r$\nChoose No to keep your data and remove only program files."

; 2026-08-25 新增:安装/卸载前检测运行中实例。
; 根因(VM001 冒烟实测):正在运行的 PrisirAI.exe 会锁住文件,NSIS 的 File/Delete 对
; 被锁文件「静默跳过」——不报错不提示,装出「旧 core + 新 reg 版本号」的新旧混合体;
; 卸载同理,core exe 残留。且关窗口只关前端,后端 PrisirAI.exe(console=False)仍驻留。
; 故安装/卸载前都检测运行实例,征得用户同意后 taskkill 结束,再继续。
LangString PROC_RUNNING ${LANG_SIMPCHINESE} "检测到 ${APP_BRAND} 正在运行。$\r$\n$\r$\n继续前需要先结束它(否则文件被占用会导致安装/卸载不完整)。$\r$\n$\r$\n是否现在结束 ${APP_BRAND} 进程?$\r$\n  「是」= 结束后继续$\r$\n  「否」= 中止操作"
LangString PROC_RUNNING ${LANG_ENGLISH} "${APP_BRAND} is currently running.$\r$\n$\r$\nIt must be closed before continuing (locked files would leave the install/uninstall incomplete).$\r$\n$\r$\nClose ${APP_BRAND} now?$\r$\n  Yes = close it and continue$\r$\n  No = abort"

; ---------------------------------------------------------------------------
; EnsureNotRunning:检测运行中的 PrisirAI 实例,征得同意后结束,否则中止。
; 实现:tasklist 按映像名过滤检测;taskkill /F /T 结束后端 exe + electron 壳进程树。
;   用 nsExec::Exec 静默执行(不弹黑窗)。进程不在则 tasklist 无匹配,直接跳过。
;   用户选「否」→ Quit(安装中止/卸载中止)。
; 安装/卸载共用一套逻辑,用 !define + !macro 生成 install(un="")与 uninstall(un="un.")两版。
; ---------------------------------------------------------------------------
!macro _EnsureNotRunningBody UN
Function ${UN}EnsureNotRunning
  ${UN}retry_check:
    ; 检测:PowerShell 单行(无 cmd 管道——nsExec 里 cmd 的 ^| ^& 转义不可靠,实测 tasklist|find
    ;   报「无效参数 |」)。Get-Process 找到 PrisirAI → Write-Output RUNNING;否则 ABSENT。
    nsExec::ExecToStack 'powershell -NoProfile -Command "if(Get-Process PrisirAI -ErrorAction SilentlyContinue){Write-Output RUNNING}else{Write-Output ABSENT}"'
    Pop $0  ; 退出码(不作判据)
    Pop $1  ; 输出文本:"RUNNING" 或 "ABSENT"(带 CRLF)
    ; 取前 7 字符与 "RUNNING" 比较(避开尾部 CRLF 干扰,纯 StrCpy+StrCmp 零外部函数依赖)
    StrCpy $2 $1 7
    ${If} $2 == "RUNNING"
      ; 在跑 → 征得同意
      MessageBox MB_YESNO|MB_ICONEXCLAMATION "$(PROC_RUNNING)" IDYES ${UN}do_kill
      ; 选否 → 中止
      Quit
      ${UN}do_kill:
        ; 结束后端 PrisirAI.exe
        nsExec::ExecToStack 'powershell -NoProfile -Command "Get-Process PrisirAI -ErrorAction SilentlyContinue | Stop-Process -Force"'
        Pop $0
        Pop $1
        ; electron 壳可能独立驻留(以 electron.exe 映像名跑),一并结束
        nsExec::ExecToStack 'powershell -NoProfile -Command "Get-Process electron -ErrorAction SilentlyContinue | Stop-Process -Force"'
        Pop $0
        Pop $1
        ; 等句柄释放(进程退出到文件锁释放有毫秒级延迟),再复查一次
        Sleep 1500
        Goto ${UN}retry_check
    ${EndIf}
FunctionEnd
!macroend
; 顶层展开两次:生成 EnsureNotRunning(安装) 与 un.EnsureNotRunning(卸载)
!insertmacro _EnsureNotRunningBody ""
!insertmacro _EnsureNotRunningBody "un."

; 安装时按系统默认 UI 语言自动选界面语言(中文系统→中文,其他→英文)。
Function .onInit
  System::Call 'kernel32::GetSystemDefaultUILanguage() i .r0'
  ; LANGID 低 10 位是主语言 ID:中文 = 0x04 (LANG_CHINESE)
  IntOp $1 $0 & 0x3FF
  ${If} $1 == 4
    StrCpy $LANGUAGE ${LANG_SIMPCHINESE}
  ${Else}
    StrCpy $LANGUAGE ${LANG_ENGLISH}
  ${EndIf}
  ; 安装前:检测并结束运行中的 PrisirAI 实例(防装出新旧混合体)
  Call EnsureNotRunning
FunctionEnd

Function un.onInit
  System::Call 'kernel32::GetSystemDefaultUILanguage() i .r0'
  IntOp $1 $0 & 0x3FF
  ${If} $1 == 4
    StrCpy $LANGUAGE ${LANG_SIMPCHINESE}
  ${Else}
    StrCpy $LANGUAGE ${LANG_ENGLISH}
  ${EndIf}
  ; 卸载前:检测并结束运行中的 PrisirAI 实例(防 core exe 残留)
  Call un.EnsureNotRunning
FunctionEnd

; -------- 安装 section --------
; 2026-08-25:纯用户安装包,只有核心区(RO)。开发者模式已砍(见文件头注记)。
Section "$(SECTION_CORE)"
  SectionIn RO

  ; 后端 exe(直接拷已构建产物)
  SetOutPath "$INSTDIR"
  File "..\dist\PrisirAI.exe"

  ; 资源 — 从 installer/_staging2/ 拷(2026-08-25 重建的干净 staging)。
  ; 旧 _staging/ 曾被手工 cp -r 弄出嵌套 oiagent-shell\oiagent-shell,且其 default_app.asar
  ; 被残留 electron 进程持久锁住删不掉、/x 排除也不可靠 → 直接从干净源(仓库根 oiagent-shell)
  ; 重建全新 _staging2/,根源无嵌套,无需任何 /x 排除或编译期删除。
  SetOutPath "$INSTDIR"
  File /r "_staging2\assets"
  File /r "_staging2\oiagent-shell"

  ; 启动器(快捷方式目标)
  SetOutPath "$INSTDIR"
  File "launcher.bat"
  File "PrisirAI.vbs"
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
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_BRAND}"

  ; 桌面快捷方式(2026-08-24 改:指向 PrisirAI.vbs 无窗启动,不再闪 CMD 黑窗)
  CreateShortcut "$DESKTOP\Prisir AI.lnk" \
    "$INSTDIR\PrisirAI.vbs" "" "$INSTDIR\oiagent-shell\icon.ico" 0

  ; 开始菜单
  CreateDirectory "$SMPROGRAMS\PrisirAI"
  CreateShortcut "$SMPROGRAMS\PrisirAI\Prisir AI.lnk" \
    "$INSTDIR\PrisirAI.vbs" "" "$INSTDIR\oiagent-shell\icon.ico" 0

  ; 写卸载器(放在最后,这样 Uninstall.exe 不会与 $INSTDIR 冲突)
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ; 卸载器快捷方式(放到开始菜单,卸载入口)
  CreateShortcut "$SMPROGRAMS\PrisirAI\$(UNINSTALL_SHORTCUT).lnk" \
    "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  Delete "$DESKTOP\Prisir AI.lnk"
  Delete "$SMPROGRAMS\PrisirAI\Prisir AI.lnk"
  Delete "$SMPROGRAMS\PrisirAI\卸载 ${APP_BRAND}.lnk"
  Delete "$SMPROGRAMS\PrisirAI\Uninstall ${APP_BRAND}.lnk"
  RMDir "$SMPROGRAMS\PrisirAI"
  RMDir /r "$INSTDIR"

  ; 2026-08-24 新增:卸载时询问是否删除对话记录和本地数据
  ; 默认保留(安全),用户确认才删 ~/.local/share/prisir/
  MessageBox MB_YESNO|MB_ICONQUESTION "$(UNINST_DEL_DATA)" IDNO skip_delete_data
    RMDir /r "$PROFILE\.local\share\prisir"
  skip_delete_data:
SectionEnd
