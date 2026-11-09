from types import SimpleNamespace

import numpy as np
import pytest

from reconstruct_surface import project_suspect_points


def test_projection_uses_nearest_surface_and_preserves_other_points():
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("rtree")
    pytest.importorskip("scipy")
    vertices = [[-2, -2, 0], [2, -2, 0], [0, 2, 0],
                [-2, -2, 5], [2, -2, 5], [0, 2, 5]]
    mesh = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [3, 4, 5]], process=False)
    weak = SimpleNamespace(length=lambda: 2)
    strong = SimpleNamespace(length=lambda: 5)
    model = SimpleNamespace(points3D={
        1: SimpleNamespace(xyz=np.array([0., 0., .5]), error=.1, track=weak),
        2: SimpleNamespace(xyz=np.array([0., 0., 4.5]), error=.1, track=weak),
        3: SimpleNamespace(xyz=np.array([0., 0., 8.]), error=.1, track=strong),
    })
    original, repaired, keep, _, distances, triangles, _ = project_suspect_points(model, mesh)
    np.testing.assert_allclose(repaired, [[0, 0, 0], [0, 0, 5], [0, 0, 8]])
    np.testing.assert_allclose(original, [[0, 0, .5], [0, 0, 4.5], [0, 0, 8]])
    np.testing.assert_allclose(distances, [.5, .5])
    assert keep.tolist() == [False, False, True]
    assert triangles.tolist() == [0, 1]
