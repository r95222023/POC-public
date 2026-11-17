from io import BytesIO
import json
import time
from zipfile import ZipFile

from PIL import Image
import pytest

from scene_jobs import JobError, SceneJobs, classify, pack_photos


def jpeg(size=(40, 20), orientation=1):
    image = Image.new("RGB", size, "red")
    image.paste((0, 0, 255), (0, 0, size[0] // 2, size[1]))
    exif = Image.Exif()
    exif[271], exif[274] = "DJI", orientation
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def wait_for(manager, job_id, statuses, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.load(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(.05)
    raise AssertionError(f"job stayed {manager.load(job_id)['status']}")


class ScriptedJobs(SceneJobs):
    """Replace the heavy pipeline with a short Python step that speaks the same protocol."""
    script = "print('STAGE extract_features'); print('WARNING 測試警告'); print('STAGE match_sequential')"

    def _pipeline(self, job):
        self._run(job, "fake", ["-c", self.script])
        job["result"] = {"viewer": "surface/preview.html"}


@pytest.mark.parametrize("names, kind", [(["flight.MP4"], "video"), (["a.jpg", "b.JPEG", "c.png"], "photos"),
                                         (["photos.zip"], "photos")])
def test_classify_accepts_video_or_photo_sets(names, kind):
    assert classify(names) == kind


@pytest.mark.parametrize("names, message", [([], "請選擇"), (["a.mp4", "b.mov"], "一次只能"),
                                            (["a.jpg", "b.jpg"], "至少需要三張"), (["a.heic", "b.jpg", "c.jpg"], "不支援")])
def test_classify_rejects_unusable_selections(names, message):
    with pytest.raises(JobError, match=message):
        classify(names)


def test_pack_photos_rotates_by_exif_and_flattens_zip(tmp_path):
    (tmp_path / "loose.jpg").write_bytes(jpeg(orientation=6))
    with ZipFile(tmp_path / "more.zip", "w") as archive:
        archive.writestr("flight/one.jpg", jpeg())
        archive.writestr("flight/two.jpeg", jpeg())
        archive.writestr("__MACOSX/._two.jpeg", b"junk")
        archive.writestr("notes.txt", b"ignored")
    output = tmp_path / "images.zip"
    assert pack_photos([tmp_path / "loose.jpg", tmp_path / "more.zip"], output) == 3
    with ZipFile(output) as archive:
        names = archive.namelist()
        with Image.open(BytesIO(archive.read(names[0]))) as rotated:
            assert rotated.size == (20, 40)
            assert rotated.getexif().get(274) == 1
            assert rotated.getexif().get(271) == "DJI"
    assert names[1:] == ["0002_one.jpg", "0003_two.jpg"]


def test_pack_photos_rejects_unsafe_zip_paths(tmp_path):
    with ZipFile(tmp_path / "bad.zip", "w") as archive:
        archive.writestr("../escape.jpg", jpeg())
    with pytest.raises(JobError, match="不安全"):
        pack_photos([tmp_path / "bad.zip"], tmp_path / "images.zip")


def test_job_runs_steps_records_stages_and_warnings(tmp_path):
    manager = ScriptedJobs(tmp_path / "jobs")
    job = manager.create("video", ["clip.mp4"], lambda d: (d / "0000.mp4").write_bytes(b"x"), preset="fast")
    done = wait_for(manager, job["id"], {"completed", "failed"})
    assert done["status"] == "completed", done.get("error")
    assert done["warnings"] == ["測試警告"]
    assert set(done["stage_started"]) == {"extract_features", "match_sequential"}
    assert "STAGE match_sequential" in manager.log_tail(job["id"])
    public = manager.public(done)
    assert public["stage_list"][0]["label"] == "影片自動切片"
    assert public["preset_label"] == "快速預覽"


def test_cancel_stops_running_step(tmp_path):
    manager = ScriptedJobs(tmp_path / "jobs")
    manager.script = "import time; print('STAGE extract_features', flush=True); time.sleep(60)"
    job = manager.create("photos", ["a.jpg", "b.jpg", "c.jpg"], lambda d: None)
    deadline = time.monotonic() + 20
    while manager.load(job["id"])["stage"] != "extract_features" and time.monotonic() < deadline:
        time.sleep(.05)
    started = time.monotonic()
    manager.cancel(job["id"])
    done = wait_for(manager, job["id"], {"cancelled", "failed", "completed"})
    assert done["status"] == "cancelled"
    assert time.monotonic() - started < 15


def test_failed_step_reports_last_log_line(tmp_path):
    manager = ScriptedJobs(tmp_path / "jobs")
    manager.script = "import sys; print('boom: disk full'); sys.exit(3)"
    job = manager.create("photos", ["a.zip"], lambda d: None)
    done = wait_for(manager, job["id"], {"failed", "completed"})
    assert done["status"] == "failed"


def test_restart_marks_unfinished_jobs_interrupted(tmp_path):
    root = tmp_path / "jobs"
    (root / "20260101-000000-abcdef").mkdir(parents=True)
    (root / "20260101-000000-abcdef" / "job.json").write_text(json.dumps(
        {"id": "20260101-000000-abcdef", "status": "running", "stage": "DensifyPointCloud"}), encoding="utf-8")
    job = SceneJobs(root).load("20260101-000000-abcdef")
    assert job["status"] == "interrupted"
    assert "重新上傳" in job["error"]


def test_invalid_settings_do_not_leave_job_folders(tmp_path):
    manager = ScriptedJobs(tmp_path / "jobs")
    with pytest.raises(JobError, match="影格數"):
        manager.create("video", ["a.mp4"], lambda d: None, frames=500)
    with pytest.raises(JobError, match="結束秒數"):
        manager.create("video", ["a.mp4"], lambda d: None, start=10, end=5)
    assert manager.list() == []


@pytest.fixture
def client(tmp_path):
    from app import app
    manager = ScriptedJobs(tmp_path / "jobs")
    app.config.update(TESTING=True, SCENE_SAMPLE_ROOT=str(tmp_path / "samples"))
    app.extensions["scene_jobs"] = manager
    yield app.test_client(), manager
    app.extensions.pop("scene_jobs", None)


def test_scene_page_renders(client):
    http, _ = client
    page = http.get("/scene")
    assert page.status_code == 200
    assert "空拍 3D 場景" in page.get_data(as_text=True)


def test_upload_larger_than_alignment_limit_is_accepted(client):
    http, manager = client
    video = BytesIO(b"\0" * (33 * 1024 * 1024))
    response = http.post("/api/scenes", data={"files": (video, "flight.mp4"), "preset": "fast"},
                         content_type="multipart/form-data")
    assert response.status_code == 202, response.get_json()
    job = response.get_json()["job"]
    assert job["kind"] == "video"
    stored = list((manager.directory(job["id"]) / "input").iterdir())
    assert [p.name for p in stored] == ["0000.mp4"]
    assert stored[0].stat().st_size == 33 * 1024 * 1024
    wait_for(manager, job["id"], {"completed", "failed"})
    listing = http.get("/api/scenes").get_json()
    assert listing["jobs"][0]["id"] == job["id"]


def test_upload_validation_errors_are_readable(client):
    http, _ = client
    response = http.post("/api/scenes", data={"files": [(BytesIO(b"x"), "a.jpg"), (BytesIO(b"x"), "b.jpg")]},
                         content_type="multipart/form-data")
    assert response.status_code == 400
    assert "三張" in response.get_json()["error"]
    assert http.post("/api/scenes", data={"sample": "seacroft"}).status_code == 400


def test_viewer_files_are_confined_to_job_output(client, tmp_path):
    http, manager = client
    job = manager.create("photos", ["a.zip"], lambda d: None)
    wait_for(manager, job["id"], {"completed", "failed"})
    surface = manager.directory(job["id"]) / "sparse" / "surface"
    surface.mkdir(parents=True)
    (surface / "viewer.js").write_text("export default 1", encoding="utf-8")
    (manager.directory(job["id"]) / "secret.txt").write_text("no", encoding="utf-8")
    ok = http.get(f"/scenes/{job['id']}/view/surface/viewer.js")
    assert ok.status_code == 200 and ok.mimetype == "text/javascript"
    assert http.get(f"/scenes/{job['id']}/view/../secret.txt").status_code == 404
    assert http.get(f"/scenes/{job['id']}/view/..%2Fsecret.txt").status_code == 404
    assert http.get("/scenes/not-a-job/view/surface/viewer.js").status_code == 404
    assert http.get("/api/scenes/..%2F..%2Fapp").status_code == 404
