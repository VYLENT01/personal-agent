# Personal Agent - Run (Windows)
# NOTE: English inside .ps1 to avoid PowerShell 5.1 encoding issues.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\..

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual env not found. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

& .\.venv\Scripts\python.exe -m app.main
exit $LASTEXITCODE
