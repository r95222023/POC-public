import base64
import io
import re
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1, area_a + area_b - inter)


def detect_stamp_rois(img_path, ocr_lines, max_crops=3):
    """
    Detect candidate stamp / handwritten-date ROIs via:
    1) HSV red / blue / purple ink clustering
    2) OCR-guided stamp keyword & bottom-margin low-confidence expansion
    """
    bgr = cv2.imread(img_path)
    if bgr is None:
        return []
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    ink_candidates = []
    ocr_candidates = []

    blue_mask = cv2.inRange(hsv, np.array([88, 30, 30]), np.array([148, 255, 255]))
    red1 = cv2.inRange(hsv, np.array([0, 40, 40]), np.array([14, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([162, 40, 40]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(red1, red2)

    k = max(9, min(w, h) // 55)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))

    for cname, mask in [("blue_ink", blue_mask), ("red_ink", red_mask)]:
        clean = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        dilated = cv2.dilate(clean, kernel, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw < max(20, w * 0.025) or bh < max(14, h * 0.015):
                continue
            if bw > w * 0.55 or bh > h * 0.35:
                continue
            ink_pixels = int(np.count_nonzero(clean[y : y + bh, x : x + bw]))
            density = ink_pixels / max(1, bw * bh)
            if density < 0.015:
                continue
            pad_x = max(24, int(bw * 0.30))
            pad_y = max(20, int(bh * 0.35))
            box = (max(0, x - pad_x), max(0, y - pad_y), min(w, x + bw + pad_x), min(h, y + bh + pad_y))
            area_frac = (bw * bh) / (w * h)
            compact_bonus = 1.4 if 0.001 <= area_frac <= 0.03 else 0.7
            score = min(ink_pixels, 8000) * compact_bonus
            ink_candidates.append({"bbox": box, "reason": cname, "score": score})

    stamp_kw = re.compile(
        r"(收文|收字|收訖|歸檔|電子公文|電子交換|交換章|收文字|^\s*\d{4}/\d{2}/\d{2}\s*$|"
        r"(上午|下午)\s*\d{1,2}:\d{2}:\d{2}|交\s*\d{2}:\d{2}:\d{2}|^\d{4}$)"
    )
    ignore_kw = re.compile(r"(電子信箱|電子郵件|http|@|電話|傳真|地址)")

    for item in ocr_lines:
        txt = item["text"].strip()
        if ignore_kw.search(txt):
            continue
        pts = item["box"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        bw, bh = x2 - x1, y2 - y1
        sc = item["score"]

        has_kw = bool(stamp_kw.search(txt))
        is_stamp_date_hint = ("發文日期" in txt and sc < 0.93)
        is_bottom_low_conf = (y1 > h * 0.75 and sc < 0.90 and len(txt) >= 2)
        is_bottom_date = (y1 > h * 0.65 and bool(re.search(r"\d{2,3}\s*年\s*\d{1,2}\s*月", txt)))

        if has_kw or is_stamp_date_hint or is_bottom_low_conf or is_bottom_date:
            pad_x = max(70, int(bw * 0.55))
            pad_y = max(45, int(bh * 1.6))
            box = (max(0, x1 - pad_x), max(0, y1 - pad_y), min(w, x2 + pad_x), min(h, y2 + pad_y))
            prio = 20000 if (has_kw or is_bottom_date) else (15000 if is_stamp_date_hint else 7000)
            ocr_candidates.append({"bbox": box, "reason": f"ocr:{txt[:14]}", "score": prio})

    ink_candidates.sort(key=lambda c: c["score"], reverse=True)
    ocr_candidates.sort(key=lambda c: c["score"], reverse=True)

    selected = []
    pool = []
    for i in range(max(len(ocr_candidates), len(ink_candidates))):
        if i < len(ocr_candidates):
            pool.append(ocr_candidates[i])
        if i < len(ink_candidates):
            pool.append(ink_candidates[i])

    for c in pool:
        if any(_iou(c["bbox"], s["bbox"]) > 0.25 for s in selected):
            continue
        selected.append(c)
        if len(selected) >= max_crops:
            break

    pil_orig = Image.open(img_path).convert("RGB")
    out = []
    for s in selected:
        x1, y1, x2, y2 = s["bbox"]
        crop = pil_orig.crop((x1, y1, x2, y2))
        cw, ch = crop.size
        if cw < 640:
            scale = min(3.0, 680.0 / max(1, cw))
            crop = crop.resize((int(cw * scale), int(ch * scale)), Image.Resampling.LANCZOS)
        out.append({"bbox": list(s["bbox"]), "reason": s["reason"], "crop": crop})
    return out


def pil_to_data_url(im, quality=90):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}", b64


def render_annotated_overlay(img_path, ocr_lines, rois):
    """Draw OCR bounding boxes (blue) and detected stamp ROI boxes (orange-red) onto image."""
    im = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    stroke_ocr = max(1, min(w, h) // 600)
    stroke_roi = max(3, min(w, h) // 220)

    for item in ocr_lines:
        pts = [(int(p[0]), int(p[1])) for p in item["box"]]
        if len(pts) >= 4:
            draw.polygon(pts, outline=(37, 99, 235), width=stroke_ocr)

    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", max(16, min(w, h) // 45))
    except Exception:
        font = ImageFont.load_default()

    for idx, roi in enumerate(rois, 1):
        x1, y1, x2, y2 = roi["bbox"]
        draw.rectangle([(x1, y1), (x2, y2)], outline=(234, 88, 12), width=stroke_roi)
        label = f"ROI #{idx}"
        tb = draw.textbbox((x1, y1), label, font=font)
        tw, th = tb[2] - tb[0] + 10, tb[3] - tb[1] + 8
        ly = max(0, y1 - th)
        draw.rectangle([(x1, ly), (x1 + tw, ly + th)], fill=(234, 88, 12))
        draw.text((x1 + 5, ly + 3), label, fill=(255, 255, 255), font=font)

    # Cap preview max width at 1400px for smooth browser display
    if w > 1400:
        ratio = 1400.0 / w
        im = im.resize((1400, int(h * ratio)), Image.Resampling.LANCZOS)
    data_url, _ = pil_to_data_url(im, quality=88)
    return data_url
