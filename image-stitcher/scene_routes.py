# SPDX-License-Identifier: GPL-3.0-only
"""Web pages and API for turning drone video or photographs into a 3D scene."""

import hashlib
import json
import os
from pathlib import Path
import shutil

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_from_directory, url_for

from download_scene_samples import ROOT as SAMPLE_ROOT, SAMPLES
from scene_jobs import PRESETS, JobError, SceneJobs, classify

scenes = Blueprint("scenes", __name__)
MAX_UPLOAD = 4 * 1024**3
VIEWER_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
                ".glb": "model/gltf-binary", ".html": "text/html", ".ply": "application/octet-stream",
                ".png": "image/png", ".jpg": "image/jpeg"}


def jobs():
    manager = current_app.extensions.get("scene_jobs")
    if manager is None:
        manager = current_app.extensions["scene_jobs"] = SceneJobs(current_app.config.get("SCENE_JOB_ROOT")
                                                                   or Path(current_app.root_path) / "outputs" / "scenes")
    return manager


def sample_catalog():
    sources_path = Path(current_app.config.get("SCENE_SAMPLE_ROOT") or SAMPLE_ROOT) / "sources.json"
    try:
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    catalog = []
    for key, spec in SAMPLES.items():
        source = sources.get(key)
        available = bool(source) and (sources_path.parent / source["file"]).is_file()
        catalog.append({"id": key, "label": spec["label"], "description": spec["description"],
                        "frames": spec["frames"], "available": available,
                        "license": source.get("license") if source else None,
                        "author": source.get("author") if source else None,
                        "page": source.get("page") if source else None})
    return catalog


def asset(name):
    contents = (Path(current_app.static_folder) / name).read_bytes()
    return url_for("static", filename=name, v=hashlib.sha256(contents).hexdigest()[:12])


@scenes.get("/scene")
def scene_page():
    response = current_app.make_response(render_template(
        "scene.html", presets=PRESETS, samples=sample_catalog(), stylesheet=asset("style.css"),
        scene_stylesheet=asset("scene.css"), javascript=asset("scene.js")))
    response.headers["Cache-Control"] = "no-store"
    return response


def number(name, kind=float):
    value = request.form.get(name, "").strip()
    if not value:
        return None
    try:
        return kind(value)
    except ValueError:
        raise JobError(f"{name} 必須是數字。") from None


@scenes.post("/api/scenes")
def create_scene():
    request.max_content_length = MAX_UPLOAD
    try:
        preset = request.form.get("preset", "standard")
        options = dict(preset=preset, frames=number("frames", int), start=number("start"), end=number("end"))
        sample = request.form.get("sample")
        if sample:
            spec = next((s for s in sample_catalog() if s["id"] == sample and s["available"]), None)
            if spec is None:
                raise JobError("範例影片尚未下載；請執行 download_scene_samples.py。")
            source = Path(current_app.config.get("SCENE_SAMPLE_ROOT") or SAMPLE_ROOT) / f"{sample}.webm"

            def write_sample(directory):
                target = directory / source.name
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)

            job = jobs().create("video", [source.name], write_sample, label=spec["label"],
                                **{**options, "frames": options["frames"] or spec["frames"]})
        else:
            uploads = [f for f in request.files.getlist("files") if f and f.filename]
            names = [f.filename for f in uploads]
            kind = classify(names)

            def write_uploads(directory):
                for index, upload in enumerate(uploads):
                    upload.save(directory / f"{index:04d}{Path(upload.filename).suffix.lower()}")

            job = jobs().create(kind, names, write_uploads, **options)
    except JobError as error:
        return jsonify(error=str(error)), 400
    return jsonify(job=jobs().public(job)), 202


@scenes.get("/api/scenes")
def list_scenes():
    manager = jobs()
    return jsonify(jobs=[manager.public(job) for job in manager.list()], samples=sample_catalog())


@scenes.get("/api/scenes/<job_id>")
def scene_status(job_id):
    manager = jobs()
    job = manager.load(job_id)
    if job is None:
        return jsonify(error="找不到這個工作。"), 404
    return jsonify(job=manager.public(job), log=manager.log_tail(job_id))


@scenes.post("/api/scenes/<job_id>/cancel")
def cancel_scene(job_id):
    job = jobs().cancel(job_id)
    if job is None:
        return jsonify(error="找不到這個工作。"), 404
    return jsonify(job=jobs().public(job))


@scenes.delete("/api/scenes/<job_id>")
def delete_scene(job_id):
    try:
        if not jobs().delete(job_id):
            return jsonify(error="找不到這個工作。"), 404
    except JobError as error:
        return jsonify(error=str(error)), 409
    return jsonify(deleted=job_id)


@scenes.get("/scenes/<job_id>/view/<path:filename>")
def scene_file(job_id, filename):
    manager = jobs()
    if manager.load(job_id) is None:
        abort(404)
    directory = manager.directory(job_id) / "sparse"
    response = send_from_directory(directory, filename, max_age=0,
                                   mimetype=VIEWER_TYPES.get(Path(filename).suffix.lower()))
    response.headers["Cache-Control"] = "no-cache"
    return response


@scenes.errorhandler(413)
def scene_too_large(_):
    return jsonify(error="上傳檔案超過 4 GiB。"), 413
