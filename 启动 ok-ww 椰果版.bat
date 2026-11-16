@echo off
rem The game runs elevated, so OK-WW must be elevated too or it can neither send keys nor read the F key.
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" -X utf8 main.py
