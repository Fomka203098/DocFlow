$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No server PID file found.'; exit 0 }
$serverId = [int](Get-Content -LiteralPath $pidFile -Raw)
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverId"
$expectedExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($processInfo -and $processInfo.ExecutablePath -eq $expectedExecutable -and $processInfo.CommandLine -like '*backend.app:app*') {
    Stop-Process -Id $serverId
    Write-Output 'Doccheck stopped.'
} elseif ($processInfo) { throw 'PID belongs to another program. Nothing was stopped.' }
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
