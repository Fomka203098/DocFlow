$ErrorActionPreference = 'Stop'
$dataPath = Join-Path $PSScriptRoot 'data'
$config = Join-Path $dataPath 'remote.json'
$pidFile = Join-Path $dataPath 'tunnel.pid'
# Disable remote access before stopping the proxy.
if (Test-Path -LiteralPath $config) { Remove-Item -LiteralPath $config }
if (Test-Path -LiteralPath $pidFile) {
    $tunnelId = [int](Get-Content -LiteralPath $pidFile -Raw)
    $item = Get-CimInstance Win32_Process -Filter "ProcessId = $tunnelId"
    $expected = Join-Path $PSScriptRoot 'tools\cloudflared.exe'
    if ($item -and $item.ExecutablePath -eq $expected -and $item.CommandLine -like '*tunnel*') { Stop-Process -Id $tunnelId }
    elseif ($item) { throw 'Tunnel PID belongs to another program; nothing was stopped.' }
    Remove-Item -LiteralPath $pidFile
}
Write-Output 'Remote access stopped.'
