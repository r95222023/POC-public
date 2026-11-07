# Overlap｜兩張影像的局部匹配與對齊

上傳 A、B，找出將 B 映射到 A 的最佳幾何候選，提供透明度疊圖、A 上的投影範圍、內點連線、曝光補償與羽化拼接預覽。可並排比較 SIFT 與 CNN 特徵。介面使用繁體中文，支援手機寬度。

網站上方提供四組可點選載入的照片：同日期旋轉縮放裁切、跨時間、跨時間加 90° 轉向，以及空地變建物的困難案例。點選後直接按「尋找對應區域」，也可更換其中一張為自己的圖片。`setup.sh` 會下載範例；已安裝的環境可單獨執行 `.venv/bin/python download_samples.py`。缺少資料時範例卡片會停用，不影響自行上傳。

`templates/index.html` 是 Flask 模板，不能直接當作靜態網站開啟，否則會看到 `{% ... %}` 與缺圖。若要在檔案預覽中試用四組範例，執行 `.venv/bin/python export_demo.py`，再開 `outputs/overlap-demo.html`。該單檔含照片、樣式與後端實際算出的範例結果，可離線點選及比較；不會對自行上傳的新圖片做運算，亦不是正式網站部署。生成檔含受限制的研究影像，請勿公開或商用發布。

## 安裝與啟動

Windows 本機部署至 `G:\POC`：請看 [Windows 部署說明](README-WINDOWS.md)，依序執行 `setup.cmd` 與 `run.cmd`。

已在 Linux、Python 3.12、CPU 環境實測。從此子資料夾執行：

```bash
cd /workspace/POC-public/image-stitcher
bash setup.sh
.venv/bin/python app.py
```

環境休眠或重新建立後，網站程序可能消失；檔案保留不代表伺服器仍在執行。可用 `.venv/bin/python start.py` 重啟並檢查 readiness，重跑不會重複啟動同一服務。此啟動器綁定 `0.0.0.0:5000` 以支援平台提供的預覽／桌面入口；手動啟動仍預設綁定 loopback。啟動紀錄在 `outputs/server.log`。這些命令不會建立公開網址，能否從你使用的介面存取，取決於平台實際提供的桌面或埠預覽功能。

伺服器在本機 port 5000 提供介面；`GET /api/health` 可檢查服務。這是開發伺服器。雲端 onboarding 不提供 localhost 預覽連結；可在能存取該服務的瀏覽器操作，或用下方 API 測試。

只需要 SIFT 時用 `bash setup.sh --sift-only`。CNN 版安裝 CPU PyTorch、固定 commit 的官方 LightGlue，並透過官方 HTTPS release 預下載 SuperPoint 與 LightGlue 權重。權重存在 `.cache/torch`（若已有 `TORCH_HOME`，則尊重既有設定），後續匹配可離線執行。

使用既有 checkout 即可；雲端任務已隔離，不需要建立 worktree。網路需允許 PyPI、PyTorch 與 GitHub 套件及 release 下載站；保持既有代理和 TLS 驗證。Linux 若缺少 `libGL.so.1`，需安裝系統的 `libgl1`，不要同時安裝兩套 OpenCV wheel。

## 演算法

- **SIFT**：灰階 CLAHE、最多 6000 個特徵、雙向 ratio test（0.75）與互相最近鄰。
- **SuperPoint＋LightGlue**：官方預訓練 CNN 局部特徵（最多 1024 點），LightGlue 利用上下文配對，CPU 推論。沒有自行訓練，也沒有用測試圖片微調。
- **幾何估算**：相似／仿射模型使用 RANSAC，Homography 使用 USAC_MAGSAC，統一用處理後尺寸的 4 px 門檻重算內點。
- **候選排序**：綜合內點數、比例、凸包覆蓋率、中位重投影誤差與模型複雜度。先排通過啟發式幾何檢查的候選，再按分數排序。候選表是同一組特徵的不同模型／搜尋方向，不是完整列舉所有可能地點。
- **方向搜尋**：可額外嘗試 B 的 0／90／180／270 度方向；返回矩陣仍以輸入 B 座標表示。這改善 CNN 大角度匹配機會，不保證任意角度都成功。
- **預覽融合**：有效區遮罩、有限的每通道亮度偏移補償、距離加權羽化。尚未實作 graph-cut、多頻帶融合、APAP 或三維重建。

「通過幾何檢查」目前要求 ≥12 內點、≥25% 內點比例、兩張全圖的內點凸包覆蓋率較小值 ≥1.5%、中位誤差 ≤3 px。這是便於判讀的啟發式門檻，**不是正確率或成功機率**。局部視差、重複紋理仍可能通過。小範圍重疊的正確結果也可能被標為低可信。

無足夠配對時明確回傳 `no_match`；勉強成立的模型標為 `tentative`。單一 Homography 無法消除高樓與地面的視差，融合也無法修復錯誤幾何。

## 輸入、輸出與 API

兩張圖合計上傳上限 32 MiB、每張解碼上限 4000 萬像素。先校正 EXIF 方向，再等比例縮小至長邊 1600 px。透明區以白色展平。原圖不上傳至第三方，也不由應用程式寫入磁碟。

輸出的 PNG 是預覽，長邊最多 1800 px、聯合畫布最多 1200 萬像素；**不是原始解析度的測繪成果**。包含有效黑色像素的區域不會誤當成透明。

```bash
mkdir -p outputs
curl -f -X POST http://127.0.0.1:5000/api/align \
  -F a=@data/levir/A/train_386_0512_0768.png \
  -F b=@data/levir/B/train_386_0512_0768.png \
  -F method=compare -F rotation_search=false \
  -o outputs/api-result.json
```

`method` 可為 `sift`、`lightglue`、`compare`。每個結果含候選指標、預覽 data URL 與 `matrix_original`：它將 **EXIF 校正後原圖 B** 的像素座標映射到 **EXIF 校正後原圖 A**。候選自己的 `matrix` 及像素誤差使用縮小後座標。`overlap` 是相交面積除以兩個影像投影面積的較小者，不是定位成功率。

為控制 CPU／記憶體，每個 process 同時只處理一組影像；忙碌時回傳 429。CNN 不可用時，compare 仍保留 SIFT 的結果，並標示 CNN 錯誤。

## 公開跨時間影像實測

```bash
.venv/bin/python download_samples.py
.venv/bin/python benchmark.py
```

下載作者 BIT_CD repository 中的 12 組 LEVIR-CD 範例，固定來源 commit，寫入忽略追蹤的 `data/levir`，並記錄每張影像的來源和 SHA-256。結果在 `outputs/benchmark/results.json`，範例預覽亦在該目錄。實測摘要見 [BENCHMARK.md](BENCHMARK.md)。

來源：[LEVIR-CD 說明](https://github.com/justchenhao/LEVIR)、[BIT_CD 範例](https://github.com/justchenhao/BIT_CD/tree/adcd7aea6f234586ffffdd4e9959404f96271711/samples)。LEVIR-CD 描述為 0.5 m/pixel、相隔 5–14 年的 Google Earth 影像；本次範例有 11 組 256 × 256 裁切圖，另有一組約 768 × 384，各範例沒有附確切拍攝日期或感測器資訊。它們是已配準的俯視遙測影像，不能據此宣稱測過獨立相機的斜拍空拍圖。

**影像限學術用途、禁止商用，並受 Google Earth 條款約束**；不隨程式碼重新發布。程式庫及預訓練權重亦有各自的授權條件，使用前請查閱 [LightGlue 官方說明](https://github.com/cvg/LightGlue#license)。

測試分為原始跨時間配對、跨時間再加旋轉／縮放／透視／色調／裁切、90 度方向搜尋、同日期已知變換與無關影像對照。跨時間以原先配準的單位矩陣作**近似參考**，來源本身可能殘留偏移；同日期人工變換才有精確已知的幾何真值。25 點網格誤差獨立於被選中的匹配點誤差，能揭露局部吻合但整體扭曲的候選。

## 驗證

```bash
.venv/bin/python -m pytest -q
node --check static/app.js
.venv/bin/python -m pip check
```

既有測試包含外點干擾下的三模型估算、旋轉／縮放／曝光／裁切、四方向座標還原、空白圖拒絕、投影奇點拒絕、黑色有效像素及 HTTP 上傳與錯誤處理。CNN 實際推論由 benchmark 驗證，不會在單元測試時偷偷下載模型。
