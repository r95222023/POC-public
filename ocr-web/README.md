# OCR 混合辨識工作台 (OCR Workbench)

結合本地開源 OCR（**RapidOCR PP-OCRv6 / ONNX Runtime**）、**OpenCV 印章與手寫日期自動定位**，以及可選的 **Ollama 視覺語言模型（VLM）交叉校對**，專門處理公文掃描件、蓋章／戳記日期區分與高密度系統截圖辨識。

---

## 核心功能

1. **三種執行模式**
   - **混合管線 (`hybrid`)**：先由本地 RapidOCR 進行整頁文字偵測與座標萃取，並透過 OpenCV 定位候選印章／手寫日期 ROI 放大重掃，再將整頁原圖、放大 ROI 與 OCR 座標文字送交 Ollama VLM 交叉校對與結構化輸出。若 Ollama 離線或連線失敗，仍會完整保留並回傳本地 RapidOCR、ROI 裁切圖與規則日期候選結果。
   - **僅本地開源 OCR (`ocr_only`)**：完全離線執行 RapidOCR、印章 ROI 定位、偵測疊圖繪製與正則日期候選擷取，不呼叫任何遠端模型。
   - **僅 Ollama VLM (`vlm_only`)**：不附帶 RapidOCR 提示，純以視覺模型對照辨識結果。

2. **印章／手寫日期 ROI 自動偵測 (`stamp_detector.py`)**
   - **HSV 色彩聚類**：偵測紅章／藍紫印泥區塊，計算密度與緊湊度分數並自動裁切放大。
   - **OCR 關鍵字與版面定位**：依據「收文、收字、收訖、歸檔、電子公文、交換章」及頁面下方低信心度／日期提示擴展裁切區域，並去重疊後繪製整頁偵測疊圖。

3. **公文結構化解析與防亂碼處理 (`ocr_pipeline.py`)**
   - 嚴格區分「印刷發文日期 (`printed_date`)」與「實際蓋章／手填日期 (`stamp_dates`)」。
   - 自動解碼 Gemma 4 等模型偶發輸出的 `<0x..>` UTF-8 位元組標記。
   - 支援 `doc_full`（公文整合解析）、`stamp_only`（專注蓋章日期）、`general_ocr`（一般圖片／表格逐字轉錄）三種任務。

4. **單檔零建置 Web 介面 (`static/index.html`)**
   - 支援檔案上傳、拖曳、剪貼簿 `Ctrl+V` 貼上截圖，並內建 3 張合成示範圖片一鍵測試。
   - 提供綜合解析、印章定位與偵測疊圖、開源 OCR 逐行明細搜尋、Prompt／原始 JSON 檢視，以及匯出 JSON / Markdown 報告。

---

## 快速安裝與啟動

需求：Python **3.10+**（建議 Windows Python **3.12 x64**）。純本地 OCR 模式不需 GPU 亦不需安裝 Node.js；若需使用 `hybrid` 或 `vlm_only` 模式，請另行準備可連線的 [Ollama](https://ollama.com/) 服務與視覺模型（例如 `gemma4:26b`、`qwen3.5:35b`）。

### 方式一：Windows 一鍵安裝與啟動

進入 `ocr-web` 目錄後，先執行安裝與自檢，再啟動服務：

```cmd
setup.cmd
run.cmd
```

或使用 Python 直接安裝／驗證（適合自動化或無互動環境）：

```powershell
py -3.12 setup_windows.py
.\.venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8765
```

啟動後瀏覽器開啟 `http://127.0.0.1:8765/`。

### 方式二：Linux / macOS 或手動啟動

```bash
cd ocr-web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --host 127.0.0.1 --port 8765
```

### 環境變數與命令列參數

| 變數／參數 | 預設值 | 說明 |
|---|---|---|
| `--host` / `OCR_WEB_HOST` | `127.0.0.1` | HTTP 伺服器監聽位址 |
| `--port` / `OCR_WEB_PORT` | `8765` | HTTP 伺服器連接埠 |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | 預設 Ollama 伺服器位址（亦可在網頁上方欄位即時修改） |

---

## 目錄結構

```text
ocr-web/
├── server.py               # Python 內建 ThreadingHTTPServer 與 REST API
├── ocr_pipeline.py         # RapidOCR 推論、Prompt 建構、Ollama 呼叫與後處理
├── stamp_detector.py       # OpenCV HSV 印泥偵測、OCR 關鍵字 ROI 裁切與疊圖繪製
├── static/
│   └── index.html          # 原生 HTML/CSS/JS 前端工作台
├── samples/                # 3 組合成示範圖片（清晰公文、掃描件＋紅章、系統截圖）
├── tests/
│   └── test_ocr_web.py     # pytest 單元與 HTTP API 整合測試
├── requirements.txt        # Python 相依套件
├── setup_windows.py        # Windows 虛擬環境建立、模型預熱與測試腳本
├── setup.cmd               # Windows 安裝入口
└── run.cmd                 # Windows 一鍵啟動入口
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/health` | 檢查服務狀態並取得 `default_ollama_url` |
| `GET` | `/api/ollama/tags?url=...` | 檢查指定 Ollama 伺服器連線狀態與可用模型清單 |
| `GET` | `/samples/<filename>` | 讀取內建示範圖片 |
| `POST` | `/api/ocr` | 上傳 Base64 Data URL 圖片執行辨識（上限 35 MB） |

`POST /api/ocr`（純本地模式）最小呼叫範例：

```bash
.venv/bin/python -c "import base64,json,urllib.request; b=base64.b64encode(open('samples/sample_official_scanned.png','rb').read()).decode(); req=urllib.request.Request('http://127.0.0.1:8765/api/ocr', data=json.dumps({'image_data_url':'data:image/png;base64,'+b,'mode':'ocr_only'}).encode(), headers={'Content-Type':'application/json'}); print(json.loads(urllib.request.urlopen(req).read())['ocr']['line_count'])"
```

---

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 示範資料與授權說明

- `samples/` 中的三張示範圖片皆為虛構合成資料（虛構機關名稱、文號與展示畫面），僅供功能展示與測試使用，非真實公文或案件。
- 本子專案原創程式碼依循上層儲存庫授權（**GNU GPL-3.0-only**，見 [../LICENSE](../LICENSE)）；RapidOCR、PP-OCRv6 模型權重、ONNX Runtime、OpenCV、Pillow 等第三方元件沿用各自授權（見 [../THIRD_PARTY.md](../THIRD_PARTY.md)）。
