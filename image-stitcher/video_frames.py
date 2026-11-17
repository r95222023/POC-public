# SPDX-License-Identifier: GPL-3.0-only
"""Pick sharp frames spread evenly along the camera path of a drone video.

Frames are chosen by accumulated camera motion rather than by time, so a drone
that hovers does not produce runs of near-identical photographs, and the
sharpest candidate inside each motion interval is kept to avoid motion blur.
"""

import argparse
import json
from pathlib import Path
import time
from zipfile import ZIP_STORED, ZipFile

import cv2
import numpy as np

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts"}
PROBE_WIDTH = 160
SHARPNESS_WIDTH = 640
MIN_TOTAL_MOTION = 0.3      # frame widths; below this the camera barely moved
MIN_SPACING = 0.01          # never pick frames closer than 1% of a frame width apart
WIDE_SPACING = 0.25         # warn when neighbours overlap less than roughly 75%


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def open_video(path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("無法開啟影片；請改用 H.264／H.265 MP4 或 VP9 WebM。")
    return capture


def frame_rate(capture):
    fps = capture.get(cv2.CAP_PROP_FPS)
    return float(fps) if fps and np.isfinite(fps) and 0 < fps <= 480 else None


def resize_width(gray, width):
    h, w = gray.shape[:2]
    if w <= width:
        return gray
    return cv2.resize(gray, (width, max(8, round(h * width / w))), interpolation=cv2.INTER_AREA)


def scan(path, samples_per_second=6.0, start=0.0, end=None, progress=None):
    """Decode once and measure sharpness and camera motion on sampled frames."""
    capture = open_video(path)
    fps = frame_rate(capture)
    assumed_fps = fps or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stride = max(1, round(assumed_fps / samples_per_second))
    candidates, previous, size, index = [], None, None, -1
    try:
        while capture.grab():
            index += 1
            moment = index / assumed_fps
            if moment < start or index % stride:
                continue
            if end is not None and moment > end:
                break
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                continue
            size = size or [int(frame.shape[1]), int(frame.shape[0])]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(resize_width(gray, SHARPNESS_WIDTH), cv2.CV_64F).var())
            small = resize_width(gray, PROBE_WIDTH)
            motion = 0.0
            if previous is not None and previous.shape == small.shape:
                flow = cv2.calcOpticalFlowFarneback(previous, small, None, .5, 3, 15, 3, 5, 1.2, 0)
                motion = float(np.mean(np.linalg.norm(flow, axis=2))) / small.shape[1]
            previous = small
            candidates.append({"frame": index, "time": round(moment, 3),
                               "sharpness": round(sharpness, 2), "motion": round(motion, 5)})
            if progress and total > 0 and len(candidates) % 20 == 0:
                progress(min(1.0, index / total))
    finally:
        capture.release()
    if index < 0:
        raise ValueError("影片沒有可解碼的畫面；請確認檔案完整或改用 H.264 MP4。")
    return {"fps": fps, "decoded_frames": index + 1, "reported_frames": total,
            "size": size, "sample_stride": stride}, candidates


def select(candidates, target):
    """Split the cumulative motion curve into equal parts; keep the sharpest frame of each."""
    if len(candidates) < 3:
        raise ValueError("影片太短，可用畫面不足三張。")
    motion = np.array([c["motion"] for c in candidates], dtype=float)
    cumulative = np.cumsum(motion)
    total = float(cumulative[-1])
    if total < MIN_TOTAL_MOTION:
        raise ValueError("影片中的相機幾乎沒有移動；建模需要環繞或平移拍攝，從不同位置看到同一場景。")
    count = int(min(target, len(candidates), max(3, total / MIN_SPACING)))
    edges = np.linspace(0, total, count + 1)
    bins = np.clip(np.searchsorted(edges, cumulative, side="right") - 1, 0, count - 1)
    sharpness = np.array([c["sharpness"] for c in candidates], dtype=float)
    floor = .35 * float(np.median(sharpness))
    chosen, blurry = [], 0
    for b in range(count):
        members = np.flatnonzero(bins == b)
        if not len(members):
            continue
        best = int(members[np.argmax(sharpness[members])])
        if sharpness[best] < floor:
            blurry += 1
            continue
        chosen.append(best)
    if len(chosen) < 3:
        raise ValueError("清晰畫面不足三張；影片可能過度模糊或過暗。")
    spacing = total / count
    summary = {"total_motion_widths": round(total, 3), "target_frames": int(target),
               "selected_frames": len(chosen), "dropped_blurry_intervals": blurry,
               "mean_spacing_widths": round(spacing, 4), "sharpness_floor": round(floor, 2),
               "warnings": []}
    if spacing > WIDE_SPACING:
        summary["warnings"].append("相鄰影格移動超過畫面寬度的 25%，重疊可能不足；可提高影格數。")
    if count < target:
        summary["warnings"].append("相機移動量有限，實際影格數少於設定值。")
    return [candidates[i] for i in chosen], summary


def write_frames(path, selected, destination, max_side=1600, quality=95):
    """Re-decode sequentially and store the chosen frames as JPEGs in an uncompressed ZIP."""
    wanted = {c["frame"]: n for n, c in enumerate(sorted(selected, key=lambda c: c["frame"]), 1)}
    last = max(wanted)
    capture = open_video(path)
    names, index = [], -1
    try:
        with ZipFile(destination, "w", ZIP_STORED) as archive:
            while index < last and capture.grab():
                index += 1
                if index not in wanted:
                    continue
                ok, frame = capture.retrieve()
                if not ok or frame is None:
                    continue
                h, w = frame.shape[:2]
                if max(h, w) > max_side:
                    scale = max_side / max(h, w)
                    frame = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
                ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
                if not ok:
                    raise ValueError("無法編碼影格。")
                name = f"frame_{wanted[index]:04d}_{index:06d}.jpg"
                archive.writestr(name, buffer.tobytes())
                names.append(name)
    finally:
        capture.release()
    if len(names) != len(wanted):
        raise ValueError("第二次解碼時影格數不一致；影片可能損毀。")
    return names


def extract(video, output, frames=60, max_side=1600, start=0.0, end=None, progress=None):
    started = time.monotonic()
    info, candidates = scan(video, start=start, end=end, progress=progress)
    selected, summary = select(candidates, frames)
    names = write_frames(video, selected, output, max_side=max_side)
    manifest = {"video": Path(video).name, **info, "settings": {"frames": frames, "max_side": max_side,
                "start": start, "end": end}, **summary,
                "frames": [{**c, "image": name} for c, name in zip(sorted(selected, key=lambda c: c["frame"]), names)],
                "seconds": round(time.monotonic() - started, 2)}
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="ZIP to create with the chosen JPEG frames")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--max-side", type=int, default=1600)
    parser.add_argument("--start", type=float, default=0.0, help="Ignore footage before this many seconds")
    parser.add_argument("--end", type=float, help="Ignore footage after this many seconds")
    args = parser.parse_args()
    if args.frames < 3 or args.max_side < 64:
        parser.error("Need at least 3 frames and a 64 px long side")
    if args.end is not None and args.end <= args.start:
        parser.error("--end must be after --start")
    if args.output.exists():
        parser.error("Output already exists")
    print("STAGE scan_video", flush=True)
    manifest = extract(args.video, args.output, args.frames, args.max_side, args.start, args.end,
                       progress=lambda value: print(f"PROGRESS {value:.3f}", flush=True))
    save_json(args.output.with_suffix(".json"), manifest)
    print(f"SELECTED {manifest['selected_frames']}", flush=True)
    for warning in manifest["warnings"]:
        print("WARNING " + warning, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
