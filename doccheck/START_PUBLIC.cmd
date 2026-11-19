@echo off
cd /d "%~dp0"
set "DOCCHECK_DATA_DIR=%~dp0public-data"
set "DOCCHECK_PUBLIC=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -NoBrowser
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0remote_start.py"
if errorlevel 1 goto failed
echo Public test mode: all visitors can see and delete uploaded documents.
pause
exit /b 0
:failed
echo Failed. Check public-data\tunnel.log and server-error.log.
pause
exit /b 1
