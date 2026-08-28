"""setup_llama_server.ps1 — Download MiniCPM-V-4.6 models and configure local server

Run as: powershell -ExecutionPolicy Bypass -File setup_llama_server.ps1
"""

$ErrorActionPreference = "Stop"

$MODELS_DIR = Join-Path $PSScriptRoot "models"
$MODEL_FILE = "MiniCPM-V-4_6-Q4_K_M.gguf"
$MMPROJ_FILE = "mmproj-model-f16.gguf"
$MODEL_URL = "https://huggingface.co/openbmb/MiniCPM-V-4.6-GGUF/resolve/main/MiniCPM-V-4_6-Q4_K_M.gguf"
$MMPROJ_URL = "https://huggingface.co/openbmb/MiniCPM-V-4.6-GGUF/resolve/main/mmproj-model-f16.gguf"

Write-Host "=== MiniCPM-V-4.6 Local Setup ===" -ForegroundColor Cyan
Write-Host ""

# Create models directory
if (-not (Test-Path $MODELS_DIR)) {
    New-Item -ItemType Directory -Path $MODELS_DIR | Out-Null
    Write-Host "[+] Created models directory: $MODELS_DIR"
}

# Download model
$MODEL_PATH = Join-Path $MODELS_DIR $MODEL_FILE
if (-not (Test-Path $MODEL_PATH)) {
    Write-Host "[+] Downloading MiniCPM-V-4.6 Q4_K_M (~505 MB)..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $MODEL_URL -OutFile $MODEL_PATH -UseBasicParsing
    Write-Host "[+] Model downloaded: $MODEL_PATH"
    $size = (Get-Item $MODEL_PATH).Length / 1MB
    Write-Host "    Size: $([math]::Round($size, 1)) MB"
} else {
    Write-Host "[~] Model already exists: $MODEL_PATH"
}

# Download mmproj
$MMPROJ_PATH = Join-Path $MODELS_DIR $MMPROJ_FILE
if (-not (Test-Path $MMPROJ_PATH)) {
    Write-Host "[+] Downloading mmproj-model-f16.gguf (~1.1 GB)..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $MMPROJ_URL -OutFile $MMPROJ_PATH -UseBasicParsing
    Write-Host "[+] mmproj downloaded: $MMPROJ_PATH"
    $size = (Get-Item $MMPROJ_PATH).Length / 1MB
    Write-Host "    Size: $([math]::Round($size, 1)) MB"
} else {
    Write-Host "[~] mmproj already exists: $MMPROJ_PATH"
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "To start the server, run:" -ForegroundColor White
Write-Host "  python llama_local_server.py --model models\$MODEL_FILE --mmproj models\$MMPROJ_FILE"
Write-Host ""
Write-Host "Or in background:" -ForegroundColor White
Write-Host "  Start-Process python -ArgumentList 'llama_local_server.py', '--model', 'models\$MODEL_FILE', '--mmproj', 'models\$MMPROJ_FILE' -WindowStyle Hidden"
Write-Host ""
Write-Host "API endpoint: http://127.0.0.1:8099/v1/chat/completions"
Write-Host "Health check: http://127.0.0.1:8099/health"
