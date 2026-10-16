@echo off
title DualSense Bridge
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run "uv sync" in %~dp0 first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" bridge.py

rem Keep the window open if the bridge exits with an error
if errorlevel 1 pause
