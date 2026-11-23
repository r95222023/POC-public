import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ocr_pipeline import (
    build_prompt,
    decode_ollama_byte_tokens,
    extract_local_date_candidates,
    parse_structured_json,
    run_ocr_pipeline,
)
from server import OCRHandler, _normalize_http_url
from stamp_detector import detect_stamp_rois

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def test_decode_ollama_byte_tokens():
    # \u3000 (full-width space: E3 80 80) -> normalized to ASCII space
    cleaned, count = decode_ollama_byte_tokens("發文<0xE3><0x80><0x80>日期")
    assert cleaned == "發文 日期"
    assert count == 3

    # Multi-byte UTF-8 Chinese character ("測": E6 B8 AC)
    cleaned2, count2 = decode_ollama_byte_tokens("<0xE6><0xB8><0xAC>試")
    assert cleaned2 == "測試"
    assert count2 == 3

    # Invalid byte sequence stays untouched
    raw = "<0xFF><0xFE>"
    cleaned3, count3 = decode_ollama_byte_tokens(raw)
    assert cleaned3 == raw
    assert count3 == 0


def test_parse_structured_json():
    fenced = '結果如下：\n```json\n{"stamp_dates": [{"date": "115.11.09"}], "full_text": "測試"}\n```'
    parsed = parse_structured_json(fenced)
    assert parsed["stamp_dates"][0]["date"] == "115.11.09"
    assert parsed["full_text"] == "測試"

    raw = '{"doc_fields": {"agency": "甲機關"}, "full_text": "內文"}'
    assert parse_structured_json(raw)["doc_fields"]["agency"] == "甲機關"

    assert parse_structured_json("not json at all") is None


def test_extract_local_date_candidates():
    ocr_lines = [
        {"text": "發文日期：中華民國 115 年 11 月 08 日", "score": 0.95},
        {"text": "發文日期：中華民國 115 年 11 月 08 日", "score": 0.95},  # duplicate in same source
    ]
    rois = [
        {
            "reason": "red_ink",
            "ocr": [{"text": "115.11.09 09:42", "score": 0.99}],
        }
    ]
    dates = extract_local_date_candidates(ocr_lines, rois)
    texts = [d["text"] for d in dates]
    assert len(dates) == 2
    assert any("115" in t and "11" in t for t in texts)
    assert "115.11.09 09:42" in texts


def test_build_prompt_modes():
    p_vlm = build_prompt("stamp_only", "vlm_only", "ignored", [])
    assert "輔助資料" not in p_vlm
    assert "stamp_dates" in p_vlm

    p_hybrid = build_prompt(
        "doc_full",
        "hybrid",
        "[y=10%, x=50%, conf=0.98] 收文專用章",
        [{"bbox": [10, 10, 100, 100], "reason": "red_ink", "ocr": [{"text": "115.11.09", "score": 0.99}]}],
    )
    assert "輔助資料" in p_hybrid
    assert "共提供 2 張圖片" in p_hybrid
    assert "收文專用章" in p_hybrid


def test_normalize_http_url():
    assert _normalize_http_url("http://127.0.0.1:11434/", "http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert _normalize_http_url("", "http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    with pytest.raises(ValueError):
        _normalize_http_url("file:///etc/passwd", "http://127.0.0.1:11434")


def test_pipeline_ocr_only_on_scanned_sample():
    sample = SAMPLES_DIR / "sample_official_scanned.png"
    assert sample.is_file()

    result = run_ocr_pipeline(str(sample), mode="ocr_only", task_type="doc_full")
    assert result["mode"] == "ocr_only"
    assert result["vlm"] is None
    assert result["vlm_error"] is None
    assert result["ocr"]["line_count"] >= 5
    assert result["stamps"]["roi_count"] >= 1
    assert result["stamps"]["overlay_data_url"].startswith("data:image/jpeg;base64,")
    assert any("115.11.09" in d["text"] for d in result["ocr"]["date_candidates"])

    rois = detect_stamp_rois(str(sample), result["ocr"]["lines"], max_crops=3)
    assert len(rois) >= 1


def test_http_server_endpoints():
    server = ThreadingHTTPServer(("127.0.0.1", 0), OCRHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
            assert health["ok"] is True
            assert health["default_ollama_url"].startswith("http")

        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "公文與圖片 OCR 混合辨識工作台" in html

        with urllib.request.urlopen(f"{base}/samples/sample_official_clean.png", timeout=5) as resp:
            assert resp.status == 200
            assert resp.read(4) == b"\x89PNG"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}/samples/does_not_exist.png", timeout=5)
        assert exc_info.value.code == 404

        # Invalid POST without image data -> 400
        bad_req = urllib.request.Request(
            f"{base}/api/ocr",
            data=json.dumps({"mode": "ocr_only"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as bad_exc:
            urllib.request.urlopen(bad_req, timeout=5)
        assert bad_exc.value.code == 400

        # Valid POST ocr_only -> 200
        img_b64 = base64.b64encode((SAMPLES_DIR / "sample_official_scanned.png").read_bytes()).decode("utf-8")
        ok_req = urllib.request.Request(
            f"{base}/api/ocr",
            data=json.dumps({
                "image_data_url": f"data:image/png;base64,{img_b64}",
                "filename": "sample_official_scanned.png",
                "mode": "ocr_only",
                "task_type": "doc_full",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(ok_req, timeout=30) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["filename"] == "sample_official_scanned.png"
            assert payload["ocr"]["line_count"] >= 5
            assert payload["stamps"]["roi_count"] >= 1
    finally:
        server.shutdown()
        server.server_close()
