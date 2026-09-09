@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [!] .venv not found. Run install.cmd first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" %*
