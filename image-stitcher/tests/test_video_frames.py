from zipfile import ZipFile

import cv2
import numpy as np
import pytest

from video_frames import extract, select


def textured_strip(width=1400, height=260, seed=3):
    rng = np.random.default_rng(seed)
    base = cv2.resize(rng.integers(0, 255, (height // 8, width // 8, 3), dtype=np.uint8),
                      (width, height), interpolation=cv2.INTER_CUBIC)
    for _ in range(90):
        x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
        cv2.circle(base, (x, y), int(rng.integers(4, 20)), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return base


def write_video(path, offsets, blurred=(), size=(320, 240), fps=30):
    strip = textured_strip(height=size[1])
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    for index, offset in enumerate(offsets):
        frame = np.ascontiguousarray(strip[:, offset:offset + size[0]])
        if index in blurred:
            frame = cv2.GaussianBlur(frame, (0, 0), 6)
        writer.write(frame)
    writer.release()


def test_select_spreads_frames_by_motion_not_time():
    # 30 moving samples, 30 hovering samples, 30 moving samples.
    motion = [0] + [.05] * 29 + [0] * 30 + [.05] * 30
    candidates = [{"frame": i, "time": i / 6, "sharpness": 100.0, "motion": m} for i, m in enumerate(motion)]
    chosen, summary = select(candidates, 12)
    frames = [c["frame"] for c in chosen]
    hovering = [f for f in frames if 30 <= f < 60]
    assert len(chosen) == 12
    assert len(hovering) <= 1
    assert summary["warnings"] == []


def test_select_prefers_sharp_candidates_within_each_interval():
    candidates = [{"frame": i, "time": i, "sharpness": 10.0 if i % 2 else 200.0, "motion": .1}
                  for i in range(40)]
    chosen, _ = select(candidates, 10)
    assert all(c["sharpness"] == 200.0 for c in chosen)


def test_select_rejects_static_footage():
    candidates = [{"frame": i, "time": i, "sharpness": 50.0, "motion": .0001} for i in range(50)]
    with pytest.raises(ValueError, match="幾乎沒有移動"):
        select(candidates, 20)


def test_extract_writes_sharp_frames_from_real_video(tmp_path):
    pan = list(range(0, 480, 8))
    offsets = pan + [pan[-1]] * 60 + list(range(pan[-1], pan[-1] + 480, 8))
    blurred = set(range(0, len(offsets), 7))
    video = tmp_path / "orbit.avi"
    write_video(video, offsets, blurred)
    output = tmp_path / "frames.zip"
    manifest = extract(video, output, frames=16, max_side=200)
    with ZipFile(output) as archive:
        names = archive.namelist()
        first = cv2.imdecode(np.frombuffer(archive.read(names[0]), np.uint8), cv2.IMREAD_COLOR)
    assert len(names) == manifest["selected_frames"] >= 10
    assert max(first.shape[:2]) == 200
    frames = [f["frame"] for f in manifest["frames"]]
    assert not blurred & set(frames)
    assert sum(len(pan) <= f < len(pan) + 60 for f in frames) <= 2
    assert frames == sorted(frames)
