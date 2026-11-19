@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0STOP_PUBLIC.ps1"
if errorlevel 1 pause
