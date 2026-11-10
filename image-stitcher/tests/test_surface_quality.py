import json

import numpy as np
import pytest

from reconstruct_surface import reuse_surface_metadata
from surface_quality import compare_surfaces


def baseline_fixture(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    identity = {"cameras.bin": "camera-hash", "images.bin": "image-hash", "points3D.bin": "point-hash"}
    settings = {"max_image_size": 1280, "dense_resolution": 640, "threads": 2,
                "texture_postprocessing": "none"}
    report = {"status": "completed", "sparse_model_sha256": identity, "settings": settings}
    (baseline / "surface_report.json").write_text(json.dumps(report))
    for name in ["scene.mvs", "scene_dense.mvs", "scene_dense.ply", "scene_mesh.ply",
                 "model.glb", "points.json", "points_surface_repaired.ply", "workspace/sparse/cameras.txt"]:
        target = baseline / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(name.encode())
    return baseline, identity, settings


def test_reuse_accepts_photo_only_baseline_without_survey_data(tmp_path):
    baseline, identity, settings = baseline_fixture(tmp_path)
    report, hashes = reuse_surface_metadata(baseline, tmp_path / "refined", identity,
                                           {**settings, "mesh_refinement": {"scales": 1}})
    assert report["status"] == "completed"
    assert len(hashes) == 7
    assert not (tmp_path / "refined").exists()


@pytest.mark.parametrize("changed", ["model", "resolution", "status"])
def test_reuse_rejects_incompatible_baselines(tmp_path, changed):
    baseline, identity, settings = baseline_fixture(tmp_path)
    if changed == "model":
        identity = {**identity, "points3D.bin": "other"}
    elif changed == "resolution":
        settings = {**settings, "dense_resolution": 1280}
    else:
        path = baseline / "surface_report.json"
        report = json.loads(path.read_text())
        report["status"] = "failed"
        path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        reuse_surface_metadata(baseline, tmp_path / "refined", identity, settings)


def test_reuse_prevents_recursive_workspace_copy(tmp_path):
    baseline, identity, settings = baseline_fixture(tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        reuse_surface_metadata(baseline, baseline / "workspace/refined", identity, settings)


def test_comparison_uses_same_original_landmarks_and_does_not_change_geometry():
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("rtree")
    pytest.importorskip("scipy")
    vertices = np.array([[-2., -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]])
    baseline = trimesh.Trimesh(vertices=vertices + [0, 0, 1], faces=[[0, 1, 2], [0, 2, 3]], process=False)
    refined = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False)
    landmarks = np.array([[0., 0, 0], [.5, .5, 0], [-.5, .5, 0]])
    result = compare_surfaces(baseline, refined, landmarks)
    assert result["baseline"]["landmark_distance_median"] == pytest.approx(1)
    assert result["refined"]["landmark_distance_median"] == pytest.approx(0)
    assert result["refined"]["boundary_edges"] == 4
    assert result["refined"]["nonmanifold_edges"] == 0
    np.testing.assert_array_equal(refined.vertices, vertices)
    np.testing.assert_array_equal(landmarks, [[0, 0, 0], [.5, .5, 0], [-.5, .5, 0]])
