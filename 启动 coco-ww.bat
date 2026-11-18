@echo off
rem The game runs elevated, so coco-ww must be elevated too or it can neither send keys nor read the F key.
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
rem coco-ww.exe is a renamed pythonw.exe so the process shows up as coco-ww; fall back if it is missing.
set "EXE=%~dp0python\coco-ww.exe"
if not exist "%EXE%" set "EXE=%~dp0python\pythonw.exe"
start "" "%EXE%" -X utf8 main.py
