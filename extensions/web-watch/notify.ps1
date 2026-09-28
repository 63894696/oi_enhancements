# notify.ps1 — 手动调 Windows toast 通知(本扩展辅助脚本)
# 用法: powershell -File notify.ps1 "title" "message"
# 不依赖 BurntToast,走系统自带 Windows.UI.Notifications。
# Windows 8+ / Server 2012+ 即可;Win10/11 直接弹右下角。

param(
    [Parameter(Mandatory=$true)][string]$Title,
    [Parameter(Mandatory=$true)][string]$Message
)

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
    $nodes = $xml.GetElementsByTagName('text')
    $safeTitle = ($Title -replace "'", "''").Substring(0, [Math]::Min(120, $Title.Length))
    $safeMsg = ($Message -replace "'", "''").Substring(0, [Math]::Min(100, $Message.Length))
    $nodes[0].AppendChild($xml.CreateTextNode($safeTitle)) | Out-Null
    $nodes[1].AppendChild($xml.CreateTextNode($safeMsg)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('PrisirAI').Show($toast)
    exit 0
} catch {
    Write-Host ("toast failed: " + $_.Exception.Message) -ForegroundColor Yellow
    exit 0
}
