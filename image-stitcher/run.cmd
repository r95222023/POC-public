@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run_windows.py
if errorlevel 1 (
  echo The server could not start. Read the error above.
  pause
  exit /b 1
)
