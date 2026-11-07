@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 x64 with the Python launcher first.
  echo https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3.12 setup_windows.py %*
if errorlevel 1 (
  echo Setup failed. Read the error above before retrying.
  pause
  exit /b 1
)
echo Setup complete. Run run.cmd to open the website.
pause
