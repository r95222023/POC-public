# PhotoGroup-132：相機定位與稀疏重建

實測日期：2026-11-09。輸入為本機 `samples/PhotoGroup-132.zip` 的 34 張照片；iTwin OBJ 為後續比較的參考，本次未參與相機定位或三維點計算。

## 執行環境與設定

- Windows、專案 Python 3.12.10、PyCOLMAP 4.2.1。
- Intel Core i5-1035G4、約 8 GB RAM、Intel Iris Plus；全程使用 CPU、2 個執行緒。
- SIFT 長邊上限 1600、特徵目標數 4096；全配對後進行增量 SfM。
- SIMPLE_RADIAL 相機模型，分為 DJI 4000×3000 與 Parrot 1920×1080 兩組內參。
- 未使用控制點、GPS 定位約束或既有 iTwin 幾何。

## 本次結果

| 項目 | 實測值 |
|---|---:|
| 同一模型定位成功照片 | 34 / 34 |
| 稀疏三維點 | 15,054 |
| 二維觀測數 | 124,046 |
| 每點平均觀測照片數 | 8.24 |
| 平均重投影誤差 | 0.617 px |
| 各點重投影誤差中位數 | 0.520 px |
| ≥15 幾何內點的配對 | 483 / 561 |
| 每點最大三角化角度 P10／P50／P90 | 3.66°／11.70°／27.72° |
| 包含匯入、匹配、定位與匯出的總耗時 | 596.11 秒 |

重新載入 COLMAP 二進位模型後，確認 34 張已定位、15,054 點，所有點與相機中心座標皆為有限數值。已檢視主平面／側向投影，可辨識道路、建物與河岸輪廓。點雲主要分布在近似平面的地面，主軸方差比例約 54.38%、45.00%、0.62%；這項數值不代表重建精度。

## 產物與驗證

本機輸出：`outputs/photogroup132/sift-cpu-1600/`，含 `report.json`、`diagnostics.json`、`preview.html`、`preview.png`，以及 `sparse/0/` 的二進位／文字模型、`points.ply`、相機位置與統計。這些資料皆忽略 Git 追蹤。

既有與新增自動測試共 **20 passed**；`pip check` 無依賴衝突。預覽 JavaScript 已用本次資料通過離線 DOM 模擬的繪圖、旋轉、縮放及重設檢查。Windows 瀏覽器操作工具因無法確認當前網址而中止，故未完成真實瀏覽器互動驗證。

目前僅完成稀疏重建，尚無密集點雲、網格或貼圖。尺度與世界座標未校正，重投影誤差是影像擬合指標，不能推論公尺尺度的誤差；也尚未與 iTwin 模型進行座標對齊及表面距離比較。

## 重跑

從 `image-stitcher` 執行，套件安裝見主 README，指定新的空輸出目錄：

```powershell
.\.venv\Scripts\python.exe reconstruct_sparse.py ..\samples\PhotoGroup-132.zip --output outputs\reconstruction\run-002
```

耗時、點數與誤差為單次紀錄，不應作為跨平台的精確測試斷言。下一階段可從 `sparse/0/` 的相機與點雲接續影像去畸變及密集重建。
