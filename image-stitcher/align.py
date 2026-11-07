"""Image registration. Every returned matrix maps B pixels into A pixels."""
from dataclasses import dataclass, field
import time
import cv2
import numpy as np

cv2.setNumThreads(2)
MAX_SIDE = 1600
MAX_CANVAS_PIXELS = 12_000_000


class AlignmentError(ValueError):
    pass


@dataclass
class Candidate:
    model: str
    matrix: np.ndarray
    inliers: np.ndarray
    count: int
    ratio: float
    median_error: float
    coverage: float
    overlap: float
    score: float
    reliable: bool
    rotation: int = 0
    src: np.ndarray = field(default_factory=lambda: np.empty((0, 2)), repr=False)
    dst: np.ndarray = field(default_factory=lambda: np.empty((0, 2)), repr=False)

    def summary(self):
        return {**{k: (v.tolist() if isinstance(v, np.ndarray) else v)
                   for k, v in vars(self).items() if k not in ('inliers', 'src', 'dst')},
                'src_count': len(self.src)}


def sift_matches(a, b):
    sift = cv2.SIFT_create(nfeatures=6000)
    clahe = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8))
    def features(im):
        return sift.detectAndCompute(clahe.apply(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)), None)
    ka, da = features(a)
    kb, db = features(b)
    if da is None or db is None or min(len(da), len(db)) < 2:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    matcher = cv2.BFMatcher()
    forward = matcher.knnMatch(db, da, k=2)
    reverse = matcher.knnMatch(da, db, k=2)
    back = {m.queryIdx: m.trainIdx for m, n in reverse if m.distance < .75 * n.distance}
    pairs = [m for m, n in forward if m.distance < .75 * n.distance and back.get(m.trainIdx) == m.queryIdx]
    return (np.float32([kb[m.queryIdx].pt for m in pairs]).reshape(-1, 2),
            np.float32([ka[m.trainIdx].pt for m in pairs]).reshape(-1, 2))


def corners(shape):
    h, w = shape[:2]
    return np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])


def project(points, matrix):
    return cv2.perspectiveTransform(np.float32(points).reshape(1, -1, 2), matrix)[0]


def hull_fraction(points, shape):
    return float(cv2.contourArea(cv2.convexHull(np.float32(points)))) / (shape[0] * shape[1])


def evaluate(model, matrix, src, dst, shape_a, shape_b):
    if matrix is None or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
        return None
    boundary = corners(shape_b)
    z = np.c_[boundary, np.ones(4)] @ matrix[2]
    if not (np.all(z > 1e-6) or np.all(z < -1e-6)):
        return None  # Projective horizon crosses the image.
    poly = project(boundary, matrix)
    if not np.isfinite(poly).all() or not cv2.isContourConvex(poly):
        return None
    area = cv2.contourArea(poly)
    if area < 64 or area > shape_a[0] * shape_a[1] * 100:
        return None
    # Reject reflections: image coordinates on both sides have the same winding.
    if cv2.contourArea(poly, oriented=True) <= 0:
        return None
    error = np.linalg.norm(project(src, matrix) - dst, axis=1)
    inside = error < 4.0
    count = int(inside.sum())
    if count < 4:
        return None
    overlap_area, _ = cv2.intersectConvexConvex(poly, corners(shape_a))
    overlap = float(overlap_area / max(1, min(area, cv2.contourArea(corners(shape_a)))))
    if overlap <= .005:
        return None
    coverage = min(hull_fraction(src[inside], shape_b), hull_fraction(dst[inside], shape_a))
    median = float(np.median(error[inside]))
    ratio = count / len(src)
    # Heuristic ordering, not a probability; mild complexity penalty rewards
    # simpler models when they explain essentially the same correspondences.
    penalty = {'similarity': 0, 'affine': .6, 'homography': 1.2}[model]
    score = float(8 * np.log1p(count) + 12 * ratio + 18 * np.sqrt(coverage) - 3 * median - penalty)
    reliable = bool(count >= 12 and ratio >= .25 and coverage >= .015 and median <= 3)
    return Candidate(model, matrix, inside, count, ratio, median, coverage, overlap, score, reliable, src=src, dst=dst)


def estimate(src, dst, shape_a, shape_b):
    if len(src) < 6:
        return []
    candidates = []
    for name, fn in [('similarity', cv2.estimateAffinePartial2D), ('affine', cv2.estimateAffine2D)]:
        mat, _ = fn(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4,
                    maxIters=5000, confidence=.999, refineIters=15)
        if mat is not None:
            candidate = evaluate(name, np.vstack([mat, [0, 0, 1]]), src, dst, shape_a, shape_b)
            if candidate:
                candidates.append(candidate)
    mat, _ = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 4, maxIters=10000, confidence=.999)
    candidate = evaluate('homography', mat, src, dst, shape_a, shape_b)
    if candidate:
        candidates.append(candidate)
    return candidates


def rotation_view(image, k):
    """Return CCW quarter-turn image and its original->rotated coordinate map."""
    h, w = image.shape[:2]
    matrices = [np.eye(3), np.array([[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]]),
                np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]]),
                np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]])]
    return np.ascontiguousarray(np.rot90(image, k)), matrices[k]


def align(a, b, method='sift', rotation_search=False):
    if method not in ('sift', 'lightglue'):
        raise AlignmentError('未知的特徵方法。')
    start = time.perf_counter()
    match = sift_matches
    if method == 'lightglue':
        from learned import match
    candidates = []
    match_count = 0
    # SIFT already estimates local orientation; quarter-turn search is useful
    # for SuperPoint, which is not inherently rotation invariant.
    for k in (range(4) if rotation_search else [0]):
        rotated, transform = rotation_view(b, k)
        src, dst = match(a, rotated)
        src = project(src, np.linalg.inv(transform)) if len(src) else src
        match_count = max(match_count, len(src))
        for c in estimate(src, dst, a.shape, b.shape):
            c.rotation = k * 90
            candidates.append(c)
    candidates.sort(key=lambda c: (c.reliable, c.score), reverse=True)
    return candidates, {'method': method, 'matches': match_count,
                        'seconds': round(time.perf_counter() - start, 3)}


def render(a, b, candidate):
    """Bounded union canvas with explicit validity masks (black pixels are valid)."""
    poly = project(corners(b.shape), candidate.matrix)
    all_corners = np.vstack([corners(a.shape), poly])
    low = np.floor(all_corners.min(0)).astype(int)
    high = np.ceil(all_corners.max(0)).astype(int) + 1
    width, height = map(int, high - low)
    if width <= 0 or height <= 0 or width * height > MAX_CANVAS_PIXELS or max(width, height) > 8000:
        raise AlignmentError('候選變換的畫布過大；保留指標，但不產生此候選的預覽。')
    shift = np.array([[1., 0, -low[0]], [0, 1., -low[1]], [0, 0, 1.]])
    size = (width, height)
    aa = cv2.warpPerspective(a, shift, size)
    bb = cv2.warpPerspective(b, shift @ candidate.matrix, size)
    ma = cv2.warpPerspective(np.ones(a.shape[:2], np.uint8) * 255, shift, size, flags=cv2.INTER_NEAREST)
    mb = cv2.warpPerspective(np.ones(b.shape[:2], np.uint8) * 255, shift @ candidate.matrix, size, flags=cv2.INTER_NEAREST)
    both = (ma > 0) & (mb > 0)
    # Modest per-channel exposure correction for the preview only.
    corrected = bb.copy()
    if both.sum() > 100:
        delta = np.clip(np.median(aa[both].astype(float) - bb[both], axis=0), -40, 40)
        corrected = np.clip(bb.astype(float) + delta, 0, 255).astype(np.uint8)
    da = cv2.distanceTransform(np.pad(ma, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    db = cv2.distanceTransform(np.pad(mb, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    alpha = da / np.maximum(da + db, 1e-6)
    blend = (aa * alpha[..., None] + corrected * (1 - alpha[..., None])).astype(np.uint8)
    blend[(ma == 0) & (mb == 0)] = 0
    outline = a.copy()
    cv2.polylines(outline, [np.round(poly).astype(np.int32)], True, (60, 210, 255), 3, cv2.LINE_AA)
    # A on left, B on right; show only geometrically verified pairs.
    pairs = np.flatnonzero(candidate.inliers)
    pairs = pairs[np.linspace(0, len(pairs) - 1, min(60, len(pairs))).astype(int)]
    view = np.zeros((max(a.shape[0], b.shape[0]), a.shape[1] + b.shape[1], 3), np.uint8)
    view[:a.shape[0], :a.shape[1]] = a
    view[:b.shape[0], a.shape[1]:] = b
    for i in pairs:
        pa = tuple(np.round(candidate.dst[i]).astype(int))
        pb = tuple(np.round(candidate.src[i] + [a.shape[1], 0]).astype(int))
        cv2.line(view, pa, pb, (80, 230, 120), 1, cv2.LINE_AA)
    warped = cv2.cvtColor(bb, cv2.COLOR_BGR2BGRA)
    warped[:, :, 3] = mb
    return {'base': aa, 'warped': warped, 'blend': blend, 'region': outline, 'matches': view}
