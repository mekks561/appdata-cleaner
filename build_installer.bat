@echo off
rem Build the Inno Setup installer. Output: dist\AppDataCleaner-Setup-<ver>.exe
rem Requires: dist\AppDataCleaner.exe (run build_exe.bat) and Inno Setup 6 (ISCC.exe).
rem Inno Setup is only needed for building the installer; it is not a runtime dependency.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "dist\AppDataCleaner.exe" (
  echo [ERROR] dist\AppDataCleaner.exe not found. Run build_exe.bat first.
  exit /b 1
)

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  for %%I in (ISCC.exe) do if not "%%~$PATH:I"=="" set "ISCC=%%~$PATH:I"
)

if not defined ISCC (
  echo.
  echo [ERROR] Inno Setup 6 not found ^(ISCC.exe^).
  echo   Install it from https://jrsoftware.org/isdl.php and re-run this script.
  echo   Default path: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  echo.
  exit /b 1
)

set "VER="
for /f "usebackq delims=" %%v in (`python tools\release_manifest.py --version`) do set "VER=%%v"
if not defined VER (
  echo [ERROR] Cannot read APP_VERSION from server.py.
  exit /b 1
)

"%ISCC%" /DAppVersion=%VER% "installer\AppDataCleaner.iss" || goto :fail

rem Refresh the release manifest so it also covers the setup exe (SHA256).
python tools\release_manifest.py || goto :fail

echo.
echo Build OK: dist\AppDataCleaner-Setup-%VER%.exe
echo Installer is NOT code-signed; SmartScreen may warn on first run.
goto :eof

:fail
echo.
echo Installer build FAILED. See messages above.
exit /b 1
