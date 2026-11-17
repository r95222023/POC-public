# 在 G:\POC 本機執行

需求：Windows x64、Python **3.12 x64**（包含 `py` launcher），完整版另需 Git for Windows 與首次下載套件、權重的網路。無需 GPU，不必安裝 Node.js。

## 把程式放到本機

只有在程式已推送到 GitHub 後，以下 clone 才會取得此 POC。請勿將尚未推送的雲端檔案誤認為已存在於遠端。

```powershell
git clone https://github.com/r95222023/POC-public.git G:\POC
cd G:\POC\image-stitcher
.\setup.cmd
.\run.cmd
```

若 `G:\POC` 已有檔案，請勿覆蓋或強制 clone；先確認是否為此儲存庫的既有 checkout。若是，可保留本機修改後再更新；若不是，將發行 ZIP 解壓到另一個空目錄，確認內容再搬移。

使用 ZIP 時，把包內的 `README.md` 與 `image-stitcher` 資料夾解壓到 `G:\POC`，接著執行 `G:\POC\image-stitcher\setup.cmd`。不要複製雲端 Linux 的 `.venv` 到 Windows。

## 安裝與啟動

1. 雙擊 `setup.cmd`。它會建立 Windows `.venv`、安裝套件、下載官方 CNN 權重及研究用範例，並執行測試。失敗會保留錯誤視窗，不會假裝安裝完成。
2. 完成後雙擊 `run.cmd`，網站就緒後會自動開啟本機瀏覽器。
3. 網址為 `http://127.0.0.1:5000/`。這次程式確實在你的 Windows 電腦上執行，該位址才指向這個 POC。
4. 使用期間保留啟動的命令視窗；按 `Ctrl+C` 停止。重複開啟啟動器時會先檢查是否已有同版服務。

要使用「空拍 3D 場景」頁面，安裝時改用 `setup.cmd --scene`：另外安裝 COLMAP（pycolmap）、官方 OpenMVS 2.4.0 CPU 發行檔，並下載兩段 CC0 環繞空拍範例影片。啟動後開啟 `http://127.0.0.1:5000/scene`，上傳影片或照片即可。已安裝基本環境者可只執行 `.venv\Scripts\python.exe setup_photogrammetry.py --surface` 與 `.venv\Scripts\python.exe download_scene_samples.py`。重建全在 CPU 上執行，處理期間請保持命令視窗開啟；8 GB 記憶體建議使用預設的影格數與品質。

只想先測 SIFT，可在命令列使用 `setup.cmd --sift-only`，進入網站後選 SIFT；CNN 比較需要完整版安裝。首次完整安裝需下載大型 PyTorch 套件和模型，時間依網路而定。

範例資料限學術研究、禁止商用，安裝時從作者來源下載；不包含在 Git 或來源 ZIP。來源 URL／SHA-256 記錄在 `data\levir\sources.json`。

## 驗證範圍

目前能在雲端確認 Windows Python 3.12 x64 的依賴套件供應情形，以及跨平台 Python 邏輯和應用程式測試。沒有直接存取你的 G: 磁碟，也沒有在你的 Windows 主機上執行安裝；首次本機啟動仍需實際驗證。若失敗，請提供命令視窗的錯誤訊息。
