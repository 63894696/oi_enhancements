[Console]::OutputEncoding = [Text.Encoding]::UTF8
$gbk = [Text.Encoding]::GetEncoding(936)
$lines = [System.IO.File]::ReadAllLines('C:\Users\Administrator\AppData\Local\Temp\drivers_v.csv', $gbk)
$total = $lines.Count - 1
$rows = @()
foreach ($ln in ($lines | Select-Object -Skip 1)) {
  $f = $ln -split '","'
  if ($f.Count -lt 6) { continue }
  $f = $f | ForEach-Object { $_.Trim('"') }
  $rows += [pscustomobject]@{
    Module  = $f[0]
    Display = $f[1]
    Type    = $f[3]
    Start   = $f[4]
    State   = $f[5]
    Status  = $f[6]
  }
}
$nr = $rows | Where-Object { $_.State -ne 'Running' }
Write-Output ("Total: $total  Parsed: $($rows.Count)  NotRunning: $($nr.Count)")
Write-Output "--- startmode distribution ---"
$nr | Group-Object Start | Sort-Object Count -Descending | ForEach-Object { Write-Output ("  [" + $_.Name + "] = " + $_.Count) }
Write-Output "--- type distribution ---"
$nr | Group-Object Type | Sort-Object Count -Descending | ForEach-Object { Write-Output ("  [" + $_.Name + "] = " + $_.Count) }
Write-Output "--- status != OK ---"
$bad = $nr | Where-Object { $_.Status -ne 'OK' -and $_.Status -ne '' }
Write-Output ("count: " + $bad.Count)
$bad | ForEach-Object { Write-Output ("  " + $_.Module + " | " + $_.Display + " | " + $_.Status) }
$nr | ConvertTo-Csv -NoTypeInformation | Out-File '.\nr.csv' -Encoding utf8
