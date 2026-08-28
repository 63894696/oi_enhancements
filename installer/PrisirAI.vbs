' PrisirAI.vbs — 无窗启动器,替代 launcher.bat 作为快捷方式目标。
' 2026-08-24 新增:launcher.bat 是控制台脚本,启动时会闪 CMD 黑窗;
' 改用 VBS 调 WScript.Shell.Run 0(隐藏窗口)彻底消除。
'
' 原理:
'   1. 解析脚本自身位置 → $INSTDIR
'   2. 构造 electron.exe 完整命令行
'   3. WScript.Shell.Run cmd, 0(隐藏), False(不等待)

Dim shell, fso, scriptDir, electronExe, shellDir, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' 脚本自身所在目录 = $INSTDIR
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
electronExe = scriptDir & "\oiagent-shell\node_modules\electron\dist\electron.exe"
shellDir = scriptDir & "\oiagent-shell"

' 切到 oiagent-shell 目录再启动 electron(等价于 launcher.bat 的 cd /d)
shell.CurrentDirectory = shellDir

' 0 = 隐藏窗口, False = 不等待子进程
shell.Run """" & electronExe & """ """ & shellDir & """", 0, False

Set shell = Nothing
Set fso = Nothing
