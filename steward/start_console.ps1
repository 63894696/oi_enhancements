# Prisir steward console one-click starter (tunnel + web)
$ssh = "C:\Windows\System32\OpenSSH\ssh.exe"
$key = "$env:USERPROFILE\.ssh\id_ed25519"

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:18826/health" -TimeoutSec 3 -UseBasicParsing
    Write-Host "[tunnel] already up"
} catch {
    Write-Host "[tunnel] starting..."
    Start-Process -FilePath $ssh -ArgumentList @(
        "-i", $key, "-p", "49108",
        "-L", "18826:127.0.0.1:18816",
        "-N", "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=no",
        "root@192.220.14.165"
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:18860/api/health" -TimeoutSec 3 -UseBasicParsing
    Write-Host "[console] already up"
} catch {
    Write-Host "[console] starting..."
    Start-Process -FilePath "python" -ArgumentList @(
        "C:\Users\Administrator\oi_enhancements\steward\steward_console.py"
    ) -WorkingDirectory "C:\Users\Administrator\oi_enhancements" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:18860/api/health" -TimeoutSec 5 -UseBasicParsing
    Write-Host "[ready] $($r.Content)"
    Start-Process "http://127.0.0.1:18860"
} catch {
    Write-Host "[FAIL] console not ready" -ForegroundColor Red
}
