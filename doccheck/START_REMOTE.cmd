@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -NoBrowser
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0remote_start.py"
if errorlevel 1 goto failed
echo.
echo Keep this password private. Press any key to close this window.
pause >nul
exit /b 0
:failed
echo Failed. See data\server-error.log or data\tunnel.log.
pause
exit /b 1
