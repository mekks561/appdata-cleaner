@echo off
title AppData Disk Cleaner v1.2.0 (Administrator)
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting administrator rights, please click "Yes" in the UAC dialog...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

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

echo Running as ADMINISTRATOR - protected folders can now be cleaned.
echo Starting AppData Disk Cleaner v1.2.0 at http://127.0.0.1:8765 ...
echo Close this window to stop the service.
echo.
python server.py
pause
