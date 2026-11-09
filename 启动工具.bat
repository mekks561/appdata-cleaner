@echo off
title AppData Disk Cleaner v1.4.0
cd /d "%~dp0"

chcp 65001 >nul 2>nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [ERROR] Python not found.
  echo  Please install Python 3.9+ and check "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

echo Starting AppData Disk Cleaner v1.4.0 at http://127.0.0.1:8765 ...
echo Close this window to stop the service.
echo.
python server.py
pause
