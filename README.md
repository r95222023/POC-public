# POC-public

[image-stitcher](image-stitcher/README.md)：兩張圖片的局部特徵匹配、幾何對齊與拼接預覽。支援 SIFT 與 SuperPoint＋LightGlue 比較，附公開跨時間遙測影像的[實測記錄](image-stitcher/BENCHMARK.md)。

Windows 本機使用：安裝 Python 3.12 x64 及 Git，將本儲存庫放在 `G:\POC`，進入 `image-stitcher` 執行 `setup.cmd`，完成後執行 `run.cmd`。詳見 [Windows 部署說明](image-stitcher/README-WINDOWS.md)。

[空拍 3D 場景網頁](image-stitcher/README.md#空拍影片照片--3d-場景網頁介面)：上傳一段環繞空拍影片或一組照片，自動挑選清晰影格、定位相機、建立貼圖模型，並在瀏覽器中自由旋轉檢視。安裝時執行 `setup.cmd --scene`，啟動後開啟 `http://127.0.0.1:5000/scene`。

另提供照片三維重建實驗：CPU 相機定位、密集點雲、貼圖網格，以及可疑點貼回表面的獨立修整版本。操作見 [重建與貼圖說明](image-stitcher/README.md#密集重建貼圖與離群點表面投影)，首批成果見 [相機定位實測](image-stitcher/reports/photogroup132-sparse.md)與[貼圖／表面修整實測](image-stitcher/reports/photogroup132-surface.md)。

專案程式碼採 **GNU GPL 第 3 版（GPL-3.0-only）**，授權全文見 [LICENSE](LICENSE)。第三方套件、預訓練權重與範例影像沿用各自授權；本專案的 GPL 授權不變更它們的使用條件。
