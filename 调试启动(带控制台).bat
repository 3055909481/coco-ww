@echo off
rem Same as the normal launcher but keeps a console with debug logging; use it when something does not start.
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
"%~dp0python\python.exe" -X utf8 main_debug.pyc
pause
