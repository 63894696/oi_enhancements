# prisiragent-shell desktop shortcut (F7 对话壳)
# ASCII-safe filename avoids GBK/UTF-8 mojibake on Chinese Windows consoles.
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\Administrator\oi_enhancements\prisiragent-shell'
$ico = Join-Path $root 'icon.ico'
$electron = Join-Path $root 'node_modules\electron\dist\electron.exe'
$desktop = [Environment]::GetFolderPath('Desktop')

# 直连 electron.exe(GUI 子系统),不经过 cmd / npm → 启动无 CMD 黑窗。
# 第一个参数 '.' = 让 Electron 以当前目录(WorkingDirectory)为 app 路径加载 package.json。
$lnkName = 'Prisir AI.lnk'
$lnkPath = Join-Path $desktop $lnkName

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath = $electron
$sc.Arguments = '.'
$sc.WorkingDirectory = $root
$sc.IconLocation = "$ico,0"
$sc.WindowStyle = 1   # normal window(electron 自身是无控制台的 GUI 进程)
$sc.Description = 'Prisir AI - local chat shell (Electron)'
$sc.Save()

Write-Host ("shortcut: " + $lnkPath + " exists=" + (Test-Path -LiteralPath $lnkPath))
Write-Host ("electron: " + $electron + " exists=" + (Test-Path -LiteralPath $electron))
Write-Host ("icon: " + $ico + " exists=" + (Test-Path -LiteralPath $ico))
