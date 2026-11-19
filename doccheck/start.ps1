param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python environment not found. See README.md for setup.' }
try { $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 3 } catch { $health = $null }
if ($health.service -eq 'doccheck') {
    if (($env:DOCCHECK_PUBLIC -eq '1') -ne ($health.public_mode -eq $true)) { throw 'Another Doccheck mode is running. Stop it before switching modes.' }
    if (-not $NoBrowser) { Start-Process 'http://localhost:8000' }
    Write-Output 'Doccheck is already running: http://localhost:8000'
    exit 0
}
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8000 is already used by another program.' }
$dataDir = if ($env:DOCCHECK_DATA_DIR) { $env:DOCCHECK_DATA_DIR } else { Join-Path $taskRoot 'data' }
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$env:PYTHONUTF8 = '1'
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataDir 'server.log') -RedirectStandardError (Join-Path $dataDir 'server-error.log') -PassThru
Set-Content -LiteralPath (Join-Path $dataDir 'server.pid') -Value $serverProcess.Id
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 } catch { $health = $null }
    if ($health.service -eq 'doccheck') {
        if (-not $NoBrowser) { Start-Process 'http://localhost:8000' }
        Write-Output 'Doccheck is running: http://localhost:8000'
        exit 0
    }
    if ($serverProcess.HasExited) { break }
}
throw 'Could not start Doccheck. See data\server-error.log.'
