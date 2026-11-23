import base64
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from PIL import Image
from rapidocr import RapidOCR

from stamp_detector import detect_stamp_rois, pil_to_data_url, render_annotated_overlay

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
_OCR_LOCK = threading.Lock()
_OCR_ENGINE = None


def get_ocr_engine():
    global _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def run_rapidocr_on_path(img_path):
    engine = get_ocr_engine()
    with _OCR_LOCK:
        res = engine(img_path)
    txts = list(res.txts) if res.txts is not None else []
    scores = list(res.scores) if res.scores is not None else []
    boxes = res.boxes if res.boxes is not None else []
    lines = []
    for b, t, s in zip(boxes, txts, scores):
        lines.append({
            "box": [[int(p[0]), int(p[1])] for p in b],
            "text": str(t),
            "score": round(float(s), 4),
        })
    return lines


def run_rapidocr_on_pil(im):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=92)
    engine = get_ocr_engine()
    with _OCR_LOCK:
        res = engine(buf.getvalue())
    txts = list(res.txts) if res.txts is not None else []
    scores = list(res.scores) if res.scores is not None else []
    return [{"text": str(t), "score": round(float(s), 4)} for t, s in zip(txts, scores)]


def decode_ollama_byte_tokens(text):
    """Replace literal <0xE3><0x80><0x80> UTF-8 byte token artifacts emitted by Gemma 4."""
    count = 0

    def _repl_seq(match):
        nonlocal count
        hexes = re.findall(r"<0x([0-9A-Fa-f]{2})>", match.group(0))
        try:
            raw = bytes(int(h, 16) for h in hexes)
            decoded = raw.decode("utf-8")
            count += len(hexes)
            return " " if decoded == "\u3000" or decoded == "\xa0" else decoded
        except Exception:
            return match.group(0)

    cleaned = re.sub(r"(?:<0x[0-9A-Fa-f]{2}>)+", _repl_seq, text)
    return cleaned, count


def format_ocr_for_prompt(lines, img_w, img_h):
    enriched = []
    for item in lines:
        xs = [p[0] for p in item["box"]]
        ys = [p[1] for p in item["box"]]
        yc = sum(ys) / max(1, len(ys)) / max(1, img_h) * 100
        xc = sum(xs) / max(1, len(xs)) / max(1, img_w) * 100
        enriched.append((yc, xc, item["score"], item["text"]))
    enriched.sort(key=lambda x: (round(x[0], 1), x[1]))
    return "\n".join(f"[y={y:.0f}%, x={x:.0f}%, conf={sc:.2f}] {txt}" for y, x, sc, txt in enriched)


def extract_local_date_candidates(ocr_lines, rois):
    """Rule-based fallback extracting date-like strings from OCR & ROI lines."""
    pat = re.compile(
        r"((?:中華民國\s*)?\d{2,4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?(?:\s*(?:上午|下午)?\s*\d{1,2}:\d{2}(?::\d{2})?)?)"
    )
    found = []
    seen = set()
    for item in ocr_lines:
        for m in pat.finditer(item["text"]):
            val = m.group(1).strip()
            key = ("full", val)
            if key not in seen:
                seen.add(key)
                found.append({"source": "整頁 OCR", "text": val, "line": item["text"], "score": item["score"]})
    for idx, roi in enumerate(rois, 1):
        for o in roi.get("ocr", []):
            for m in pat.finditer(o["text"]):
                val = m.group(1).strip()
                key = (f"ROI#{idx}", val)
                if key not in seen:
                    seen.add(key)
                    found.append({"source": f"自動印章 ROI #{idx} ({roi['reason']})", "text": val, "line": o["text"], "score": o["score"]})
    return found


def build_prompt(task_type, mode, full_ocr_txt, rois):
    has_ocr = mode == "hybrid" and bool(full_ocr_txt.strip())
    has_rois = mode == "hybrid" and len(rois) > 0

    if task_type == "stamp_only":
        core = (
            "請從圖片中專注找出所有「蓋章／戳記」（例如收文章、日期章、電子交換章、職名章旁手寫日期時間等）上的日期。\n"
            "規則：\n"
            "1. 嚴格區分「公文原本印刷的發文日期／內文提及日期」與「實際蓋印或蓋章內填寫的日期」。\n"
            "2. 請以清楚的 JSON 區塊（```json ... ```）與繁體中文摘要輸出，JSON 欄位：\n"
            '   {"printed_dates": [...], "stamp_dates": [{"location": "...", "stamp_type": "...", "date": "...", "time": "...", "note": "..."}], "summary": "..."}\n'
            "3. 若印章太模糊無法辨認，請在 note 標明看不清，切勿硬猜。"
        )
    elif task_type == "doc_full":
        core = (
            "請對這份文件／圖片進行完整高精度繁體中文辨識與公文結構解析。\n"
            "請先輸出一個 JSON 區塊（```json ... ```），格式如下：\n"
            "{\n"
            '  "doc_fields": {"agency": "", "doc_number": "", "printed_date": "", "recipient": "", "subject": ""},\n'
            '  "stamp_dates": [{"location": "", "stamp_type": "", "date": "", "time": "", "note": ""}],\n'
            '  "full_text": "完整的逐字校對全文（保留原本段落與表格）"\n'
            "}\n"
            "規則：\n"
            "1. 務必區分印刷發文日期（printed_date）與實際蓋章/手填日期（stamp_dates）。\n"
            "2. 若圖片不是公文（例如系統截圖或一般表格），doc_fields 留空字串，並在 full_text 完整逐字轉錄所有文字。\n"
            "3. 不可輸出 `<0xE3><0x80><0x80>` 等位元組標記，保留原本繁簡與英數字元。"
        )
    else:  # general_ocr
        core = (
            "請完整逐字轉錄圖片中的所有文字、數字、代碼與表格內容。\n"
            "規則：\n"
            "1. 保持原本的繁體／簡體中文、英文大小寫、數字與符號，不要摘要或自行改寫。\n"
            "2. 若有表格請以 Markdown 表格呈現。\n"
            "3. 不可輸出 `<0xE3><0x80><0x80>` 等位元組標記。"
        )

    if not has_ocr:
        return core

    parts = [core, "\n【輔助資料：開源 OCR（RapidOCR PP-OCRv6）預掃描結果】"]
    if has_rois:
        roi_desc = []
        for idx, r in enumerate(rois, 1):
            o_str = "; ".join(f"{o['text']} ({o['score']:.2f})" for o in r.get("ocr", [])) or "(無文字檢出)"
            roi_desc.append(f"- 圖 {idx+1}（自動定位區 bbox={r['bbox']}, 觸發={r['reason']}）放大後 OCR：{o_str}")
        parts.append(
            f"本次共提供 {1 + len(rois)} 張圖片：圖 1 為整頁原圖（保留整體版面與文號年份上下文）；"
            "圖 2 起為 OpenCV/OCR 自動偵測並放大的候選印章／手寫註記區域（請用來確認模糊印章與小字細節）。\n"
            + "\n".join(roi_desc)
        )

    parts.append(
        "請結合「圖片實際視覺字形」與「下方 OCR 文字及座標」交叉比對：\n"
        "- OCR 對密集表格、10px–16px 小字與數字通常更精準，可補回圖片壓縮漏字；\n"
        "- 但 OCR 偶爾會把繁體誤轉為簡體（如暂/键/虚/宝）或漏讀藍紅印泥，請務必對照圖片實際筆畫與上下文修正。\n\n"
        f"整頁 OCR 偵測結果：\n{full_ocr_txt}"
    )
    return "\n".join(parts)


def parse_structured_json(text):
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        # Fallback if model returned raw JSON without fences
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return json.loads(stripped)
            except Exception:
                return None
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def call_ollama_chat(ollama_url, model, prompt, images_b64, think=False, num_ctx=8192):
    base = ollama_url.rstrip("/")
    # Fast preflight check so an offline/unreachable server fails cleanly in ~2.5s instead of hanging
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/version"), timeout=2.5).close()
    msg = {"role": "user", "content": prompt}
    if images_b64:
        msg["images"] = images_b64

    payload = {
        "model": model,
        "stream": False,
        "think": bool(think),
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": [msg],
    }
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=240) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    dt = time.time() - t0
    m = res.get("message", {})
    raw_content = m.get("content", "") or ""
    cleaned_content, byte_tokens_cleaned = decode_ollama_byte_tokens(raw_content)
    ev_s = res.get("eval_duration", 0) / 1e9
    ev_n = res.get("eval_count", 0)
    return {
        "wall_s": round(dt, 2),
        "prompt_eval_count": res.get("prompt_eval_count", 0),
        "eval_count": ev_n,
        "tokens_per_s": round(ev_n / ev_s, 1) if ev_s > 0 else 0.0,
        "thinking": m.get("thinking", "") or "",
        "content": cleaned_content,
        "byte_tokens_cleaned": byte_tokens_cleaned,
    }


def run_ocr_pipeline(
    img_path,
    mode="hybrid",
    task_type="doc_full",
    ollama_url=None,
    model="gemma4:26b",
    think=False,
    include_roi_crops=True,
):
    if not ollama_url:
        ollama_url = DEFAULT_OLLAMA_URL
    t_start = time.time()
    im = Image.open(img_path).convert("RGB")
    img_w, img_h = im.size

    ocr_lines = []
    rois_meta = []
    roi_images_b64 = []
    overlay_data_url = None
    ocr_s = 0.0

    # Step 1 & 2: Local RapidOCR + OpenCV Stamp ROI Detector
    if mode in ("hybrid", "ocr_only"):
        t_ocr = time.time()
        ocr_lines = run_rapidocr_on_path(img_path)
        rois = detect_stamp_rois(img_path, ocr_lines, max_crops=3)
        for r in rois:
            roi_ocr = run_rapidocr_on_pil(r["crop"])
            data_url, b64_crop = pil_to_data_url(r["crop"], quality=92)
            rois_meta.append({
                "bbox": r["bbox"],
                "reason": r["reason"],
                "size": list(r["crop"].size),
                "data_url": data_url,
                "ocr": roi_ocr,
            })
            if include_roi_crops:
                roi_images_b64.append(b64_crop)
        overlay_data_url = render_annotated_overlay(img_path, ocr_lines, rois)
        ocr_s = round(time.time() - t_ocr, 2)

    full_ocr_formatted = format_ocr_for_prompt(ocr_lines, img_w, img_h) if ocr_lines else ""
    date_candidates = extract_local_date_candidates(ocr_lines, rois_meta)

    # Step 3: Ollama VLM Verification / Synthesis
    vlm_result = None
    vlm_error = None
    structured = None
    prompt_used = ""

    if mode in ("hybrid", "vlm_only"):
        prompt_used = build_prompt(task_type, mode, full_ocr_formatted, rois_meta if include_roi_crops else [])
        with open(img_path, "rb") as f:
            main_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Note: gemma4:e4b on Ollama 0.40.16 has broken vision encoder; if selected in hybrid mode,
        # send text-only OCR post-processing so it still returns useful text instead of hallucinating abstract art!
        if "gemma4:e4b" in model and mode == "hybrid":
            images_to_send = []
            prompt_used = (
                "（註：目前選擇 gemma4:e4b，將以純文字模式整理與校對 RapidOCR 結果）\n\n" + prompt_used
            )
        else:
            images_to_send = [main_b64] + (roi_images_b64 if (mode == "hybrid" and include_roi_crops) else [])

        try:
            vlm_result = call_ollama_chat(
                ollama_url=ollama_url,
                model=model,
                prompt=prompt_used,
                images_b64=images_to_send,
                think=think,
            )
            structured = parse_structured_json(vlm_result["content"])
        except Exception as exc:
            vlm_error = str(exc)

    avg_conf = round(sum(x["score"] for x in ocr_lines) / len(ocr_lines) * 100, 1) if ocr_lines else 0.0

    return {
        "image_info": {"width": img_w, "height": img_h},
        "mode": mode,
        "task_type": task_type,
        "model": model,
        "timing": {
            "ocr_s": ocr_s,
            "vlm_s": vlm_result["wall_s"] if vlm_result else 0.0,
            "total_s": round(time.time() - t_start, 2),
        },
        "ocr": {
            "line_count": len(ocr_lines),
            "avg_confidence_pct": avg_conf,
            "lines": ocr_lines,
            "plain_text": "\n".join(x["text"] for x in ocr_lines),
            "formatted_with_coords": full_ocr_formatted,
            "date_candidates": date_candidates,
        },
        "stamps": {
            "roi_count": len(rois_meta),
            "rois": rois_meta,
            "overlay_data_url": overlay_data_url,
        },
        "vlm": vlm_result,
        "vlm_error": vlm_error,
        "structured": structured,
        "prompt_used": prompt_used,
    }
