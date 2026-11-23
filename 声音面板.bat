@echo off
rem Voice panel launcher: runs onboarding setup automatically on first launch if .venv is missing
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -NoLaunch
    if errorlevel 1 (
        echo.
        pause
        exit /b 1
    )
    if not exist "%~dp0.venv\Scripts\pythonw.exe" (
        echo Setup incomplete. Please run setup.ps1 again.
        pause
        exit /b 1
    )
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\desktop.py"
