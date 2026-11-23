@echo off
setlocal
chcp 65001 >nul
title OCR 混合辨識工作台 (OCR Workbench)

cd /d "%~dp0"

set PORT=8765
if not "%OCR_WEB_PORT%"=="" set PORT=%OCR_WEB_PORT%
set URL=http://127.0.0.1:%PORT%/

if not exist ".venv\Scripts\python.exe" (
  echo 偵測到尚未完成環境設定，正在執行 setup_windows.py...
  where py >nul 2>&1
  if errorlevel 1 (
    python setup_windows.py
  ) else (
    py -3.12 setup_windows.py 2>nul || python setup_windows.py
  )
  if errorlevel 1 (
    echo 環境設定失敗，請檢查上方錯誤訊息。
    pause
    exit /b 1
  )
)

echo 啟動瀏覽器預覽: %URL%
start "" "%URL%"

echo 正在啟動 OCR Workbench 伺服器 (連接埠: %PORT%)...
echo 按 Ctrl + C 可停止服務。
echo.

".venv\Scripts\python.exe" server.py --host 127.0.0.1 --port %PORT%
if errorlevel 1 (
  echo 伺服器異常結束，請檢查上方錯誤訊息。
  pause
  exit /b 1
)
