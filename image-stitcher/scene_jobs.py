# SPDX-License-Identifier: GPL-3.0-only
"""Queue uploaded drone video or photographs and turn each upload into a textured 3D scene.

One CPU job runs at a time. Every step (frame selection, COLMAP camera solving,
OpenMVS surface and texture) runs in its own process, so the web server stays
responsive, a job can be cancelled, and each step keeps its log beside the results.
"""

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageOps

from video_frames import VIDEO_SUFFIXES

ROOT = Path(__file__).resolve().parent
JOB_ROOT = ROOT / "outputs" / "scenes"
JOB_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
THREADS = max(1, min(4, os.cpu_count() or 1))

PRESETS = {
    "fast": {"label": "快速預覽", "frames": 40, "frame_side": 1280, "features": 4096,
             "surface_side": 1024, "dense": 512},
    "standard": {"label": "標準", "frames": 70, "frame_side": 1600, "features": 4096,
                 "surface_side": 1280, "dense": 640},
}
STAGE_LABELS = {
    "queued": "排隊中", "scan_video": "影片自動切片", "prepare": "整理照片",
    "extract_features": "擷取特徵點", "match_exhaustive": "影像兩兩配對",
    "match_sequential": "相鄰影格配對",
    "incremental_mapping": "相機定位與稀疏點雲", "undistort_images": "影像去畸變",
    "InterfaceCOLMAP": "轉換為 OpenMVS 場景", "DensifyPointCloud": "密集點雲",
    "ReconstructMesh": "建立表面網格", "TextureMesh": "貼上照片紋理",
    "embed_textures": "封裝 GLB 模型", "project_suspect_points": "點雲修整",
    "prepare_viewer": "準備 3D 檢視器", "finished": "完成",
}
VIDEO_STAGES = ["scan_video", "extract_features", "match_sequential", "incremental_mapping",
                "undistort_images", "InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh",
                "TextureMesh", "embed_textures", "project_suspect_points", "prepare_viewer"]
PHOTO_STAGES = ["prepare", "extract_features", "match_exhaustive", *VIDEO_STAGES[3:]]
SEQUENTIAL_OVERLAP = 10  # video frames are time-ordered; match neighbours and close the orbit
ACTIVE = {"queued", "running"}


class JobError(Exception):
    """A failure with a message meant for the person using the page."""


class StepFailed(Exception):
    """A pipeline process exited with an unexpected code."""


class Cancelled(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify(names):
    """Return 'video' for exactly one video, 'photos' for images/ZIPs; reject anything else."""
    suffixes = [Path(name).suffix.lower() for name in names]
    if not suffixes:
        raise JobError("請選擇一段影片，或至少三張照片（也可上傳照片 ZIP）。")
    if any(s in VIDEO_SUFFIXES for s in suffixes):
        if len(suffixes) > 1:
            raise JobError("一次只能處理一段影片；影片請勿與照片混合上傳。")
        return "video"
    unsupported = sorted({s or "(無副檔名)" for s in suffixes if s not in PHOTO_SUFFIXES | {".zip"}})
    if unsupported:
        raise JobError(f"不支援的檔案類型：{', '.join(unsupported)}。影片請用 MP4／MOV／WebM，照片請用 JPG／PNG。")
    if ".zip" not in suffixes and len(suffixes) < 3:
        raise JobError("照片至少需要三張，且要從不同位置拍攝同一場景。")
    return "photos"


def upright(data, suffix):
    """Apply EXIF orientation so COLMAP sees upright pixels; keep camera tags for intrinsics."""
    with Image.open(BytesIO(data)) as image:
        exif = image.getexif()
        if exif.get(274, 1) == 1:
            return data
        rotated = ImageOps.exif_transpose(image)
        exif[274] = 1
        buffer = BytesIO()
        if suffix == ".png":
            rotated.save(buffer, format="PNG", exif=exif)
        else:
            rotated.convert("RGB").save(buffer, format="JPEG", quality=95, exif=exif)
        return buffer.getvalue()


def pack_photos(paths, archive_path):
    """Collect photos (loose or inside ZIPs) into one flat, uniquely named ZIP for COLMAP."""
    count = 0

    def add(archive, name, data):
        nonlocal count
        suffix = Path(name).suffix.lower()
        stem = re.sub(r"[^0-9A-Za-z_-]+", "_", Path(name).stem)[:40] or "photo"
        count += 1
        try:
            data = upright(data, suffix)
        except OSError as error:
            raise JobError(f"無法讀取照片 {name}：{error}") from error
        archive.writestr(f"{count:04d}_{stem}{'.jpg' if suffix == '.jpeg' else suffix}", data)

    with ZipFile(archive_path, "w", ZIP_STORED) as archive:
        for path in paths:
            if path.suffix.lower() != ".zip":
                add(archive, path.name, path.read_bytes())
                continue
            with ZipFile(path) as source:
                entries = [e for e in source.infolist() if not e.is_dir()]
                if sum(e.file_size for e in entries) > 2 * 1024**3:
                    raise JobError("照片 ZIP 解壓後超過 2 GiB。")
                for entry in entries:
                    name = PurePosixPath(entry.filename.replace("\\", "/"))
                    if name.is_absolute() or ".." in name.parts or ":" in str(name):
                        raise JobError(f"ZIP 內含不安全的路徑：{entry.filename}")
                    if name.suffix.lower() in PHOTO_SUFFIXES and not name.name.startswith("._"):
                        add(archive, name.name, source.read(entry))
    if count < 3:
        raise JobError("可用照片不足三張（支援 JPG／PNG）。")
    return count


def kill_tree(process):
    """Stop a step and the OpenMVS/COLMAP programs it started."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def tail(path, lines):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip() and not line.startswith("PROGRESS ")][-lines:]


class SceneJobs:
    def __init__(self, root=JOB_ROOT, python=sys.executable, threads=THREADS):
        self.root, self.python, self.threads = Path(root), python, threads
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.queue, self.cancelled = [], set()
        self.current, self.process, self.worker = None, None, None
        self.root.mkdir(parents=True, exist_ok=True)
        for job in self.list():
            if job["status"] in ACTIVE:
                job.update(status="interrupted", finished_utc=now(),
                           error="伺服器在處理期間重新啟動，這個工作已中斷；請重新上傳。")
                self.save(job)

    # -- storage -----------------------------------------------------------------
    def directory(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.match(job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def load(self, job_id):
        # Reads share the lock with writes: Windows cannot replace a file another thread has open.
        with self.lock:
            try:
                return json.loads((self.directory(job_id) / "job.json").read_text(encoding="utf-8"))
            except (KeyError, OSError, ValueError):
                return None

    def save(self, job):
        with self.lock:
            path = self.directory(job["id"]) / "job.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(20):
                try:
                    temporary.replace(path)
                    return
                except PermissionError:  # e.g. antivirus or an editor briefly holding job.json
                    if attempt == 19:
                        raise
                    time.sleep(.05)

    def list(self):
        jobs = [self.load(p.name) for p in self.root.iterdir() if p.is_dir() and JOB_ID.match(p.name)]
        return sorted((j for j in jobs if j), key=lambda j: j["id"], reverse=True)

    def log_tail(self, job_id, lines=25):
        job = self.load(job_id)
        if not job or not job.get("log"):
            return []
        return tail(self.directory(job_id) / "logs" / job["log"], lines)

    def public(self, job):
        """Job state for the browser, with readable stage labels and queue position."""
        if not job:
            return None
        stages = [{"key": key, "label": STAGE_LABELS[key], "started_utc": job["stage_started"].get(key)}
                  for key in job["stages"]]
        with self.lock:
            position = self.queue.index(job["id"]) + 1 if job["id"] in self.queue else 0
        return {**job, "stage_label": STAGE_LABELS.get(job["stage"], job["stage"]), "stage_list": stages,
                "queue_position": position,
                "preset_label": PRESETS.get(job["preset"], {}).get("label", job["preset"])}

    # -- lifecycle ---------------------------------------------------------------
    def create(self, kind, names, writer, preset="standard", frames=None, start=None, end=None, label=None):
        """Create a queued job; `writer(input_dir)` stores the uploaded files there."""
        if preset not in PRESETS:
            raise JobError("未知的品質設定。")
        settings = dict(PRESETS[preset], threads=self.threads)
        if kind == "video":
            settings["frames"] = int(frames or settings["frames"])
            if not 10 <= settings["frames"] <= 150:
                raise JobError("影格數需介於 10 到 150。")
            if start is not None and start < 0:
                raise JobError("起始秒數不可為負。")
            if end is not None and end <= (start or 0):
                raise JobError("結束秒數需大於起始秒數。")
            settings.update(start=start, end=end)
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        directory = self.directory(job_id)
        (directory / "input").mkdir(parents=True)
        try:
            writer(directory / "input")
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        job = {"id": job_id, "label": label or (names[0] if len(names) == 1 else f"{len(names)} 個檔案"),
               "kind": kind, "inputs": list(names), "preset": preset, "settings": settings,
               "status": "queued", "stage": "queued", "stages": VIDEO_STAGES if kind == "video" else PHOTO_STAGES,
               "stage_started": {}, "created_utc": now(), "warnings": [], "log": None}
        self.save(job)
        with self.wake:
            self.queue.append(job_id)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, name="scene-jobs", daemon=True)
                self.worker.start()
            self.wake.notify()
        return job

    def cancel(self, job_id):
        with self.lock:
            job = self.load(job_id)
            if not job or job["status"] not in ACTIVE:
                return job
            if job_id in self.queue:
                self.queue.remove(job_id)
                job.update(status="cancelled", finished_utc=now())
                self.save(job)
                return job
            self.cancelled.add(job_id)
            process = self.process if self.current == job_id else None
        if process:
            kill_tree(process)
        return self.load(job_id)

    def delete(self, job_id):
        job = self.load(job_id)
        if not job:
            return False
        if job["status"] in ACTIVE:
            raise JobError("工作仍在進行中，請先取消。")
        shutil.rmtree(self.directory(job_id))
        return True

    # -- worker ------------------------------------------------------------------
    def _work(self):
        while True:
            with self.wake:
                while not self.queue:
                    self.wake.wait()
                job_id = self.queue.pop(0)
                self.current = job_id
            try:
                self._process(job_id)
            except Exception:  # Never let one job's bookkeeping error stop the queue.
                import traceback
                traceback.print_exc()
            finally:
                with self.lock:
                    self.current, self.process = None, None
                    self.cancelled.discard(job_id)

    def _process(self, job_id):
        job = self.load(job_id)
        started = time.monotonic()
        try:
            job.update(status="running", started_utc=now())
            self.save(job)
            self._pipeline(job)
            job.update(status="completed", stage="finished")
        except Cancelled:
            job.update(status="cancelled", error="已取消。")
        except JobError as error:
            job.update(status="failed", error=str(error))
        except Exception as error:  # Unexpected: keep the server alive and report it.
            job.update(status="failed", error=f"未預期的錯誤：{error}")
        finally:
            job.update(finished_utc=now(), elapsed_seconds=round(time.monotonic() - started, 1))
            self.save(job)

    def _stage(self, job, stage):
        if stage in job["stages"] and stage != job.get("stage"):
            job["stage"] = stage
            job["stage_started"][stage] = now()
            self.save(job)

    def _check_cancel(self, job):
        with self.lock:
            if job["id"] in self.cancelled:
                raise Cancelled()

    def _run(self, job, step, arguments, accept=(0,)):
        """Run one pipeline script, streaming its log and STAGE/WARNING lines into the job."""
        logs = self.directory(job["id"]) / "logs"
        logs.mkdir(exist_ok=True)
        log_path = logs / f"{step}.log"
        job["log"] = log_path.name
        self.save(job)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        options = {"start_new_session": True} if os.name != "nt" else {}
        with self.lock:
            self._check_cancel(job)
            process = self.process = subprocess.Popen(
                [self.python, "-u", *arguments], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **options)
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith("STAGE "):
                    self._stage(job, line.split(None, 1)[1].strip())
                elif line.startswith("WARNING "):
                    job["warnings"].append(line[8:].strip())
                    self.save(job)
        code = process.wait()
        self._check_cancel(job)
        if code not in accept:
            raise StepFailed(code)
        return code

    def _failure(self, prefix, log_path, report_path=None):
        detail = None
        if report_path:
            try:
                detail = json.loads(report_path.read_text(encoding="utf-8")).get("error")
            except (OSError, ValueError):
                pass
        if not detail:
            lines = tail(log_path, 1)
            detail = lines[-1].strip() if lines else "程序沒有輸出"
        return JobError(f"{prefix}：{detail}")

    def _pipeline(self, job):
        directory = self.directory(job["id"])
        logs = directory / "logs"
        settings = job["settings"]
        inputs = sorted((directory / "input").iterdir())
        archive = directory / "images.zip"
        if job["kind"] == "video":
            arguments = ["video_frames.py", str(inputs[0]), "--output", str(archive),
                         "--frames", str(settings["frames"]), "--max-side", str(settings["frame_side"])]
            if settings.get("start"):
                arguments += ["--start", str(settings["start"])]
            if settings.get("end"):
                arguments += ["--end", str(settings["end"])]
            try:
                self._run(job, "frames", arguments)
            except StepFailed:
                raise self._failure("影片切片失敗", logs / "frames.log") from None
            manifest = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
            job["frames"] = manifest["selected_frames"]
            job["video"] = {"size": manifest["size"], "fps": manifest["fps"],
                            "decoded_frames": manifest["decoded_frames"]}
        else:
            self._stage(job, "prepare")
            job["frames"] = pack_photos(inputs, archive)
        self.save(job)

        sparse = directory / "sparse"
        try:
            self._run(job, "sparse", ["reconstruct_sparse.py", str(archive), "--output", str(sparse),
                                      "--max-image-size", str(settings["frame_side"]),
                                      "--max-features", str(settings["features"]),
                                      "--threads", str(settings["threads"]),
                                      "--sequential-overlap", str(SEQUENTIAL_OVERLAP if job["kind"] == "video" else 0)],
                      accept=(0, 2))
        except StepFailed:
            raise self._failure("相機定位失敗", logs / "sparse.log", sparse / "report.json") from None
        report = json.loads((sparse / "report.json").read_text(encoding="utf-8"))
        if report.get("status") == "no_reconstruction":
            raise JobError("無法定位相機：影像間的重疊或紋理不足。請使用環繞拍攝、移動較慢的影片，或提高影格數。")
        best = next(m for m in report["models"] if m["model_id"] == report["best_model_id"])
        job["registered"] = [best["registered_images"], best["input_images"]]
        job["sparse_points"] = best["points3D"]
        if best["registered_images"] < best["input_images"]:
            job["warnings"].append(f"{best['input_images']} 張影像中有 {best['registered_images']} 張成功定位，"
                                   "其餘影像未用於建模。")
        if best["registered_images"] < 5:
            raise JobError("成功定位的影像少於 5 張，不足以建立表面。請改用重疊更多的影片或照片。")
        self.save(job)

        surface = sparse / "surface"
        try:
            self._run(job, "surface", ["reconstruct_surface.py", str(sparse), "--output", str(surface),
                                       "--max-image-size", str(settings["surface_side"]),
                                       "--dense-resolution", str(settings["dense"]),
                                       "--threads", str(settings["threads"])])
        except StepFailed:
            raise self._failure("表面重建失敗", logs / "surface.log", surface / "surface_report.json") from None
        surface_report = json.loads((surface / "surface_report.json").read_text(encoding="utf-8"))
        job["mesh"] = surface_report.get("mesh")
        job["result"] = {"viewer": "surface/preview.html", "model": "surface/model.glb",
                         "points": "surface/points_surface_repaired.ply", "sparse_preview": "preview.html",
                         "model_bytes": (surface / "model.glb").stat().st_size}
