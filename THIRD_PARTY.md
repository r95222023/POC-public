# 第三方程式與資料

本儲存庫原創程式碼採 GPL-3.0-only（見 LICENSE）。此授權不變更第三方程式、權重與資料的授權。安裝器下載的第三方內容存於忽略追蹤的快取，不隨本儲存庫重新發布。

| 元件或資料 | 用途／來源與授權說明 |
|---|---|
| COLMAP／PyCOLMAP | 相機定位、稀疏重建與去畸變；[官方原始碼與授權](https://github.com/colmap/colmap)。 |
| OpenMVS | 透過獨立命令列程序執行密集重建、網格及貼圖；[官方原始碼、授權與發行檔](https://github.com/cdcseacave/openMVS)。不隨本儲存庫包含其二進位檔。 |
| SuperPoint／LightGlue | 特徵提取、配對與預訓練權重；[官方授權說明](https://github.com/cvg/LightGlue#license)。不同程式、特徵與權重可能有不同條件。 |
| Three.js | 本機三維貼圖預覽；使用 npm 官方套件的固定版本 0.180.0，複製其 LICENSE 到預覽資產資料夾；[官方原始碼](https://github.com/mrdoob/three.js)。 |
| Trimesh／SciPy／Rtree | 網格讀取、幾何查詢與點到表面的投影；各套件附有自己的授權與相依元件聲明。 |
| Flask／NumPy／OpenCV／Pillow／PyTorch／pytest | 網站、影像與數值處理、推論及測試；沿用各套件附帶授權。 |
| RapidOCR／ONNX Runtime | 本地 OCR 推論與 PP-OCRv6 模型權重（執行時下載至 `.venv` 套件快取）；沿用各專案附帶授權。 |
| Ollama（選用外部服務） | 視覺語言模型交叉校對端點，由使用者自行架設與連線，不隨本儲存庫發布模型權重。 |
| LEVIR-CD／BIT_CD 範例影像 | 限學術研究、禁止商用，另受來源與 Google Earth 條款約束；[來源與限制](image-stitcher/README.md)。專案 GPL 授權不授予影像商用權。 |
| samples/ 使用者照片與 iTwin 參考成果 | 本機測試資料，不納入儲存庫的 GPL 授權，也不提交或推送至 GitHub。 |

散布包含第三方程式、模型或資料的套裝成品時，應保留各自的授權全文、版權及必要聲明，並依所採用版本的條件處理。
