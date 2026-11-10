# SPDX-License-Identifier: GPL-3.0-only
"""Continue a sparse reconstruction with CPU OpenMVS and texture its surface."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen

import numpy as np

from reconstruct_sparse import ROOT, point_quality_mask, save_json
from surface_quality import compare_surfaces


def sparse_identity(path):
    return {name: hashlib.sha256((path / name).read_bytes()).hexdigest()
            for name in ("cameras.bin", "images.bin", "points3D.bin")}


def reuse_surface_metadata(baseline, output, identity, settings):
    if output.is_relative_to(baseline / "workspace"):
        raise ValueError("Output cannot be inside the reused image workspace")
    report = json.loads((baseline / "surface_report.json").read_text(encoding="utf-8"))
    expected = {key: value for key, value in settings.items() if key != "mesh_refinement"}
    if report.get("status") != "completed" or report.get("sparse_model_sha256") != identity:
        raise ValueError("Reuse requires a completed surface from the same sparse model")
    if report.get("settings") != expected:
        raise ValueError("Reused image, depth and texture settings must match")
    files = ["scene.mvs", "scene_dense.mvs", "scene_dense.ply", "scene_mesh.ply",
             "model.glb", "points.json", "points_surface_repaired.ply"]
    for name in files + ["workspace/sparse/cameras.txt"]:
        if not (baseline / name).is_file():
            raise ValueError(f"Missing baseline artifact: {name}")
    hashes = {name: hashlib.sha256((baseline / name).read_bytes()).hexdigest() for name in files}
    return report, hashes


def embed_model_textures(path):
    import trimesh

    def document():
        with path.open("rb") as stream:
            stream.read(12)
            length, _ = struct.unpack("<II", stream.read(8))
            return json.loads(stream.read(length))

    data = document()
    if any("uri" in image for image in data.get("images", [])):
        scene = trimesh.load_scene(path, process=False)
        embedded = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
        backup = path.with_name("model_openmvs.glb")
        if not backup.exists():
            shutil.copyfile(path, backup)
        temporary = path.with_suffix(".glb.tmp")
        temporary.write_bytes(embedded)
        temporary.replace(path)
        data = document()
    if any("uri" in image for image in data.get("images", [])):
        raise ValueError("GLB still references external textures")
    return {"embedded_images": len(data.get("images", [])), "self_contained": True,
            "file_bytes": path.stat().st_size}


def viewer_assets(destination):
    cache = ROOT / ".cache/three-0.180.0"
    paths = ["build/three.module.js", "build/three.core.js",
             "examples/jsm/controls/OrbitControls.js", "examples/jsm/loaders/GLTFLoader.js",
             "examples/jsm/utils/BufferGeometryUtils.js", "LICENSE"]
    if not all((cache / path).is_file() for path in paths):
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / "three.tgz"
        with urlopen("https://registry.npmjs.org/three/-/three-0.180.0.tgz", timeout=60) as response:
            with archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        with tarfile.open(archive) as package:
            for relative in paths:
                target = cache / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile("package/" + relative) as stream:
                    target.write_bytes(stream.read())
    for relative in paths:
        target = destination / "vendor/three" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache / relative, target)


def project_suspect_points(model, mesh):
    points = list(model.points3D.values())
    xyz = np.array([p.xyz for p in points])
    keep, quality = point_quality_mask(xyz, [p.error for p in points],
                                       [p.track.length() for p in points])
    repaired = xyz.copy()
    if (~keep).any():
        positions, distances, triangles = mesh.nearest.on_surface(xyz[~keep])
        if not np.isfinite(positions).all():
            raise ValueError("Projection produced non-finite coordinates")
        repaired[~keep] = positions
    else:
        distances, triangles = np.array([]), np.array([], dtype=int)
    return xyz, repaired, keep, quality, distances, triangles, points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sparse_run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--openmvs-bin", type=Path,
                        default=ROOT / ".cache/openmvs-2.4.0/vc17/x64/Release")
    parser.add_argument("--max-image-size", type=int, default=1280)
    parser.add_argument("--dense-resolution", type=int, default=640)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--resume", action="store_true", help="Continue the same source and settings from preserved outputs")
    parser.add_argument("--refine", action="store_true", help="Refine the mesh against photographs before texturing")
    parser.add_argument("--reuse-surface", type=Path, help="Seed a new refinement run from a completed baseline surface")
    args = parser.parse_args()
    source, output, binaries = args.sparse_run.resolve(), args.output.resolve(), args.openmvs_bin.resolve()
    if args.reuse_surface and (not args.refine or args.resume):
        parser.error("--reuse-surface requires --refine and a new output; use --resume separately")
    if args.resume and not (output / "surface_report.json").is_file():
        parser.error("Resume requires an existing surface_report.json")
    if output.exists() and any(output.iterdir()) and not args.resume:
        parser.error("Output must be new or empty")
    if min(args.max_image_size, args.dense_resolution, args.threads) < 1:
        parser.error("Resolutions and threads must be positive")
    names = ["InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh"]
    if args.refine:
        names.append("RefineMesh")
    executables = {name: binaries / (name + (".exe" if sys.platform == "win32" else "")) for name in names}
    if not all(path.is_file() for path in executables.values()):
        parser.error("OpenMVS tools not found; see README setup instructions")
    sys.path.insert(0, str(ROOT / ".cache/photogrammetry"))
    import pycolmap
    import trimesh

    sparse_report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    sparse_model = source / "sparse" / str(sparse_report["best_model_id"])
    identity = sparse_identity(sparse_model)
    settings = {"max_image_size": args.max_image_size,
                "dense_resolution": args.dense_resolution, "threads": args.threads,
                "texture_postprocessing": "none"}
    if args.refine:
        settings["mesh_refinement"] = {"resolution_level": 1, "scales": 1,
                                       "max_views": 4, "subdivide": False}
    baseline = args.reuse_surface.resolve() if args.reuse_surface else None
    baseline_report, baseline_hashes = (None, None)
    if baseline:
        baseline_report, baseline_hashes = reuse_surface_metadata(baseline, output, identity, settings)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
              "device": "cpu", "openmvs_version": "2.4.0", "pycolmap_version": pycolmap.__version__,
              "settings": settings, "sparse_source": str(source), "sparse_model_sha256": identity,
              "commands": [], "limitations": ["Uncalibrated scale and world coordinates.",
              "Snapping suspect points to the mesh is visual repair, not improved measurement accuracy."]}
    if args.resume:
        previous = json.loads((output / "surface_report.json").read_text(encoding="utf-8"))
        if previous.get("sparse_model_sha256") != identity or previous.get("settings") != settings:
            parser.error("Resume requires the same sparse model hashes and settings")
        report = previous
        report["status"] = "running"
        report.pop("error", None)
        report["resumed_utc"] = datetime.now(timezone.utc).isoformat()
        started -= report.get("elapsed_seconds", 0)

    def stage(name):
        report["stage"] = name
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save_json(output / "surface_report.json", report)
        print("STAGE " + name, flush=True)

    def run(tool, arguments, expected):
        artifact = output / expected
        seeded = baseline is not None and tool in {"InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh"}
        if (args.resume or seeded) and artifact.is_file() and artifact.stat().st_size:
            print("REUSE " + expected, flush=True)
            return
        stage(tool)
        command = [str(executables[tool]), "--max-threads", str(args.threads), *arguments]
        report["commands"].append(command)
        save_json(output / "surface_report.json", report)
        with (output / (tool + ".console.log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode != 0 or not (output / expected).is_file():
            raise RuntimeError(f"{tool} exited {result.returncode}; inspect its console and timestamped logs")

    try:
        if baseline:
            stage("reuse_baseline")
            shutil.copytree(baseline / "workspace", output / "workspace")
            for name in ("scene.mvs", "scene_dense.mvs", "scene_dense.ply", "scene_mesh.ply"):
                shutil.copyfile(baseline / name, output / name)
            for original, copied in [("model.glb", "baseline_model.glb"),
                                     ("points.json", "baseline_points.json"),
                                     ("points_surface_repaired.ply", "baseline_points_surface_repaired.ply")]:
                shutil.copyfile(baseline / original, output / copied)
            report["baseline"] = {"path": str(baseline), "sha256": baseline_hashes,
                                  "mesh": baseline_report["mesh"],
                                  "point_repair": baseline_report["point_repair"]}
        stage("undistort_images")
        workspace = output / "workspace"
        if not ((args.resume or baseline) and (workspace / "sparse/cameras.txt").is_file()):
            pycolmap.undistort_images(workspace, sparse_model, source / "images",
                                      undistort_options={"max_image_size": args.max_image_size},
                                      num_threads=args.threads)
            undistorted = pycolmap.Reconstruction(workspace / "sparse")
            undistorted.write_text(workspace / "sparse")
        run("InterfaceCOLMAP", ["-i", str(workspace), "-o", "scene.mvs",
                               "--image-folder", str(workspace / "images")], "scene.mvs")
        run("DensifyPointCloud", ["-i", "scene.mvs", "-o", "scene_dense.mvs",
                                 "--max-resolution", str(args.dense_resolution),
                                 "--min-resolution", "320", "--resolution-level", "1",
                                 "--number-views", "4", "--number-views-fuse", "3",
                                 "--iters", "2", "--geometric-iters", "1", "--tower-mode", "0"],
            "scene_dense.mvs")
        run("ReconstructMesh", ["-i", "scene_dense.mvs", "-o", "scene_mesh.mvs",
                                "--target-face-num", "100000", "--close-holes", "0"], "scene_mesh.ply")
        selected_mesh = "scene_mesh.ply"
        if args.refine:
            selected_mesh = "scene_mesh_refined.ply"
            run("RefineMesh", ["-i", "scene_dense.mvs", "-m", "scene_mesh.ply",
                               "-o", "scene_mesh_refined.mvs", "--resolution-level", "1",
                               "--min-resolution", "320", "--max-views", "4", "--scales", "1",
                               "--decimate", "1", "--close-holes", "0", "--max-face-area", "0",
                               "--ensure-edge-size", "0"], selected_mesh)
        run("TextureMesh", ["-i", "scene_dense.mvs", "-m", selected_mesh, "-o", "model.mvs", "--export-type", "glb",
                            "--resolution-level", "0", "--max-texture-size", "4096", "--close-holes", "0",
                            "--global-seam-leveling", "0", "--local-seam-leveling", "0", "--sharpness-weight", "0"],
            "model.glb")
        stage("embed_textures")
        report["texture_package"] = embed_model_textures(output / "model.glb")
        stage("project_suspect_points")
        mesh = trimesh.load_mesh(output / selected_mesh, process=False)
        model = pycolmap.Reconstruction(sparse_model)
        xyz, repaired, keep, quality, distances, triangles, points = project_suspect_points(model, mesh)
        colors = np.array([p.color for p in points])
        trimesh.points.PointCloud(repaired, colors=colors).export(output / "points_surface_repaired.ply")
        save_json(output / "points.json", {"original": np.round(xyz, 6).tolist(),
                  "repaired": np.round(repaired, 6).tolist(), "colors": colors.tolist(),
                  "suspect": (~keep).tolist()})
        repair = {"projected_points": int((~keep).sum()), "unchanged_points": int(keep.sum()),
                  "filter": quality, "distance_units": "uncalibrated reconstruction units",
                  "median_displacement": float(np.median(distances)) if len(distances) else 0,
                  "max_displacement": float(np.max(distances)) if len(distances) else 0,
                  "point_ids": [int(key) for key, good in zip(model.points3D.keys(), keep) if not good],
                  "nearest_triangle_ids": triangles.tolist()}
        save_json(output / "point_repair.json", repair)
        report["mesh"] = {"vertices": len(mesh.vertices), "faces": len(mesh.faces)}
        report["mesh_file"] = selected_mesh
        center = np.median(mesh.vertices, axis=0)
        _, _, basis = np.linalg.svd(mesh.vertices - center, full_matrices=False)
        basis[2] = np.cross(basis[0], basis[1])
        camera_center = np.mean([image.projection_center() for image in model.images.values()
                                 if image.has_pose], axis=0)
        if np.dot(camera_center - center, basis[2]) < 0:
            basis[1:] *= -1
        view_transform = np.eye(4)
        view_transform[:3, :3] = basis
        view_transform[:3, 3] = -basis @ center
        report["view_transform"] = view_transform.reshape(-1).tolist()
        if report.get("baseline"):
            reference = json.loads((Path(report["baseline"]["path"]) / "surface_report.json").read_text(encoding="utf-8"))
            report["view_transform"] = reference["view_transform"]
            current_hashes = {name: hashlib.sha256((Path(report["baseline"]["path"]) / name).read_bytes()).hexdigest()
                              for name in report["baseline"]["sha256"]}
            if current_hashes != report["baseline"]["sha256"]:
                raise RuntimeError("Baseline artifacts changed during refinement")
            report["baseline"]["unchanged"] = True
            stage("compare_surfaces")
            baseline_mesh = trimesh.load_mesh(output / "scene_mesh.ply", process=False)
            comparison = compare_surfaces(baseline_mesh, mesh, xyz[keep])
            save_json(output / "comparison.json", comparison)
            report["comparison"] = comparison
        report["point_repair"] = {k: v for k, v in repair.items() if k not in {"point_ids", "nearest_triangle_ids"}}
        stage("prepare_viewer")
        viewer_assets(output)
        shutil.copyfile(ROOT / "templates/surface_preview.html", output / "preview.html")
        if report.get("baseline"):
            shutil.copyfile(ROOT / "templates/surface_comparison.html", output / "comparison.html")
        report["status"] = "completed"
        stage("finished")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        stage("failed")
        raise


if __name__ == "__main__":
    main()
