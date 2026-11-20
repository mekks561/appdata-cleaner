@echo off
rem Build two forms (2.3): onefile portable exe + onedir payload for the installer.
rem Output: dist\AppDataCleaner.exe and dist\AppDataCleaner\AppDataCleaner.exe
rem Requires Python 3.9+. Build deps are pinned in requirements-build.txt.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem 构建依赖锁定在 requirements-build.txt；已满足时 pip 会快速返回
python -m pip install -q -r requirements-build.txt || goto :fail

python -m PyInstaller --noconfirm --clean --onefile --name AppDataCleaner --add-data "static;static" --exclude-module tests --collect-all pythonnet --collect-all webview --collect-all clr_loader server.py || goto :fail

python -m PyInstaller --noconfirm --clean --onedir --name AppDataCleaner --add-data "static;static" --exclude-module tests --collect-all pythonnet --collect-all webview --collect-all clr_loader server.py || goto :fail

python tools\release_manifest.py || goto :fail

echo.
echo Build OK: dist\AppDataCleaner.exe (onefile, portable)
echo Build OK: dist\AppDataCleaner\AppDataCleaner.exe (onedir, installer payload)
echo Release manifest: dist\release.json
echo Copy the exe anywhere and double-click to run. config/logs/cache are created next to the exe.
goto :eof

:fail
echo.
echo Build FAILED. See messages above.
exit /b 1
