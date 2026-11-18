# Personal Agent - Environment Setup (Windows)
# NOTE: English is used inside .ps1 files to avoid PowerShell 5.1 encoding
# issues with UTF-8 files without BOM; Persian documentation is in /docs.

param(
    [string]$Model = "qwen3:8b",
    [switch]$PullModel
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\..

Write-Host "=== Personal Agent - Environment Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Python version check (3.12 required) ---
Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $pythonCmd) {
    Write-Host "  Python not found. Install Python 3.12 from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
& python --version
Write-Host ""

# --- 2. Virtual environment ---
Write-Host "[2/5] Creating virtual environment (.venv)..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Failed to create venv." -ForegroundColor Red
        exit 1
    }
}
$venvPython = ".\.venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet
Write-Host "  OK"
Write-Host ""

# --- 3. Python dependencies ---
Write-Host "[3/5] Installing dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependency installation failed." -ForegroundColor Red
    exit 1
}
& $venvPython -m pip install pytest --quiet
Write-Host ""

# --- 4. Ollama check ---
Write-Host "[4/5] Checking Ollama..." -ForegroundColor Yellow
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    Write-Host "  Ollama not found on PATH." -ForegroundColor Red
    Write-Host "  Install it from https://ollama.com/download then re-run this script."
    Write-Host "  (The app itself also reports this gracefully at runtime.)"
} else {
    & ollama --version
    & ollama list
    if ($PullModel) {
        Write-Host "  Pulling model: $Model ..." -ForegroundColor Yellow
        & ollama pull $Model
    }
}
Write-Host ""

# --- 5. Final environment check ---
Write-Host "[5/5] Running environment check..." -ForegroundColor Yellow
& $venvPython scripts\check_environment.py
$checkExit = $LASTEXITCODE

Write-Host ""
Write-Host "Setup finished. To start the app run:  .\scripts\run.ps1" -ForegroundColor Green
exit $checkExit
