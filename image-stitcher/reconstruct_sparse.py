# SPDX-License-Identifier: GPL-3.0-only
"""CPU-only sparse reconstruction from a ZIP of photographs."""

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import time
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_images(source, destination):
    """Preserve image bytes and group intrinsics by camera, resolution and focal length."""
    records = []
    names = set()
    with ZipFile(source) as archive:
        entries = [e for e in archive.infolist() if not e.is_dir()]
        if sum(e.file_size for e in entries) > 2 * 1024**3:
            raise ValueError("Photo archive exceeds the 2 GiB prototype limit")
        for entry in entries:
            name = PurePosixPath(entry.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or ":" in str(name):
                raise ValueError(f"Unsafe archive entry: {entry.filename}")
            if name.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            data = archive.read(entry)
            with Image.open(BytesIO(data)) as image:
                exif = image.getexif()
                if exif.get(274, 1) != 1:
                    raise ValueError(f"Normalize EXIF orientation before processing: {name}")
                camera = [str(exif.get(271, "unknown")), str(exif.get(272, "unknown")),
                          image.width, image.height]
                exif_camera = exif.get_ifd(34665)
                focal = exif_camera.get(37386)
                camera.append(str(focal) if focal is not None else None)
                size = [image.width, image.height]
            group = hashlib.sha256(json.dumps(camera).encode()).hexdigest()[:12]
            relative = Path(group) / name.name
            if relative.as_posix().casefold() in names:
                raise ValueError(f"Duplicate image name in camera group: {name}")
            names.add(relative.as_posix().casefold())
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            records.append({"source": entry.filename, "image": relative.as_posix(),
                            "camera_group": group, "camera_metadata": camera,
                            "size": size, "sha256": hashlib.sha256(data).hexdigest()})
    if len(records) < 3:
        raise ValueError("At least three images are required")
    return records


def summarize_model(model, model_id, records, destination):
    model_path = destination / "sparse" / str(model_id)
    model.write_text(model_path)
    model.export_PLY(model_path / "points.ply")
    registered = {image.name for image in model.images.values() if image.has_pose}
    cameras = []
    for image in model.images.values():
        if not image.has_pose:
            continue
        cameras.append({"image": image.name, "camera_id": image.camera_id,
                        "center": image.projection_center().tolist()})
    save_json(model_path / "camera_positions.json", cameras)
    points = list(model.points3D.values())
    errors = [p.error for p in points]
    stats = {
        "model_id": model_id,
        "registered_images": len(registered), "input_images": len(records),
        "unregistered_images": [r["image"] for r in records if r["image"] not in registered],
        "points3D": model.num_points3D(), "observations": model.compute_num_observations(),
        "mean_reprojection_error_px": model.compute_mean_reprojection_error(),
        "median_point_reprojection_error_px": float(np.median(errors)) if errors else None,
        "mean_track_length": model.compute_mean_track_length(),
        "cameras": [{"id": key, "model": camera.model_name,
                     "width": camera.width, "height": camera.height,
                     "params": camera.params.tolist()} for key, camera in model.cameras.items()],
    }
    save_json(model_path / "statistics.json", stats)
    return stats, points, cameras


def point_quality_mask(xyz, errors, track_lengths):
    xyz = np.asarray(xyz, dtype=np.float32)
    distances = np.zeros(len(xyz))
    threshold = None
    if len(xyz) > 17:
        cv2.setRNGSeed(0)
        index = cv2.flann_Index(xyz, dict(algorithm=1, trees=4))
        _, squared = index.knnSearch(xyz, 17, params=dict(checks=128))
        distances = np.sqrt(np.maximum(squared[:, 1:], 0)).mean(axis=1)
        median = float(np.median(distances))
        mad = float(np.median(np.abs(distances - median)))
        threshold = median + 6 * 1.4826 * mad
    isolated = distances > threshold if threshold is not None else np.zeros(len(xyz), dtype=bool)
    weak = np.asarray(track_lengths) < 3
    inaccurate = np.asarray(errors) > 1.5
    keep = ~(isolated | weak | inaccurate)
    return keep, {"hidden_points": int((~keep).sum()), "retained_points": int(keep.sum()),
                  "isolated_points": int(isolated.sum()), "short_tracks": int(weak.sum()),
                  "high_reprojection_error": int(inaccurate.sum()),
                  "neighbor_distance_threshold": threshold,
                  "min_track_length": 3, "max_reprojection_error_px": 1.5}


def write_preview(destination, points, cameras, stats):
    keep, quality = point_quality_mask([p.xyz for p in points], [p.error for p in points],
                                       [p.track.length() for p in points])
    data = {"points": [[*p.xyz.tolist(), *p.color.tolist(), bool(good)] for p, good in zip(points, keep)],
            "cameras": cameras, "stats": stats, "quality_filter": quality,
            "surface_preview": (destination / "surface/preview.html").is_file()}
    save_json(destination / "preview_filter.json", quality)
    template = (ROOT / "templates/sparse_preview.html").read_text(encoding="utf-8")
    encoded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    temporary = destination / "preview.html.tmp"
    temporary.write_text(template.replace("__SCENE_DATA__", encoded), encoding="utf-8")
    temporary.replace(destination / "preview.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photos", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New, empty output directory")
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--max-features", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if min(args.max_image_size, args.max_features, args.threads) < 1:
        parser.error("Image size, feature limit and threads must be positive")
    if not args.photos.is_file():
        parser.error("Photo ZIP does not exist")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output must be empty; use a new directory to preserve previous runs")
    sys.path.insert(0, str(ROOT / ".cache/photogrammetry"))
    try:
        import pycolmap
    except ImportError as error:
        parser.error(f"Install requirements-photogrammetry.txt first: {error}")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
              "pycolmap_version": pycolmap.__version__, "device": "cpu",
              "settings": {"max_image_size": args.max_image_size,
                           "max_features": args.max_features, "threads": args.threads,
                           "camera_model": "SIMPLE_RADIAL", "random_seed": 0},
              "limitations": ["Sparse reconstruction only; no dense mesh or texture.",
                              "Scale and world coordinates are not calibrated.",
                              "Reprojection error is not absolute geometric accuracy."]}

    def stage(name):
        report["stage"] = name
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save_json(output / "report.json", report)
        print(f"STAGE {name}", flush=True)

    try:
        stage("prepare")
        records = prepare_images(args.photos, output / "images")
        save_json(output / "images.json", records)
        report["input_images"] = len(records)
        database = output / "database.db"
        stage("extract_features")
        pycolmap.extract_features(
            database, output / "images", camera_mode=pycolmap.CameraMode.PER_FOLDER,
            reader_options={"camera_model": "SIMPLE_RADIAL"},
            extraction_options={"max_image_size": args.max_image_size,
                                "num_threads": args.threads, "use_gpu": False,
                                "sift": {"max_num_features": args.max_features}},
            device=pycolmap.Device.cpu)
        stage("match_exhaustive")
        pycolmap.match_exhaustive(
            database, matching_options={"num_threads": args.threads, "use_gpu": False},
            device=pycolmap.Device.cpu)
        with sqlite3.connect(database) as connection:
            report["matching"] = {
                "feature_rows": connection.execute("SELECT SUM(rows) FROM keypoints").fetchone()[0],
                "verified_pairs": connection.execute("SELECT COUNT(*) FROM two_view_geometries WHERE rows >= 15").fetchone()[0],
                "possible_pairs": len(records) * (len(records) - 1) // 2}
        stage("incremental_mapping")
        sparse = output / "sparse"
        sparse.mkdir()
        models = pycolmap.incremental_mapping(
            database, output / "images", sparse,
            options={"num_threads": args.threads, "random_seed": 0, "ba_use_gpu": False,
                     "mapper": {"num_threads": args.threads, "random_seed": 0}})
        report["models"] = []
        best = None
        for model_id, model in models.items():
            stats, points, cameras = summarize_model(model, model_id, records, output)
            report["models"].append(stats)
            if best is None or stats["registered_images"] > best[0]["registered_images"]:
                best = stats, points, cameras
        if best is None:
            report["status"] = "no_reconstruction"
        else:
            report["best_model_id"] = best[0]["model_id"]
            report["status"] = "all_images_registered" if best[0]["registered_images"] == len(records) else "partial_reconstruction"
            write_preview(output, best[1], best[2], best[0])
        stage("finished")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if best is not None else 2
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        stage("failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
