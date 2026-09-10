<#
.SYNOPSIS
    Start or restart the Compliance & Risk Agent server on Windows.

.EXAMPLE
    .\run.ps1
    .\run.ps1 8080
    .\run.ps1 -Port 8080 -NoReload
#>
[CmdletBinding()]
param (
    [Parameter(Position=0)]
    [string]$Port = $env:PORT,
    [switch]$NoReload,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$UvicornArgs
)

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not $Port) { $Port = "8000" }

# --- 1. Auto-bootstrap .env if missing --------------------------------------
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Write-Host "[run] .env not found -- copying from .env.example" -ForegroundColor Cyan
    Copy-Item ".env.example" ".env"
}

# --- 2. Virtual environment setup -------------------------------------------
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvPip = Join-Path $PSScriptRoot ".venv\Scripts\pip.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "[run] .venv not found -- locating system Python..." -ForegroundColor Cyan
    $sysPy = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $sysPy) {
        Write-Error "[run] Python was not found in PATH. Please install Python 3.9+ from https://python.org and ensure 'Add Python to PATH' is checked."
        exit 1
    }
    Write-Host "[run] Creating virtual environment (.venv)..." -ForegroundColor Cyan
    & $sysPy.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[run] Failed to create virtual environment."
        exit 1
    }
    Write-Host "[run] Installing dependencies from requirements.txt..." -ForegroundColor Cyan
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[run] Failed to install dependencies."
        exit 1
    }
    New-Item -Path ".venv\.requirements_installed" -ItemType File -Force | Out-Null
}

# --- 3. Free the port if in use ---------------------------------------------
try {
    $conns = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            Write-Host "[run] Port $Port is in use by PID $($conn.OwningProcess) -- stopping old server..." -ForegroundColor Yellow
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
} catch {
    # Fallback to netstat if Get-NetTCPConnection is unavailable or restricted
    $netstatOut = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    foreach ($line in $netstatOut) {
        $tokens = ($line -split '\s+') | Where-Object { $_ -ne '' }
        $pidToKill = $tokens[-1]
        if ($pidToKill -match '^\d+$') {
            Write-Host "[run] Port $Port is in use by PID $pidToKill -- stopping old server..." -ForegroundColor Yellow
            Stop-Process -Id ([int]$pidToKill) -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- 4. Ensure sample docs exist --------------------------------------------
if (-not (Test-Path "sample_docs\1_GDPR_Art28_DPA_Requirements.pdf")) {
    Write-Host "[run] Generating sample PDFs into sample_docs/..." -ForegroundColor Cyan
    & $venvPy -c "import demo; demo.make_sample_pdfs()"
}

# --- 5. Start uvicorn server ------------------------------------------------
$argsList = @("app.main:app", "--host", "0.0.0.0", "--port", "$Port")
if (-not $NoReload) {
    $argsList += "--reload"
}
if ($UvicornArgs) {
    $argsList += $UvicornArgs
}

Write-Host "[run] starting: uvicorn $($argsList -join ' ')" -ForegroundColor Green
Write-Host "[run] open http://localhost:$Port   *   health: http://localhost:$Port/api/health" -ForegroundColor Green
& $venvPy -m uvicorn @argsList
