# ============================================================================
# Order Ops Console - Windows PowerShell Convenience Startup Script (start.ps1)
# ============================================================================
# What this script does:
# 1. Checks that Python is installed and accessible.
# 2. Creates the local data/ and samples/ folders if needed.
# 3. Starts the FastAPI web server on http://127.0.0.1:8000 using Uvicorn.
# ============================================================================

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Order Ops Console - Starting Local Server" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python was not found in your PATH. Please install Python 3.11+ and try again." -ForegroundColor Red
    exit 1
}

$pyVersion = python --version
Write-Host "Using runtime: $pyVersion" -ForegroundColor Green
Write-Host "Opening console at: http://127.0.0.1:8000" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop the server." -ForegroundColor Gray
Write-Host ""

python -m uvicorn main:app --host 127.0.0.1 --port 8000
