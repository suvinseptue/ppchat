@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PPCHAT_ROOT=%~dp0"
if "%PPCHAT_ROOT:~-1%"=="\" set "PPCHAT_ROOT=%PPCHAT_ROOT:~0,-1%"
set "PS1=%PPCHAT_ROOT%\tools\install_windows.ps1"

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
echo [!] Do not run install.cmd from inside the zip window.
echo     Windows copies it to Temp, so tools\install_windows.ps1 is missing.
echo.
echo     Extract the zip to a normal folder (e.g. D:\ppchat),
echo     then double-click install.cmd. Do not Run as administrator.
echo.
pause
exit /b 1

:missing
echo.
echo [!] Missing:
echo     %PS1%
echo.
echo     Extract the zip, then double-click install.cmd in that folder.
echo     install.cmd does not need Administrator.
echo.
pause
exit /b 1
