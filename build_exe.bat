@echo off
rem Build a standalone exe (onefile). Output: dist\AppDataCleaner.exe
rem Requires Python 3.9+. Build deps are pinned in requirements-build.txt.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem 构建依赖锁定在 requirements-build.txt；已满足时 pip 会快速返回
python -m pip install -q -r requirements-build.txt || goto :fail

python -m PyInstaller --noconfirm --clean --onefile --name AppDataCleaner --add-data "static;static" --exclude-module tests --collect-all pythonnet --collect-all webview --collect-all clr_loader server.py || goto :fail

python tools\release_manifest.py || goto :fail

echo.
echo Build OK: dist\AppDataCleaner.exe
echo Release manifest: dist\release.json
echo Copy it anywhere and double-click to run. config/logs/cache are created next to the exe.
goto :eof

:fail
echo.
echo Build FAILED. See messages above.
exit /b 1
