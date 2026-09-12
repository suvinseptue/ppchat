@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PPCHAT_ROOT=%~dp0"
if "%PPCHAT_ROOT:~-1%"=="\" set "PPCHAT_ROOT=%PPCHAT_ROOT:~0,-1%"
set "PS1=%PPCHAT_ROOT%\tools\setup_keys_windows.ps1"

echo %PPCHAT_ROOT% | findstr /i /c:".zip" >nul
if %ERRORLEVEL%==0 goto :fromzip
if not exist "%PS1%" goto :missing

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath $env:PPCHAT_ROOT; & (Join-Path $env:PPCHAT_ROOT 'tools\setup_keys_windows.ps1')"
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%

:fromzip
echo.
echo [!] 不要在压缩包窗口里直接打开 setup-keys.cmd。
echo     请先解压整个 zip 到普通文件夹，再右键解压后的 setup-keys.cmd
echo     → 以管理员身份运行。
echo.
pause
exit /b 1

:missing
echo.
echo [!] 找不到：
echo     %PS1%
echo.
echo     请先解压整个 zip，再运行解压目录里的 setup-keys.cmd。
echo.
pause
exit /b 1
