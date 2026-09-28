# install_wechat_publisher.ps1
# PrisirAI multi-platform publisher one-click installer (Easel + Playwright + wcdb-key-tool)
# P3j T10-F 2026-09-25
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\install_wechat_publisher.ps1
#   powershell -ExecutionPolicy Bypass -File .\install_wechat_publisher.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File .\install_wechat_publisher.ps1 -SkipEasel -SkipWcdb
#
# What it installs:
#   1) aiohttp / pyyaml (PrisirAI wechat-publisher backend)
#   2) playwright + Chromium browser
#   3) Easel repo (git clone --depth=1) to ~/work/zju_easel + its python deps
#   4) wcdb-key-tool Windows binary to ~/prisirmp_toolbin/

[CmdletBinding()]
param(
    [string]$EaselRoot = "$HOME\work\zju_easel",
    [string]$WcdbToolRoot = "$HOME\prisirmp_toolbin",
    [switch]$SkipEasel = $false,
    [switch]$SkipWcdb = $false,
    [switch]$DryRun = $false
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# --- helpers ---------------------------------------------------------------

function Write-Stage($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-OK($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}
function Write-Warn($msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}
function Write-Err($msg) {
    Write-Host "  [ERR] $msg" -ForegroundColor Red
}

function Test-PythonOnPath {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) {
        Write-Err "python not in PATH. Install Python 3.10+ first."
        return $false
    }
    $ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    Write-OK "Python $ver @ $($py.Source)"
    return $true
}

function Test-GitOnPath {
    $g = (Get-Command git -ErrorAction SilentlyContinue)
    if (-not $g) {
        Write-Warn "git not in PATH. Easel clone may fail."
        return $false
    }
    Write-OK "git @ $($g.Source)"
    return $true
}

function Install-PipIfNeeded {
    param([string[]]$Packages, [string]$Why = "")
    if ($Why) { Write-Stage "pip: $Why" }
    foreach ($pkg in $Packages) {
        $pipName = ($pkg -split '[><=!]')[0]
        # Suppress both streams (PowerShell 5.1 still shows RemoteException on stderr;
        # capture into a temp var instead and check $LASTEXITCODE).
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $sw = New-Object System.IO.StringWriter
        $oldOut = [Console]::Out
        $oldErr = [Console]::Error
        [Console]::SetOut($sw)
        [Console]::SetError($sw)
        try {
            & python -m pip show $pipName | Out-Null
        } finally {
            [Console]::SetOut($oldOut)
            [Console]::SetError($oldErr)
            $ErrorActionPreference = $prevEAP
        }
        if ($LASTEXITCODE -eq 0) {
            Write-OK "already installed: $pkg"
        } else {
            Write-Host "  + installing $pkg ..." -NoNewline
            if ($DryRun) {
                Write-Host " (dry-run)" -ForegroundColor Yellow
                continue
            }
            try {
                & python -m pip install --quiet --disable-pip-version-check $pkg
                Write-Host " done" -ForegroundColor Green
            } catch {
                Write-Err "$pkg install failed: $_"
            }
        }
    }
}

# --- 0. pre-check -----------------------------------------------------------

Write-Stage "Pre-check: Python / Git"
if (-not (Test-PythonOnPath)) { exit 2 }
$gitOk = Test-GitOnPath

# --- 1. backend deps --------------------------------------------------------

Install-PipIfNeeded -Packages @("aiohttp>=3.9", "pyyaml>=6.0") `
    -Why "PrisirAI publisher backend deps (aiohttp + pyyaml)"

# --- 2. playwright + chromium ----------------------------------------------

Install-PipIfNeeded -Packages @("playwright>=1.45") `
    -Why "Playwright (QR login + mp dashboard scraping)"

Write-Stage "Playwright Chromium"
try {
    $pw = & python -c "from playwright.sync_api import sync_playwright
import os
p=sync_playwright().start()
print(p.chromium.executable_path)
p.stop()" 2>$null
    if ($pw -and (Test-Path $pw)) {
        Write-OK "Chromium already installed: $pw"
    } else {
        Write-Host "  + python -m playwright install chromium ..." -NoNewline
        if ($DryRun) { Write-Host " (dry-run)" -ForegroundColor Yellow }
        else {
            & python -m playwright install chromium 2>&1 | Out-Null
            Write-Host " done" -ForegroundColor Green
        }
    }
} catch {
    Write-Warn "Chromium probe failed; will auto-download on first QR login."
}

# --- 3. Easel repo ----------------------------------------------------------

if (-not $SkipEasel) {
    Write-Stage "Easel repo (git clone --depth=1)"
    if (Test-Path "$EaselRoot\skills") {
        Write-OK "exists: $EaselRoot"
    } else {
        if (-not $gitOk) {
            Write-Err "no git; cannot clone Easel. Download zip manually to $EaselRoot"
        } else {
            Write-Host "  + git clone https://github.com/ZJU-REAL/Easel.git $EaselRoot ..." -NoNewline
            if ($DryRun) { Write-Host " (dry-run)" -ForegroundColor Yellow }
            else {
                New-Item -ItemType Directory -Force -Path (Split-Path $EaselRoot) | Out-Null
                & git clone --depth=1 https://github.com/ZJU-REAL/Easel.git $EaselRoot 2>&1 | Out-Null
                if (Test-Path "$EaselRoot\skills") { Write-Host " done" -ForegroundColor Green }
                else { Write-Err "clone failed - try manual zip download" }
            }
        }
    }
    if (Test-Path "$EaselRoot\skills") {
        Write-Stage "Easel Python dependencies"
        Install-PipIfNeeded -Packages @(
            "Pillow>=10",
            "opencv-python>=4.8",
            "PyYAML>=6.0",
            "markdown>=3.5",
            "jieba>=0.42",
            "snownlp>=0.12",
            "faster-whisper>=1.0",
            "edge-tts>=6.1"
        ) -Why "Easel mp scraper + formatter + TTS"
    }
} else {
    Write-Stage "Easel (skipped)"; Write-OK "SkipEasel=on, assume $EaselRoot is ready"
}

# --- 4. wcdb-key-tool (optional but recommended) ----------------------------

if (-not $SkipWcdb) {
    Write-Stage "wcdb-key-tool (Windows binary)"
    New-Item -ItemType Directory -Force -Path $WcdbToolRoot | Out-Null
    $candidates = @("$WcdbToolRoot\wcdb_key_tool_windows.py")
    $present = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($present) {
        Write-OK "exists: $present"
    } else {
        Write-Host "  + downloading wcdb-key-tool Windows .py ..." -NoNewline
        if ($DryRun) {
            Write-Host " (dry-run)" -ForegroundColor Yellow
            Write-Host "    manual URL: https://github.com/TANGandXUE/wcdb-key-tool/releases" -ForegroundColor Yellow
        } else {
            try {
                $rel = Invoke-RestMethod "https://api.github.com/repos/TANGandXUE/wcdb-key-tool/releases/latest"
                $asset = $rel.assets | Where-Object {
                    $_.name -match "windows" -and $_.name -match "\.py$"
                } | Select-Object -First 1
                if ($asset) {
                    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile "$WcdbToolRoot\wcdb_key_tool_windows.py"
                    Write-Host " done" -ForegroundColor Green
                    Write-OK "wcdb-key-tool -> $WcdbToolRoot\wcdb_key_tool_windows.py"
                } else {
                    Write-Warn "no windows .py asset found; download manually from release page"
                }
            } catch {
                Write-Warn "download failed (network/rate-limit): $_"
                Write-Host "    manual URL: https://github.com/TANGandXUE/wcdb-key-tool/releases" -ForegroundColor Yellow
            }
        }
    }
} else {
    Write-Stage "wcdb-key-tool (skipped)"; Write-OK "SkipWcdb=on"
}

# --- 5. next-steps ----------------------------------------------------------

Write-Stage "INSTALL DONE - next steps"
Write-Host ""
Write-Host "  1) Start publisher backend:" -ForegroundColor Cyan
Write-Host "     python companion\prisIragent-wechat-publisher.py --port 0"
Write-Host ""
Write-Host "  2) Open the Web UI (port from HKCU registry or stdout):" -ForegroundColor Cyan
Write-Host "     http://127.0.0.1:<port>/"
Write-Host ""
Write-Host "  3) First-time platform login:" -ForegroundColor Cyan
Write-Host "     Open the Settings tab -> click 'QR Login' for each platform"
Write-Host "     (mp dashboard / xiaohongshu / bilibili all use QR login)"
Write-Host ""
Write-Host "  4) Override EASEL_ROOT (optional):" -ForegroundColor Cyan
Write-Host '     set env var EASEL_ROOT, OR add to ~/.claude/settings.json:'
Write-Host '     {"easel":{"root": "D:\\path\\to\\Easel"}}'
Write-Host ""
Write-Host "  5) Post-install verify:" -ForegroundColor Cyan
Write-Host "     python verify_wechat_publisher.py"
Write-Host ""