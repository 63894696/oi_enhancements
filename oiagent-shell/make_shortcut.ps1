# oiagent-shell desktop shortcut (F7 对话壳)
# ASCII-safe filename avoids GBK/UTF-8 mojibake on Chinese Windows consoles.
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\Administrator\oi_enhancements\oiagent-shell'
$ico = Join-Path $root 'icon.ico'
$desktop = [Environment]::GetFolderPath('Desktop')

# Launch via cmd: cd into shell dir, npm start (electron .). Minimized window.
$lnkName = 'oiagent Shell.lnk'
$lnkPath = Join-Path $desktop $lnkName

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
$sc.Arguments = '/c cd /d "' + $root + '" && npm start'
$sc.WorkingDirectory = $root
$sc.IconLocation = "$ico,0"
$sc.WindowStyle = 7   # minimized (electron opens its own window)
$sc.Description = 'oiagent Shell - local chat shell (Electron)'
$sc.Save()

Write-Host ("shortcut: " + $lnkPath + " exists=" + (Test-Path -LiteralPath $lnkPath))
Write-Host ("icon: " + $ico + " exists=" + (Test-Path -LiteralPath $ico))
