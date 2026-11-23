@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  python setup_windows.py %*
) else (
  py -3.12 setup_windows.py %* 2>nul || python setup_windows.py %*
)
if errorlevel 1 (
  echo.
  echo Setup failed. Check the error message above.
  pause
  exit /b 1
)
pause
