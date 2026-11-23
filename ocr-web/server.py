import argparse
import base64
import json
import mimetypes
import os
import tempfile
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ocr_pipeline import DEFAULT_OLLAMA_URL, get_ocr_engine, run_ocr_pipeline

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
SAMPLES_DIR = os.path.join(BASE_DIR, "samples")
DEFAULT_HOST = os.environ.get("OCR_WEB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("OCR_WEB_PORT", "8765"))


def _normalize_http_url(url, fallback):
    candidate = (url or "").strip() or fallback
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid HTTP(S) URL: {candidate}")
    return candidate.rstrip("/")


class OCRHandler(BaseHTTPRequestHandler):
    server_version = "OCRWorkbench/1.0"

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, fpath):
        if not os.path.isfile(fpath):
            self.send_error(404, "Not found")
            return
        mime, _ = mimetypes.guess_type(fpath)
        with open(fpath, "rb") as fp:
            data = fp.read()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"{mime or 'application/octet-stream'}; charset=utf-8"
            if (mime or "").startswith("text/")
            else (mime or "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))

        if path.startswith("/samples/"):
            fname = os.path.basename(urllib.parse.unquote(path[len("/samples/"):]))
            target = os.path.abspath(os.path.join(SAMPLES_DIR, fname))
            if not target.startswith(os.path.abspath(SAMPLES_DIR) + os.sep):
                self.send_error(403, "Forbidden")
                return
            return self._send_file(target)

        if path == "/api/health":
            return self._send_json(200, {"ok": True, "default_ollama_url": DEFAULT_OLLAMA_URL})

        if path == "/api/ollama/tags":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                ollama_url = _normalize_http_url((qs.get("url") or [""])[0], DEFAULT_OLLAMA_URL)
            except ValueError as exc:
                return self._send_json(200, {"online": False, "error": str(exc), "models": []})
            try:
                req = urllib.request.Request(f"{ollama_url}/api/tags")
                with urllib.request.urlopen(req, timeout=4) as r:
                    tags = json.loads(r.read().decode("utf-8"))
                ver = None
                try:
                    with urllib.request.urlopen(urllib.request.Request(f"{ollama_url}/api/version"), timeout=3) as vr:
                        ver = json.loads(vr.read().decode("utf-8")).get("version")
                except Exception:
                    pass
                models = []
                for m in tags.get("models", []):
                    caps = m.get("capabilities", [])
                    models.append({
                        "name": m.get("name"),
                        "size_gb": round(m.get("size", 0) / (1024**3), 1),
                        "capabilities": caps,
                        "parameter_size": (m.get("details") or {}).get("parameter_size", ""),
                    })
                return self._send_json(200, {"online": True, "version": ver, "models": models})
            except Exception as exc:
                return self._send_json(200, {"online": False, "error": str(exc), "models": []})

        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/ocr":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 35 * 1024 * 1024:
            return self._send_json(400, {"error": "Invalid payload size (max 35MB)."})

        try:
            raw_body = self.rfile.read(length).decode("utf-8")
            req_data = json.loads(raw_body)
        except Exception as exc:
            return self._send_json(400, {"error": f"Invalid JSON body: {exc}"})

        data_url = req_data.get("image_data_url", "")
        if "," in data_url:
            header, b64_str = data_url.split(",", 1)
        else:
            header, b64_str = "", data_url

        if not b64_str:
            return self._send_json(400, {"error": "Missing image data."})

        ext = ".png"
        if "jpeg" in header or "jpg" in header:
            ext = ".jpg"
        elif "webp" in header:
            ext = ".webp"

        mode = req_data.get("mode", "hybrid")
        try:
            ollama_url = _normalize_http_url(req_data.get("ollama_url"), DEFAULT_OLLAMA_URL) if mode in ("hybrid", "vlm_only") else DEFAULT_OLLAMA_URL
        except ValueError as exc:
            return self._send_json(400, {"error": str(exc)})

        tmp_path = None
        try:
            img_bytes = base64.b64decode(b64_str)
            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="ocr_upload_")
            os.close(fd)
            with open(tmp_path, "wb") as fp:
                fp.write(img_bytes)

            result = run_ocr_pipeline(
                img_path=tmp_path,
                mode=mode,
                task_type=req_data.get("task_type", "doc_full"),
                ollama_url=ollama_url,
                model=req_data.get("model", "gemma4:26b"),
                think=bool(req_data.get("think", False)),
                include_roi_crops=bool(req_data.get("include_roi_crops", True)),
            )
            result["filename"] = req_data.get("filename", "uploaded_image")
            return self._send_json(200, result)
        except Exception as exc:
            return self._send_json(500, {"error": str(exc)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="OCR Workbench HTTP Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: 8765)")
    args = parser.parse_args()

    print("Pre-warming RapidOCR engine...", flush=True)
    get_ocr_engine()
    server = ThreadingHTTPServer((args.host, args.port), OCRHandler)
    print(f"OCR Workbench running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
