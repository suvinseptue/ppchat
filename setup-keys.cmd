@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PPCHAT_ROOT=%~dp0"
if "%PPCHAT_ROOT:~-1%"=="\" set "PPCHAT_ROOT=%PPCHAT_ROOT:~0,-1%"
set "PS1=%PPCHAT_ROOT%\tools\setup_keys_windows.ps1"

echo %PPCHAT_ROOT% | findstr /i /c:".zip" >nul
if %ERRORLEVEL%==0 goto :fromzip
if not exist "%PS1%" goto :missing

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%

:fromzip
echo.
echo [!] Do not run setup-keys.cmd from inside the zip window.
echo     Extract the zip first, then right-click setup-keys.cmd
echo     and choose Run as administrator.
echo.
pause
exit /b 1

:missing
echo.
echo [!] Missing:
echo     %PS1%
echo.
echo     Extract the zip, then run setup-keys.cmd from the extracted folder.
echo.
pause
exit /b 1
