# SPDX-License-Identifier: GPL-3.0-only
"""Relative surface diagnostics; these do not establish survey accuracy."""

import numpy as np


def surface_metrics(mesh, landmarks, normalization):
    if not len(mesh.vertices) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError("Surface must contain finite vertices and nonempty triangles")
    counts = np.bincount(mesh.edges_unique_inverse)
    _, distances, _ = mesh.nearest.on_surface(landmarks)
    if not np.isfinite(distances).all():
        raise ValueError("Surface distance calculation produced non-finite values")
    return {
        "vertices": len(mesh.vertices), "faces": len(mesh.faces),
        "boundary_edges": int((counts == 1).sum()),
        "nonmanifold_edges": int((counts > 2).sum()),
        "degenerate_faces": int((mesh.area_faces <= normalization**2 * 1e-12).sum()),
        "landmark_distance_median": float(np.median(distances)),
        "landmark_distance_p90": float(np.percentile(distances, 90)),
        "landmark_distance_median_percent_of_baseline_diagonal": float(np.median(distances) / normalization * 100),
    }


def compare_surfaces(baseline, refined, landmarks, sample_size=512):
    if sample_size < 1:
        raise ValueError("Landmark sample size must be positive")
    landmarks = np.asarray(landmarks, dtype=np.float64)
    if not len(landmarks) or not np.isfinite(landmarks).all():
        raise ValueError("Comparison needs finite original landmarks")
    normalization = float(np.linalg.norm(np.ptp(baseline.vertices, axis=0)))
    if not np.isfinite(normalization) or normalization <= 0:
        raise ValueError("Baseline surface has no spatial extent")
    indices = np.random.default_rng(0).choice(len(landmarks), min(sample_size, len(landmarks)), replace=False)
    sampled = landmarks[indices]
    return {
        "landmark_sample_count": len(sampled), "sample_seed": 0,
        "baseline_diagonal": normalization, "coordinate_units": "uncalibrated reconstruction units",
        "baseline": surface_metrics(baseline, sampled, normalization),
        "refined": surface_metrics(refined, sampled, normalization),
        "limitations": ["Uses the same original, unsnapped sparse landmarks for both surfaces.",
                        "Distance to sparse landmarks measures agreement, not ground-truth accuracy.",
                        "Boundary edges can be valid at the scene boundary; counts are diagnostics, not scores."],
    }
