@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1"
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%
