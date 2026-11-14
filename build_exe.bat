@echo off
rem Build a standalone exe (onefile). Output: dist\AppDataCleaner.exe
rem Requires Python 3.9+. PyInstaller is installed automatically if missing.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo Installing PyInstaller ...
  python -m pip install pyinstaller || goto :fail
)

python -m pip show pywebview >nul 2>&1
if errorlevel 1 (
  echo Installing pywebview (GUI window shell) ...
  python -m pip install pywebview || goto :fail
)

python -m PyInstaller --noconfirm --clean --onefile ^
  --name AppDataCleaner ^
  --add-data "static;static" ^
  --exclude-module tests ^
  --collect-all pythonnet ^
  --collect-all webview ^
  --collect-all clr_loader ^
  server.py || goto :fail

echo.
echo Build OK: dist\AppDataCleaner.exe
echo Copy it anywhere and double-click to run. config/logs/cache are created next to the exe.
goto :eof

:fail
echo.
echo Build FAILED. See messages above.
exit /b 1
