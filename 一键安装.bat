@echo off
rem Onboarding & one-click setup: checks/installs uv, Python 3.12 + dependencies, and assists VB-CABLE install
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
echo.
pause
